#!/usr/bin/env python3
"""CEO Decision Notifier v2 (Phase E closeout, 2026-10-22, dual-advisor approved).

Extends v1 (single delivery point: CEO decision packages -> contracts/ queue
+ /hooks/agent trigger; no new queue) with the approved gate set:

  A. Deterministic escalation: high-risk action types / keywords in tasks,
     or a non-false escalate_to_human, force requires_approval regardless of
     what the package claims. Parse failure of escalate_to_human = escalate.
  B. Consultation fields (Doc-2) in SHADOW mode: Notifier computes its own
     degraded flag (package-supplied values are ignored for gating), records
     reasons, never blocks dispatch in shadow mode.
  C. Decomposition lifecycle: after triggering main, wait for the outbox
     submission (contracts-outbox/<decision_id>/submission.json), validate
     every contract (schema v1.2 incl. structured actions, decision_id
     linkage, NC-id coverage vs the decision, action-subset check against
     the decision's approved side_effects), snapshot the validated bytes
     into the Verifier area (contracts/.snapshots), re-hash at promotion
     time (TOCTOU: any mismatch -> quarantine + freeze + alarm), then
     promote snapshot copies into contracts/pending/<worker>/.
  D. Approvals: host-side only (--approve/--revoke), bound to sha256 of the
     decision file and of the contract set; content change voids approval.
  E. Dispatcher spec integrity: before triggering, sha256 of the gateway
     workspace AGENTS.md + DISPATCHER_PROMPT.md must match the host-side
     record (state/.dispatcher-spec.sha256); mismatch -> refuse to trigger.

Usage:
  decision_notifier.py <package.json> | --stdin
  decision_notifier.py --resume <decision_id>          (legacy, non-forced)
  decision_notifier.py --approve <decision_id> --by <name>
  decision_notifier.py --revoke  <decision_id> --by <name>

Exit codes: 0 ok (incl. pending_approval / promoted), 3 invalid package,
4 duplicate / not found, 5 write failure, 6 trigger failure,
7 frozen (no submission after retries), 8 validation/TOCTOU violation,
10 spec hash mismatch.
Audit: /home/ubuntu/ai-team/logs/notifier-audit.jsonl (JSON lines).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

BASE = "/home/ubuntu/ai-team"
CONTRACTS = os.path.join(BASE, "contracts")
PENDING = os.environ.get("NOTIFIER_PENDING_DIR", os.path.join(CONTRACTS, "pending"))
CLAIMED = os.path.join(CONTRACTS, "claimed")
DONE = os.path.join(CONTRACTS, "done")
QUARANTINE = os.path.join(CONTRACTS, "quarantine")
OUTBOX = os.environ.get("NOTIFIER_OUTBOX_DIR", os.path.join(BASE, "contracts-outbox"))
SNAPSHOTS = os.path.join(CONTRACTS, ".snapshots")
APPROVALS = os.path.join(CONTRACTS, "approvals")
AUDIT = os.path.join(BASE, "logs", "notifier-audit.jsonl")
TOKEN_FILES = [
    os.path.join(BASE, "state", ".hooks-token"),
    os.path.join(BASE, ".hooks-token"),
]
SPEC_FILES = [
    "/opt/openclaw-official/.openclaw/workspace/AGENTS.md",
    "/opt/openclaw-official/.openclaw/workspace/DISPATCHER_PROMPT.md",
]
SPEC_RECORD = os.path.join(BASE, "state", ".dispatcher-spec.sha256")
GATEWAY = "openclaw-official-openclaw-gateway-1"
NC_FIELDS = ("id", "source_text", "rule", "acceptance_check")
WAIT_S = int(os.environ.get("NOTIFIER_WAIT_S", "420"))
MAX_TRIGGER_ATTEMPTS = 3

HIGH_RISK_TYPES = {
    "payment", "purchase", "order", "external_send", "publish",
    "infra_change", "delete", "irreversible", "account_change",
}
SAFE_TYPES = {
    "read", "search", "llm_generate", "llm_extract", "file_write",
    "file_read", "analysis", "report",
}
RISK_KEYWORDS = [
    "付款", "支付", "下单", "购买", "充值", "对外发送", "发送邮件", "群发",
    "公开发布", "发布到", "删除文件", "删除数据", "清空", "改配置", "修改配置",
    "部署上线", "重启服务", "payment", "purchase", "checkout", "publish",
    "delete", "drop table", "restart service",
]


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def audit(decision_id, action, detail=None):
    rec = {"ts": now_iso(), "decision_id": decision_id, "action": action}
    if detail:
        rec["detail"] = detail
    try:
        os.makedirs(os.path.dirname(AUDIT), exist_ok=True)
        with open(AUDIT, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"WARN audit write failed: {e}", file=sys.stderr)
    print(json.dumps(rec, ensure_ascii=False))


# ---------------------------------------------------------------- validation
def validate(pkg):
    """Structural validation (v1 rules + v2 additions). Returns problems."""
    problems = []
    if not isinstance(pkg, dict):
        return ["package is not a JSON object"]
    did = pkg.get("decision_id")
    if not did or not isinstance(did, str):
        problems.append("missing/invalid decision_id")
    nc = pkg.get("negative_constraints")
    if not isinstance(nc, list) or not nc:
        problems.append("negative_constraints missing or empty")
    else:
        for i, item in enumerate(nc):
            if not isinstance(item, dict):
                problems.append(f"negative_constraints[{i}] not an object")
                continue
            for f in NC_FIELDS:
                if not item.get(f):
                    problems.append(f"negative_constraints[{i}] missing {f}")
    tasks = pkg.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        problems.append("tasks missing or empty")
    se = pkg.get("side_effects")
    if se is not None:
        if not isinstance(se, list) or not all(isinstance(x, str) for x in se):
            problems.append("side_effects must be a list of action-type strings")
    return problems


def collect_task_strings(tasks):
    out = []

    def walk(x):
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, dict):
            for k, v in x.items():
                if k in ("objective", "title", "description", "detail", "instruction"):
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    for t in tasks or []:
        walk(t)
    return out


def escalation_check(pkg):
    """Deterministic escalation. Returns (forced: bool, reasons: list)."""
    reasons = []
    if pkg.get("requires_approval") is True:
        reasons.append("package requires_approval=true")
    esc = pkg.get("escalate_to_human", False)
    if esc is not False:
        # anything but an explicit JSON false escalates (incl. parse oddities)
        reasons.append(f"escalate_to_human is not explicit false ({esc!r})")
    declared = set(pkg.get("side_effects") or [])
    hit = declared & HIGH_RISK_TYPES
    if hit:
        reasons.append(f"declared side_effects hit high-risk types: {sorted(hit)}")
    blob = "\n".join(collect_task_strings(pkg.get("tasks"))).lower()
    kw = [k for k in RISK_KEYWORDS if k.lower() in blob]
    if kw:
        reasons.append(f"task text hit risk keywords: {kw[:5]}")
    return (len(reasons) > 0), reasons


def consultation_shadow(pkg):
    """Doc-2 consultation fields, shadow mode. Notifier-owned degraded."""
    reasons = []
    cr = pkg.get("consultation_routing")
    if cr is None:
        reasons.append("consultation_routing missing")
    elif not isinstance(cr, dict):
        reasons.append("consultation_routing not an object")
    else:
        for k in ("triggered_conditions", "mode", "primary_model", "reviewer_model"):
            if k not in cr:
                reasons.append(f"consultation_routing missing {k}")
    rtf = pkg.get("red_team_findings")
    if isinstance(rtf, list) and len(rtf) == 0:
        if not pkg.get("red_team_round_id") and not pkg.get("divergence_draft_sha"):
            reasons.append("red_team_findings empty without round id / draft sha")
    if pkg.get("consultation_divergence") is not None and not pkg.get("divergence_rationale"):
        reasons.append("consultation_divergence present without rationale")
    return {
        "mode": "shadow",
        "notifier_degraded": len(reasons) > 0,
        "notifier_degraded_reasons": reasons,
        "ceo_confidence_recorded": pkg.get("ceo_confidence"),
    }


def decision_exists(decision_id):
    name = f"decision-{decision_id}.json"
    if os.path.isfile(os.path.join(PENDING, name)):
        return "pending"
    if os.path.isfile(os.path.join(DONE, name)):
        return "done"
    if os.path.isdir(CLAIMED):
        for sub in os.listdir(CLAIMED):
            if os.path.isfile(os.path.join(CLAIMED, sub, name)):
                return f"claimed/{sub}"
    return None


# ------------------------------------------------------------------- trigger
def read_token():
    for tf in TOKEN_FILES:
        if os.path.exists(tf):
            with open(tf) as f:
                return f.read().strip()
    return None


def spec_hash_ok():
    """Dispatcher spec integrity check (fail-closed when a record exists)."""
    if not os.path.exists(SPEC_RECORD):
        return True, "no spec record yet"
    try:
        recorded = {}
        with open(SPEC_RECORD) as f:
            for line in f:
                parts = line.split()
                if len(parts) == 2:
                    recorded[parts[1]] = parts[0]
        # workspace files are opc-owned under a 700 dir: hash via sudo
        out = subprocess.run(
            ["sudo", "-n", "sha256sum"] + SPEC_FILES,
            capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            return False, f"sha256sum failed: {out.stderr.strip()[:200]}"
        current = {}
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2:
                current[parts[1]] = parts[0]
        for path in SPEC_FILES:
            if recorded.get(path) != current.get(path):
                return False, f"hash mismatch: {path}"
        return True, "ok"
    except Exception as e:
        return False, f"spec check error: {e}"


def trigger(decision_id, attempt=1):
    """POST /hooks/agent via docker exec into the gateway container."""
    token = read_token()
    if not token:
        return None, "hooks token file not found"
    body = json.dumps({
        "message": f"new_decision decision_id={decision_id}",
        "decision_id": decision_id,
        "agentId": "main",
        "sessionKey": "agent:main:dispatcher",
        "name": "ceo-decision-notifier",
        "idempotencyKey": f"{decision_id}:{attempt}",
        "deliver": False,
    })
    cmd = [
        "docker", "exec", GATEWAY, "curl", "-s",
        "-w", "\n%{http_code}",
        "-X", "POST", "http://127.0.0.1:18789/hooks/agent",
        "-H", "Content-Type: application/json",
        "-H", f"Authorization: Bearer {token}",
        "-d", body,
        "--max-time", "30",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as e:
        return None, f"trigger exec failed: {e}"
    lines = (out.stdout or "").strip().splitlines()
    if not lines:
        return None, f"empty trigger response (stderr: {out.stderr.strip()[:200]})"
    code = lines[-1]
    payload = "\n".join(lines[:-1])
    if code != "200":
        return None, f"HTTP {code}: {payload[:300]}"
    try:
        return json.loads(payload), None
    except Exception:
        return {"raw": payload}, None


# ----------------------------------------------------------------- lifecycle
def load_validator():
    sys.path.insert(0, os.path.join(BASE, "scripts"))
    import validate_contract
    return validate_contract


def wait_for_submission(did):
    """Poll the outbox for submission.json. Returns (manifest, error)."""
    outdir = os.path.join(OUTBOX, did)
    deadline = time.time() + WAIT_S
    while time.time() < deadline:
        if os.path.isfile(os.path.join(outdir, "FAILED")):
            return None, "dispatcher reported FAILED"
        sp = os.path.join(outdir, "submission.json")
        if os.path.isfile(sp):
            try:
                with open(sp) as f:
                    return json.load(f), None
            except Exception as e:
                return None, f"submission.json unreadable: {e}"
        time.sleep(5)
    return None, f"no submission within {WAIT_S}s"


def validate_submission(did, pkg, manifest):
    """Validate decomposed contracts. Returns (contracts, problems).

    contracts: list of dicts with keys: name, data (parsed), raw (bytes),
    sha (of raw), worker.
    """
    problems = []
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        return [], ["submission manifest has no files"]
    if manifest.get("decision_id") != did:
        problems.append("manifest decision_id mismatch")
    vc = load_validator()
    outdir = os.path.join(OUTBOX, did)
    decision_nc_ids = {
        item.get("id") for item in pkg.get("negative_constraints") or []
        if isinstance(item, dict)
    }
    approved_types = set(pkg.get("side_effects") or []) | SAFE_TYPES
    covered_nc = set()
    contracts = []
    used_types = set()
    for name in files:
        if not isinstance(name, str) or "/" in name or name.startswith("."):
            problems.append(f"bad file name in manifest: {name!r}")
            continue
        path = os.path.join(outdir, name)
        if not os.path.isfile(path) or os.path.islink(path):
            problems.append(f"submitted file missing or symlink: {name}")
            continue
        with open(path, "rb") as f:
            raw = f.read()
        try:
            data = json.loads(raw)
        except Exception as e:
            problems.append(f"{name}: invalid JSON ({e})")
            continue
        for e in vc.check(data):
            problems.append(f"{name}: {e}")
        if data.get("decision_id") != did:
            problems.append(f"{name}: decision_id linkage missing/mismatch")
        for item in data.get("negative_constraints") or []:
            if isinstance(item, dict) and item.get("id"):
                covered_nc.add(item["id"])
        actions = data.get("actions")
        if not isinstance(actions, list) or not actions:
            problems.append(f"{name}: actions missing or empty")
        else:
            for i, a in enumerate(actions):
                if not isinstance(a, dict) or not all(
                    isinstance(a.get(k), str) and a.get(k)
                    for k in ("tool", "target", "type")
                ):
                    problems.append(f"{name}: actions[{i}] needs tool/target/type strings")
                else:
                    used_types.add(a["type"])
        worker = (
            data.get("executor_preference")
            if data.get("executor_preference") in ("guangtou", "noesis")
            else (data.get("preferred_worker") or "muse")
        )
        contracts.append({
            "name": name, "data": data, "raw": raw,
            "sha": sha256_bytes(raw), "worker": worker,
        })
    missing_nc = decision_nc_ids - covered_nc
    if missing_nc:
        problems.append(f"decision NC not covered by contracts: {sorted(missing_nc)}")
    subset_bad = used_types - approved_types
    if subset_bad:
        problems.append(f"contract action types exceed approved set: {sorted(subset_bad)}")
    risky = used_types & HIGH_RISK_TYPES
    if risky and not approval_record(did):
        problems.append(f"high-risk action types without approval: {sorted(risky)}")
    return contracts, problems


def snapshot_contracts(did, contracts):
    """Copy validated bytes into the Verifier snapshot area (same-buffer)."""
    sdir = os.path.join(SNAPSHOTS, did)
    os.makedirs(sdir, exist_ok=True)
    meta = {"decision_id": did, "snapshotted_at": now_iso(), "files": {}}
    for c in contracts:
        dest = os.path.join(sdir, c["name"])
        with open(dest, "wb") as f:
            f.write(c["raw"])  # bytes hashed earlier from the same read
        meta["files"][c["name"]] = c["sha"]
    with open(os.path.join(sdir, "snapshot.json"), "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


def promote(did, contracts):
    """Re-hash outbox files (TOCTOU), then promote snapshot copies."""
    outdir = os.path.join(OUTBOX, did)
    for c in contracts:
        path = os.path.join(outdir, c["name"])
        try:
            with open(path, "rb") as f:
                current = sha256_bytes(f.read())
        except Exception as e:
            return f"promote re-read failed for {c['name']}: {e}"
        if current != c["sha"]:
            return f"TOCTOU sha mismatch for {c['name']}"
    promoted = []
    for c in contracts:
        wdir = os.path.join(PENDING, c["worker"])
        os.makedirs(wdir, exist_ok=True)
        src = os.path.join(SNAPSHOTS, did, c["name"])
        # Fleet convention (scan_inbox resolves contracts by <task_id>.json);
        # the outbox manifest name (task-1.json) is NOT a valid queue name.
        dest_name = str(c["data"].get("task_id") or c["name"])
        if not dest_name.endswith(".json"):
            dest_name += ".json"
        dest = os.path.join(wdir, dest_name)
        fd, tmp = tempfile.mkstemp(dir=wdir, suffix=".tmp")
        with open(src, "rb") as sf, os.fdopen(fd, "wb") as tf:
            shutil.copyfileobj(sf, tf)
        os.chmod(tmp, 0o664)  # mkstemp defaults to 600; workers (ingest) must be able to read
        os.rename(tmp, dest)
        promoted.append(dest)
    return promoted


def freeze_worker(worker, did, reason):
    os.makedirs(QUARANTINE, exist_ok=True)
    rec = {"worker": worker, "decision_id": did, "reason": reason, "ts": now_iso()}
    path = os.path.join(QUARANTINE, "frozen-workers.jsonl")
    with open(path, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    audit(did, "worker_frozen", rec)


def run_lifecycle(did, pkg):
    """Trigger main (with retries), wait, validate, snapshot, promote."""
    ok, why = spec_hash_ok()
    if not ok:
        audit(did, "spec_hash_mismatch", {"reason": why})
        return 10
    manifest, last_err = None, None
    for attempt in range(1, MAX_TRIGGER_ATTEMPTS + 1):
        resp, err = trigger(did, attempt)
        if err:
            audit(did, "trigger_failed", {"error": err, "attempt": attempt})
            return 6
        audit(did, "triggered", {
            "runId": resp.get("runId") if isinstance(resp, dict) else None,
            "attempt": attempt,
        })
        manifest, last_err = wait_for_submission(did)
        if manifest:
            break
        audit(did, "submission_timeout", {"error": last_err, "attempt": attempt})
    if not manifest:
        audit(did, "frozen_no_submission", {"error": last_err})
        return 7
    contracts, problems = validate_submission(did, pkg, manifest)
    if problems:
        audit(did, "submission_rejected", {"problems": problems})
        qdir = os.path.join(QUARANTINE, f"submission-{did}")
        try:
            shutil.copytree(os.path.join(OUTBOX, did), qdir, dirs_exist_ok=True)
        except Exception:
            pass
        return 8
    rec = approval_record(did)
    if rec:
        try:
            with open(decision_file(did), "rb") as f:
                cur_dsha = sha256_bytes(f.read())
        except Exception as e:
            audit(did, "approval_check_failed", {"error": str(e)})
            return 8
        if cur_dsha != rec.get("decision_sha256"):
            audit(did, "approval_voided_decision_changed", {
                "approved_sha": rec.get("decision_sha256"), "current_sha": cur_dsha})
            return 8
    snapshot_contracts(did, contracts)
    result = promote(did, contracts)
    if isinstance(result, str):
        audit(did, "toctou_violation", {"error": result})
        workers = {c["worker"] for c in contracts}
        for w in workers:
            freeze_worker(w, did, result)
        return 8
    assertion = {
        "decision_id": did,
        "asserted_at": now_iso(),
        "contracts": [
            {"file": c["name"], "sha256": c["sha"], "worker": c["worker"],
             "promoted_to": p}
            for c, p in zip(contracts, result)
        ],
        "nc_coverage": "full",
        "subset_check": "pass",
    }
    os.makedirs(DONE, exist_ok=True)
    with open(os.path.join(DONE, f"assert-{did}.json"), "w") as f:
        json.dump(assertion, f, ensure_ascii=False, indent=2)
    if rec:
        rec["contracts_sha256"] = {c["name"]: c["sha"] for c in contracts}
        rec["contracts_bound_at"] = now_iso()
        with open(approval_path(did), "w") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
        audit(did, "approval_bound_to_contracts", {"files": list(rec["contracts_sha256"])})
    audit(did, "promoted", {"contracts": [c["name"] for c in contracts]})
    return 0


# ----------------------------------------------------------------- approvals
def approval_path(did):
    return os.path.join(APPROVALS, f"{did}.json")


def approval_record(did):
    p = approval_path(did)
    if os.path.isfile(p):
        try:
            with open(p) as f:
                rec = json.load(f)
            if rec.get("status") == "approved":
                return rec
        except Exception:
            return None
    return None


def decision_file(did):
    return os.path.join(PENDING, f"decision-{did}.json")


def approve(did, by):
    dest = decision_file(did)
    if not os.path.isfile(dest):
        audit(did, "approve_not_found", {"path": dest})
        return 4
    with open(dest, "rb") as f:
        pkg = json.loads(f.read())
    with open(dest, "rb") as f:
        dsha = sha256_bytes(f.read())
    os.makedirs(APPROVALS, exist_ok=True)
    rec = {
        "decision_id": did,
        "decision_sha256": dsha,
        "contracts_sha256": None,
        "approved_by": by,
        "approved_at": now_iso(),
        "status": "approved",
    }
    snap = os.path.join(SNAPSHOTS, did, "snapshot.json")
    if os.path.isfile(snap):
        with open(snap) as f:
            meta = json.load(f)
        rec["contracts_sha256"] = meta.get("files")
    with open(approval_path(did), "w") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    audit(did, "approved", {"by": by, "decision_sha256": dsha})
    marker = dest + ".pending_approval"
    if os.path.exists(marker):
        os.remove(marker)
    return run_lifecycle(did, pkg)


def revoke(did, by):
    rec = approval_record(did)
    if not rec:
        audit(did, "revoke_no_record", {})
        return 4
    rec["status"] = "revoked"
    rec["revoked_by"] = by
    rec["revoked_at"] = now_iso()
    with open(approval_path(did), "w") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    audit(did, "revoked", {"by": by})
    # freeze unclaimed promoted contracts of this decision
    moved = []
    if os.path.isdir(PENDING):
        for sub in os.listdir(PENDING):
            wdir = os.path.join(PENDING, sub)
            if not os.path.isdir(wdir):
                continue
            for name in os.listdir(wdir):
                if not name.endswith(".json"):
                    continue
                p = os.path.join(wdir, name)
                try:
                    with open(p) as f:
                        data = json.load(f)
                except Exception:
                    continue
                if data.get("decision_id") == did:
                    q = os.path.join(QUARANTINE, f"revoked-{did}-{name}")
                    os.makedirs(QUARANTINE, exist_ok=True)
                    os.rename(p, q)
                    moved.append(q)
    audit(did, "revoked_contracts_quarantined", {"moved": moved})
    return 0


# -------------------------------------------------------------------- deliver
def deliver(pkg):
    did = pkg.get("decision_id")
    problems = validate(pkg)
    if problems:
        audit(did, "rejected_invalid", {"problems": problems})
        return 3
    where = decision_exists(did)
    if where:
        audit(did, "rejected_duplicate", {"existing": where})
        return 4
    pkg = dict(pkg)
    pkg.setdefault("source", "ceo-decision-notifier")
    pkg["received_at"] = now_iso()
    pkg["consultation"] = consultation_shadow(pkg)
    forced, reasons = escalation_check(pkg)
    if forced:
        pkg["requires_approval"] = True
        pkg["escalation_reasons"] = reasons
    dest = os.path.join(PENDING, f"decision-{did}.json")
    try:
        os.makedirs(PENDING, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=PENDING, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(pkg, f, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o644)  # gateway node user must be able to read it
        os.rename(tmp, dest)
    except Exception as e:
        audit(did, "alarm_write_failed", {"error": str(e)})
        return 5
    audit(did, "written_pending", {
        "path": dest,
        "degraded_shadow": pkg["consultation"]["notifier_degraded"],
    })
    if pkg.get("requires_approval") is True:
        marker = dest + ".pending_approval"
        try:
            with open(marker, "w") as f:
                f.write(now_iso() + "\n")
        except Exception as e:
            audit(did, "alarm_marker_failed", {"error": str(e)})
            return 5
        audit(did, "pending_approval", {"marker": marker, "reasons": reasons})
        return 0
    return run_lifecycle(did, pkg)


def resume(decision_id):
    dest = decision_file(decision_id)
    if not os.path.exists(dest):
        audit(decision_id, "resume_not_found", {"path": dest})
        return 4
    with open(dest) as f:
        pkg = json.load(f)
    forced, reasons = escalation_check(pkg)
    if forced and not approval_record(decision_id):
        audit(decision_id, "resume_blocked_needs_approval", {"reasons": reasons})
        return 0
    marker = dest + ".pending_approval"
    if os.path.exists(marker):
        os.remove(marker)
    audit(decision_id, "resumed", {})
    return run_lifecycle(decision_id, pkg)


def main(argv):
    if len(argv) >= 3 and argv[1] == "--resume":
        return resume(argv[2])
    if len(argv) >= 4 and argv[1] == "--approve" and argv[3] == "--by":
        return approve(argv[2], argv[4] if len(argv) >= 5 else "unknown")
    if len(argv) >= 4 and argv[1] == "--revoke" and argv[3] == "--by":
        return revoke(argv[2], argv[4] if len(argv) >= 5 else "unknown")
    if len(argv) >= 2 and argv[1] == "--stdin":
        raw = sys.stdin.read()
    elif len(argv) >= 2:
        with open(argv[1]) as f:
            raw = f.read()
    else:
        print(__doc__)
        return 2
    try:
        pkg = json.loads(raw)
    except Exception as e:
        audit(None, "rejected_invalid_json", {"error": str(e)})
        return 3
    return deliver(pkg)


if __name__ == "__main__":
    sys.exit(main(sys.argv))

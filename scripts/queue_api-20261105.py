#!/usr/bin/env python3
"""queue_api — PRODUCTION install 2026-11-05 (task 3 of 4, single-machine stage).
Adapted from Window B draft A (state/window-b-20261104/queue_api_draft.py).
Reused from draft: route shape, fencing/lease semantics (1200 s), rate limit
30 req/min, artifact cap 5 MiB, fixed artifact filename <task_id>.bin, verifier
owns DONE (worker only reaches VERIFYING), stdlib only, binds 127.0.0.1 only.

Adaptations for production (install review decisions, all recorded in
state/queue-api-20261105/EVIDENCE-20261105.md):
- DSN from state/.cq-dsn (same file as the production executor, fail closed).
  The draft's WB_TEST_DSN / central_queue_test wiring is NOT used.
- Tables are the production ones: central_tasks / central_leases (public
  schema). The draft's wb.* schema (wb.tasks/wb.leases/wb.workers/
  wb.api_audit/wb.checkpoints) does not exist in production.
- Claim predicate is byte-identical in meaning to the production executor
  whitelist (item 2, 2026-11-05): llm_doc/script/patrol/timer, plus code
  only with non-empty workdir. A code task with no workdir (e.g. the READY
  OCI task b639d483-71b5-4262-b536-73c049ee8e15) is NEVER claimable here.
- Auth: single worker-1 bearer token. Only its sha256 is compared, read
  from state/.queue-api-token-hash (0600). Plaintext token lives only in
  state/.queue-api-worker1-token (0600, hub-only, for delivery to worker-1
  when its pull client is installed). No wb.workers table exists in prod.
  Worker identity is fixed to "worker-1" (allowlist for this stage).
- Audit: file JSONL state/logs/queue-api-audit.log. central_events is not
  writable by the cq_timer DB user (SELECT denied), so the draft's DB audit
  cannot be used.
- Checkpoints: file-backed under state/queue-api-20261105/checkpoints/
  <task_id>/<step>.json (write-if-absent, idempotent like the draft's
  ON CONFLICT DO NOTHING) + central_tasks.next_step / last_checkpoint_id
  update. Reason: the DB user has no schema CREATE privilege, so the
  contract's central_checkpoints table cannot be created by this install;
  table creation is a remaining step for the DB owner (see evidence file).
- Submit keeps the lease ACTIVE (diverges from draft, which released it):
  the production verifier requires an ACTIVE lease to check fencing before
  flipping VERIFYING -> DONE; releasing at submit would make every submit
  fail verification.
- Lease expiry is NOT swept here: the existing per-minute cq_sweep cron
  already expires leases (301 EXPIRED rows observed). No new sweeper.
- Artifact upload: POST /v1/artifact, raw octet-stream, headers
  X-Task-Id / X-Fencing-Token, 5 MiB cap, atomic replace, fence-checked.
  sha is advisory only; the verifier recomputes it before DONE.
- No unauthenticated routes (no health endpoint): liveness is proven by
  the 401 response to an unauthenticated claim.
- Never: arbitrary SQL, arbitrary file read, prod DB credentials in logs,
  token in logs. The token hash file is re-read per request so rotation
  (rewrite hash file) takes effect without a restart.
Run: systemctl --user start queue-api (unit queue-api.service).
"""
import hashlib, hmac, json, os, re, subprocess, sys, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

DSN_FILE = "/home/ubuntu/ai-team/state/.cq-dsn"
TOKEN_HASH_FILE = "/home/ubuntu/ai-team/state/.queue-api-token-hash"
AUDIT_LOG = "/home/ubuntu/ai-team/state/logs/queue-api-audit.log"
QUARANTINE_DIR = "/home/ubuntu/ai-team/state/queue-api-20261105/quarantine"
# Pipeline contract (2026-11-06, thorough fix not a patch): queue_api is the ONLY
# writer that places a submitted artifact where c2-verifier looks.
# c2-verifier code-check globs state/deliverables/<task_id>* and
# state/c2-task-results/<task_id>*; quarantine/ alone is audit-only and
# verifier never reads it. Every artifact submit MUST land in both places
# atomically with identical bytes/sha, or every pull_once code task fails
# gate #1 with "code deliverable file missing".
DELIV_DIR = "/home/ubuntu/ai-team/state/deliverables"
REVIEW_GATE = "/home/ubuntu/ai-team/scripts/code-review-gate-20261107.py"
REVIEW_PRODUCER = "/home/ubuntu/ai-team/scripts/review-producer-20261109.py"
REVIEW_DIR = "/home/ubuntu/ai-team/state/code-review-20261107"
# Review hard gate wiring (code-landing-scan-20261107 item 13, code-dev-sop
# SS三.6/SS五-2): /v1/submit calls code-review-gate for code tasks before
# the VERIFYING transition. Enforcement is AUDIT MODE by default (check
# runs, result audited + logged, transition proceeds). Hard enforcement
# (409 review_blocked) needs env QUEUE_API_REVIEW_ENFORCE=1 AND a server
# restart (queue_api is long-lived, started 2026-11-07 14:00 via
# systemctl --user queue-api; this file edit alone does not activate it).
# Audit mode is deliberate, verified 2026-11-07: no review records exist
# anywhere (state/code-review-20261107/ absent, no producer writes
# review-<task>-*.json) and 4 live code tasks are in flight; hard-on now
# would 409 every code submit. Enable only after a review producer exists
# + butler canary. See state/outbox/review-gate-enforce-decision-20261107.md.
# Review-409 thorough fix (2026-11-10): _ensure_review used to call
# tarfile.open(apath, "r:") directly on the artifact, but code artifacts
# are PULLONCE-BUNDLE-V1 bundles (header + manifest + gzip tar) by
# contract, so every code submit 409'd with artifact-extract-fail:
# invalid header. Extraction now goes through scripts/artifact_bundle.py
# (canonical parser, sha-verified, same semantics as the verifier's
# unpack_bundle); ENFORCE stays on. Rollback: restore
# queue_api-20261105.py.bak-20261110-review409 + systemctl --user
# restart queue-api.

HUB_EXEC = "/home/ubuntu/ai-team/state/pull-once-20261105/hub_exec.py"

def _ensure_worker_workdir(workdir):
    """Create workdir on worker-1 via hub_exec (mkdir -p). Best effort:
    returns True if created/confirmed, False otherwise. Never raises."""
    try:
        wd = (workdir or "").strip()
        if not wd:
            return False
        # Only allow workdir under ~/code-agent-pilot/workdir/ to avoid accidents
        if "code-agent-pilot/workdir" not in wd:
            return False
        # Expand ~ on the worker side; hub_exec runs the cmd on the worker
        cmd = "mkdir -p %s && test -d %s && echo WORKDIR_OK" % (wd, wd)
        r = subprocess.run(["python3", HUB_EXEC], input=cmd,
                           capture_output=True, text=True, timeout=60)
        return "WORKDIR_OK" in (r.stdout or "")
    except Exception:
        return False

CHECKPOINT_DIR = "/home/ubuntu/ai-team/state/queue-api-20261105/checkpoints"
WORKER_ID = "worker-1"
MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
# Cutover51 W3: single lease definition lives in cq_common (LEASE_SECONDS=1200).
# Do NOT define a second lease constant anywhere in this repo (grep canary).
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from cq_common import LEASE_SECONDS
except Exception:
    LEASE_SECONDS = 1200
RATE_PER_MIN = 30
PORT = 12700
# Claim predicate (2026-11-06 fix, W2/W3c evidence; cutover51 W2.4 note):
# Types a worker's pull_once can execute via /v1/claim (2026-11-13 hub-exec
# fix): code with non-empty workdir, script, AND llm_doc with PRE-GENERATED
# content (>=10 bytes). Generative llm_doc (no/short content) is NOT
# claimable here — pull_once has no model access, so it must never claim
# one (it would fail it). Generative llm_doc stays with the hub
# gen-llm-doc-worker until workers get gateway access (chairman decision
# pending, see hub-exec-fix-20261113.md). timer/patrol are hub monitoring
# types claimed by the hub c2-executor directly from Postgres. Workers
# have no direct-DB claim path: the only claim entry for workers is
# /v1/claim.
CLAIM_WHERE = """(payload->>'type' = 'script')
                  OR (payload->>'type' = 'code' AND payload->>'workdir' IS NOT NULL
                      AND payload->>'workdir' <> '')
                  OR (payload->>'type' = 'llm_doc' AND payload->>'content' IS NOT NULL
                      AND length(payload->>'content') >= 10)"""
_UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

def get_dsn():
    try:
        with open(DSN_FILE) as f:
            for line in f:
                line = line.strip()
                if line.startswith("CQ_DSN="):
                    return line[len("CQ_DSN="):].strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""

_ARTIFACT_BUNDLE = None  # lazy-loaded canonical artifact parser (see below)


def _load_artifact_bundle():
    """Lazy import of the canonical artifact parser (cached).

    queue_api is long-lived; the import happens per review attempt so a
    missing/broken module fails closed (review blocked, audited) instead
    of killing the server at startup.
    """
    global _ARTIFACT_BUNDLE
    if _ARTIFACT_BUNDLE is None:
        import importlib
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        _ARTIFACT_BUNDLE = importlib.import_module("artifact_bundle")
    return _ARTIFACT_BUNDLE


def _extract_artifact_files(tid, apath):
    """Format-aware artifact extraction for the review producer.

    Uses artifact_bundle.load_artifact (canonical parser):
    PULLONCE-BUNDLE-V1 bundles (header + manifest + gzip tar,
    sha-verified), plain tars, and single files are recognised. Code
    review needs real code files, so only "bundle" and "tar" kinds are
    accepted; a lone file (e.g. plain pytest text from a code task with
    no bundleable files) is rejected with artifact-not-bundle-or-tar --
    such an artifact violates the contract (taskcontract_gate requires
    format PULLONCE-BUNDLE-V1) and the verifier rejects it anyway; the
    review gate must not wave it through.
    Returns (tmpd, [paths]) on success, (None, reason) on failure.
    Paths are validated to stay inside tmpd (no tar traversal).
    """
    import tempfile
    try:
        _ab = _load_artifact_bundle()
    except Exception as e:
        return None, "bundle-parser-unavailable:%s" % str(e)[:80]
    try:
        files_map, kind = _ab.load_artifact(apath)
    except ValueError as e:
        return None, "artifact-extract-fail:%s" % str(e)[:120]
    except Exception as e:
        return None, "artifact-extract-fail:%s" % str(e)[:120]
    if kind not in ("bundle", "tar"):
        return None, "artifact-not-bundle-or-tar:kind=%s" % kind
    names = sorted(files_map)
    py = [n for n in names if n.endswith(".py")]
    chosen = py if py else names
    chosen = chosen[:20]
    if not chosen:
        return None, "artifact-empty"
    tmpd = tempfile.mkdtemp(prefix="review-%s-" % tid)
    files = []
    base = os.path.realpath(tmpd)
    for name in chosen:
        dest = os.path.realpath(os.path.join(tmpd, name))
        if dest != base and not dest.startswith(base + os.sep):
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(files_map[name])
        files.append(dest)
    if not files:
        return None, "artifact-empty"
    return tmpd, files


def _ensure_review(tid, apath, payload):
    """Auto-run review producer for code tasks lacking review records.
    Returns (records_exist: bool, detail: str). Never raises; on any
    failure returns (False, reason) so the gate decides (audit vs enforce).
    Format-aware extraction (2026-11-10 review-409 thorough fix):
    artifacts are PULLONCE-BUNDLE-V1 bundles (header + manifest + gzip
    tar) or plain tars; the old code called tarfile.open() directly on
    the file and 409'd every bundle with "invalid header". Extraction
    now goes through artifact_bundle.load_artifact (canonical parser,
    sha-verified, same semantics as the verifier's unpack_bundle).
    """
    std_path = os.path.join(REVIEW_DIR, "review-%s-standards.json" % tid)
    spec_path = os.path.join(REVIEW_DIR, "review-%s-spec.json" % tid)
    if os.path.exists(std_path) and os.path.exists(spec_path):
        return True, "records-exist"
    tmpd, files = _extract_artifact_files(tid, apath)
    if tmpd is None:
        return False, files  # files holds the reason string here
    try:
        contract = (payload or {}).get("contract") or {}
        fp = contract.get("five_part") or {}
        spec_txt = (
            "task_id: %s\ninput: %s\npass_line: %s\n"
            "goal: %s\nstop_when: %s\nconstraint: %s\nmust_not: %s\n"
            % (tid, contract.get("input", ""), contract.get("pass_line", ""),
               fp.get("goal", ""), fp.get("stop_when", ""), fp.get("constraint", ""),
               ", ".join(fp.get("must_not", []) if isinstance(fp.get("must_not"), list) else [str(fp.get("must_not", ""))]))
        )
        spec_file = os.path.join(tmpd, "spec.md")
        with open(spec_file, "w") as f:
            f.write(spec_txt)
    except Exception as e:
        return False, "spec-build-fail:%s" % str(e)[:120]
    try:
        r = subprocess.run(["python3", REVIEW_PRODUCER, "--task-id", str(tid),
                            "--files"] + files + ["--spec", spec_file,
                            "--review-dir", REVIEW_DIR, "--timeout", "300"],
                           capture_output=True, text=True, timeout=330)
        ok = os.path.exists(std_path) and os.path.exists(spec_path)
        return ok, "producer-rc=%d records=%s" % (r.returncode, ok)
    except Exception as e:
        return False, "producer-fail:%s" % str(e)[:120]
def _audit(worker, endpoint, task_id, result):
    try:
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                "worker": worker, "endpoint": endpoint,
                                "task_id": str(task_id), "result": result}) + "\n")
    except Exception:
        pass

# F1 2026-11-10 (failure_id 12): diagnostic event payload. The audit
# trigger writes central_events payload '{}', so every entry into
# RETRY_WAIT/QUARANTINED was unreadable without chasing pull_once.log on
# worker-1. Writers stamp the just-created STATUS_CHANGE row (same txn)
# with failure_class (existing F-* taxonomy, same tokens as
# retrywait-scan classify()) + reason. Diagnostic only: nothing reads
# event payload for decisions, so this cannot change routing or gating.
_FAIL_CLASS_EXACT = {
    "pytest_exit_5": "F-EMPTY",
    "pytest_timeout": "F-TIMEOUT",
    "workdir_missing": "F-EMPTY",
    "workdir_create_failed": "F-EMPTY",
    "workdir_not_allowlisted": "F-ROUTING",
    "command_not_allowlisted": "F-CONTRACT",
    "artifact_failed": "F-VERIFY",
    "checkpoint1_failed": "F-503",
    "checkpoint2_failed": "F-503",
    "heartbeat_failed": "F-503",
    "exception": "F-UNDIAG",
    "unknown": "F-UNDIAG",
}

def _fail_class(reason):
    if reason in _FAIL_CLASS_EXACT:
        return _FAIL_CLASS_EXACT[reason]
    if reason.startswith("pytest_exit_") or reason.startswith("script_exit_"):
        return "F-VERIFY"
    if reason.startswith("unsupported_type_"):
        return "F-CONTRACT"
    return "F-UNDIAG"

def _stamp_event(cur, task_id, new_status, fclass, reason):
    cur.execute(
        "UPDATE central_events SET payload = jsonb_build_object('failure_class', %s::text, 'reason', %s::text)"
        " WHERE event_id = (SELECT event_id FROM central_events"
        " WHERE task_id=%s AND new_status=%s AND payload='{}'::jsonb"
        " ORDER BY created_at DESC LIMIT 1)",
        (fclass, reason[:300], task_id, new_status))

class H(BaseHTTPRequestHandler):
    bucket = {}
    def log_message(self, *a):  # never log Authorization
        sys.stderr.write("%s %s\n" % (self.command, self.path))
    def _auth(self):
        tok = (self.headers.get("Authorization") or "")
        if not tok.startswith("Bearer "):
            return None
        try:
            want = open(TOKEN_HASH_FILE).read().strip()
        except Exception:
            return None
        got = hashlib.sha256(tok[7:].encode()).hexdigest()
        return WORKER_ID if hmac.compare_digest(got, want) else None
    def _body_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if n < 0 or n > 65536:
            return None
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw or b"{}")
        except Exception:
            return None
    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def do_POST(self):
        dsn = get_dsn()
        if not dsn:
            self._send(503, {"error": "no_dsn_refusing"})
            return
        import psycopg
        path = urlparse(self.path).path
        # /v1/artifact carries raw bytes, not JSON.
        if path == "/v1/artifact":
            self._artifact(dsn)
            return
        body = self._body_json()
        if body is None:
            self._send(400, {"error": "bad_json_or_too_large"})
            return
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                worker = self._auth()
                if not worker:
                    self._send(401, {"error": "unauthorized"})
                    return
                now = time.time()
                stamps = [t for t in self.bucket.get(worker, []) if now - t < 60]
                if len(stamps) >= RATE_PER_MIN:
                    self._send(429, {"error": "rate_limited"})
                    return
                stamps.append(now)
                self.bucket[worker] = stamps
                if path == "/v1/claim":
                    cur.execute("""SELECT task_id, payload, current_fencing_token, min_bytes, retry_count, deadline, workdir
                        FROM central_tasks WHERE status='READY' AND retry_count < COALESCE(max_retries, 3) AND (""" + CLAIM_WHERE + """)
                        AND NOT EXISTS (SELECT 1 FROM central_leases l WHERE l.task_id = central_tasks.task_id AND l.status = 'ACTIVE')
                        ORDER BY CASE lane WHEN 'P0' THEN 0 WHEN 'biz' THEN 1 WHEN 'infra' THEN 2 ELSE 9 END, created_at
                        LIMIT 1 FOR UPDATE SKIP LOCKED""")
                    row = cur.fetchone()
                    if not row:
                        self._send(200, {"claimed": None})
                        return
                    tid, payload, token, min_bytes, retry_count, deadline, wd_col = row
                    new_token = (token or 0) + 1
                    # Transition guard requires READY -> DISPATCHING -> CLAIMED
                    # (direct READY -> CLAIMED is rejected by the trigger).
                    cur.execute("UPDATE central_tasks SET status='DISPATCHING' WHERE task_id=%s", (tid,))
                    cur.execute("UPDATE central_tasks SET status='CLAIMED', current_fencing_token=%s, heartbeat_at=now() WHERE task_id=%s", (new_token, tid))
                    # Cutover51 W3: lease_epoch = fencing token (increments per
                    # claim); expiry capped by the task deadline (never renewed
                    # past deadline).
                    import datetime as _dt
                    _exp = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=LEASE_SECONDS)
                    if deadline is not None:
                        try:
                            _dl = deadline if deadline.tzinfo is not None else deadline.replace(tzinfo=_dt.timezone.utc)
                            _exp = min(_exp, _dl)
                        except Exception:
                            pass
                    cur.execute("INSERT INTO central_leases (task_id, worker_id, fencing_token, lease_epoch, expires_at) VALUES (%s, %s, %s, %s, %s)",
                                (tid, worker, new_token, new_token, _exp))
                    conn.commit()
                    _audit(worker, "claim", tid, "ok")
                    cur.execute("SELECT task_id, title, payload FROM central_tasks WHERE task_id=%s", (tid,))
                    t = cur.fetchone()
                    # Thorsten Ball Rule 7 (2026-11-07 fix) + cutover51 W2.4:
                    # workdir is created ONCE, HERE, at claim time, from the
                    # central_tasks.workdir column (payload.workdir fallback).
                    _pl = t[2] if isinstance(t[2], dict) else {}
                    _wd = (wd_col or _pl.get("workdir", "") or "")
                    if isinstance(_wd, str) and _wd.strip():
                        _ok = _ensure_worker_workdir(_wd)
                        print("queue_api: workdir ensure %s -> %s" % (_wd, "OK" if _ok else "FAIL"), flush=True)
                    self._send(200, {"task_id": str(t[0]), "title": t[1], "payload": t[2], "fencing_token": new_token, "lease_epoch": new_token})
                    return
                if path == "/v1/heartbeat":
                    tid, tok = body.get("task_id"), body.get("fencing_token")
                    epoch = body.get("lease_epoch")
                    if not _UUID_RE.match(str(tid or "")):
                        self._send(400, {"error": "bad_task_id"})
                        return
                    if epoch is not None and epoch != tok:
                        self._send(409, {"error": "epoch_mismatch"})
                        return
                    # Cutover51 W3: renewal is ONE atomic statement; the
                    # WHERE decides the sweep-vs-renew race (no separate
                    # coordination code). Expired leases cannot be renewed,
                    # and renewal never extends past the task deadline.
                    _iv = str(LEASE_SECONDS)
                    cur.execute("""UPDATE central_leases SET last_heartbeat_at=now(),
                        expires_at=(SELECT LEAST(now()+interval '""" + _iv + """ seconds', COALESCE(t.deadline, now()+interval '""" + _iv + """ seconds')) FROM central_tasks t WHERE t.task_id=central_leases.task_id)
                        WHERE task_id=%s AND worker_id=%s AND fencing_token=%s AND status='ACTIVE' AND expires_at > now()""", (tid, worker, tok))
                    if cur.rowcount != 1:
                        self._send(409, {"error": "fence_or_lease_mismatch"})
                        return
                    # long-task-heartbeat (2026-11-06): stamp task-level heartbeat too.
                    cur.execute("UPDATE central_tasks SET heartbeat_at=now() WHERE task_id=%s AND current_fencing_token=%s", (tid, tok))
                    conn.commit()
                    _audit(worker, "heartbeat", tid, "ok")
                    self._send(200, {"ok": True})
                    return
                if path == "/v1/checkpoint":
                    tid, tok = body.get("task_id"), body.get("fencing_token")
                    step = body.get("step")
                    state_json = body.get("state_json")
                    sha = body.get("sha")
                    next_step = body.get("next_step")
                    if not _UUID_RE.match(str(tid or "")) or not isinstance(step, int) or not 0 <= step <= 9999:
                        self._send(400, {"error": "bad_task_id_or_step"})
                        return
                    if not isinstance(state_json, dict):
                        self._send(400, {"error": "bad_state_json"})
                        return
                    if sha is not None and not _HEX64_RE.match(str(sha)):
                        self._send(400, {"error": "bad_sha"})
                        return
                    cur.execute("SELECT current_fencing_token FROM central_tasks WHERE task_id=%s", (tid,))
                    r = cur.fetchone()
                    if not r or r[0] != tok:
                        self._send(409, {"error": "fence_mismatch"})
                        return
                    if body.get("lease_epoch") is not None and body.get("lease_epoch") != tok:
                        self._send(409, {"error": "epoch_mismatch"})
                        return
                    cur.execute("SELECT 1 FROM central_leases WHERE task_id=%s AND worker_id=%s AND fencing_token=%s AND status='ACTIVE'",
                                (tid, worker, tok))
                    if not cur.fetchone():
                        self._send(409, {"error": "no_active_lease"})
                        return
                    cdir = os.path.join(CHECKPOINT_DIR, str(tid))
                    os.makedirs(cdir, exist_ok=True)
                    cpath = os.path.join(cdir, "%d.json" % step)
                    try:
                        with open(cpath, "x", encoding="utf-8") as f:
                            cid = str(uuid.uuid4())
                            f.write(json.dumps({"task_id": str(tid), "step": step, "checkpoint_id": cid,
                                                "state_json": state_json, "sha": sha, "next_step": next_step,
                                                "worker": worker}, ensure_ascii=False))
                    except FileExistsError:
                        try:
                            cid = json.load(open(cpath, encoding="utf-8")).get("checkpoint_id")
                        except Exception:
                            self._send(409, {"error": "checkpoint_conflict"})
                            return
                        if not cid:
                            self._send(409, {"error": "checkpoint_conflict"})
                            return
                    # 2026-11-05 pull_once fix: central_checkpoints EXISTS but
                    # cq_timer has NO privilege on it (all False) and
                    # central_tasks.last_checkpoint_id has an FK to it, so
                    # setting last_checkpoint_id to any real UUID raises
                    # ForeignKeyViolation and kills the request (proven live
                    # 06:37 UTC). File-backed checkpoint written above is
                    # the record; only next_step is updated in the DB.
                    # long-task-heartbeat (2026-11-06): a checkpoint is also
                    # a sign of life, so stamp heartbeat_at too.
                    cur.execute("UPDATE central_tasks SET next_step=%s, heartbeat_at=now() WHERE task_id=%s AND current_fencing_token=%s",
                                (next_step, tid, tok))
                    if cur.rowcount != 1:
                        self._send(409, {"error": "task_not_claimed"})
                        return
                    conn.commit()
                    _audit(worker, "checkpoint", tid, "ok")
                    self._send(200, {"ok": True, "checkpoint_id": cid})
                    return
                if path == "/v1/submit":
                    tid, tok = body.get("task_id"), body.get("fencing_token")
                    receipt_id = body.get("receipt_id")
                    if receipt_id is not None and not _UUID_RE.match(str(receipt_id)):
                        self._send(400, {"error": "bad_receipt_id"})
                        return
                    if not _UUID_RE.match(str(tid or "")):
                        self._send(400, {"error": "bad_task_id"})
                        return
                    cur.execute("SELECT current_fencing_token FROM central_tasks WHERE task_id=%s", (tid,))
                    r = cur.fetchone()
                    if not r or r[0] != tok:
                        self._send(409, {"error": "fence_mismatch"})
                        return
                    if body.get("lease_epoch") is not None and body.get("lease_epoch") != tok:
                        self._send(409, {"error": "epoch_mismatch"})
                        return
                    # 2026-11-13 submit-fix: _artifact writes llm_doc as
                    # <task_id>.md (hub-exec fix) but submit hardcoded .bin,
                    # so every llm_doc submit 409'd artifact_missing (a2 +
                    # hw-n0, 2026-11-13). Match the extension to the task
                    # type, with fallback to the other extension.
                    cur.execute("SELECT payload->>'type' FROM central_tasks WHERE task_id=%s", (tid,))
                    _trow = cur.fetchone()
                    _ttype = _trow[0] if _trow else None
                    _ext = ".md" if _ttype == "llm_doc" else ".bin"
                    apath = os.path.join(QUARANTINE_DIR, str(tid) + _ext)
                    if not os.path.exists(apath):
                        _alt = ".bin" if _ext == ".md" else ".md"
                        _altpath = os.path.join(QUARANTINE_DIR, str(tid) + _alt)
                        if os.path.exists(_altpath):
                            apath = _altpath
                        else:
                            self._send(409, {"error": "artifact_missing"})
                            return
                    data = open(apath, "rb").read()
                    # Review hard gate (item 13): code tasks need REVIEW-PASS
                    # (standards + spec) before VERIFYING. Audit mode by
                    # default; hard enforcement only with env
                    # QUEUE_API_REVIEW_ENFORCE=1 (see REVIEW_GATE note).
                    try:
                        cur.execute("SELECT payload->>'type' FROM central_tasks WHERE task_id=%s", (tid,))
                        _trow = cur.fetchone()
                        _ttype = _trow[0] if _trow else None
                    except Exception:
                        _ttype = None
                    if _ttype == "code":
                        # Auto-producer (2026-11-09): if no review records exist,
                        # run the producer from the artifact + payload spec first,
                        # so the gate below can pass without manual CLI.
                        try:
                            cur.execute("SELECT payload FROM central_tasks WHERE task_id=%s", (tid,))
                            _prow = cur.fetchone()
                            _payload = _prow[0] if _prow else {}
                            _ok, _det = _ensure_review(str(tid), apath, _payload)
                            _audit(worker, "submit-review-producer", tid, _det)
                            print("queue_api submit-review-producer %s %s" % (tid, _det), flush=True)
                        except Exception as _e:
                            _audit(worker, "submit-review-producer", tid, "error:%s" % str(_e)[:120])
                        _gr = subprocess.run(["python3", REVIEW_GATE, str(tid)],
                                             capture_output=True, text=True, timeout=60)
                        _gres = "REVIEW-PASS" if _gr.returncode == 0 else "REVIEW-BLOCKED"
                        _audit(worker, "submit-review", tid, "%s rc=%d" % (_gres, _gr.returncode))
                        print("queue_api submit-review %s %s rc=%d" % (tid, _gres, _gr.returncode), flush=True)
                        if _gr.returncode != 0 and os.environ.get("QUEUE_API_REVIEW_ENFORCE") == "1":
                            self._send(409, {"error": "review_blocked",
                                             "detail": (_gr.stdout or "").strip()[:500]})
                            return
                    # Transition guard requires CLAIMED -> RUNNING -> VERIFYING
                    # (direct CLAIMED -> VERIFYING is rejected by the trigger).
                    cur.execute("UPDATE central_tasks SET status='RUNNING' WHERE task_id=%s AND current_fencing_token=%s AND status='CLAIMED'",
                                (tid, tok))
                    if receipt_id:
                        cur.execute("UPDATE central_tasks SET status='VERIFYING', artifact_sha=%s, artifact_bytes=%s, receipt_id=%s WHERE task_id=%s AND current_fencing_token=%s AND status='RUNNING'",
                                    (hashlib.sha256(data).hexdigest(), len(data), receipt_id, tid, tok))
                    else:
                        cur.execute("UPDATE central_tasks SET status='VERIFYING', artifact_sha=%s, artifact_bytes=%s WHERE task_id=%s AND current_fencing_token=%s AND status='RUNNING'",
                                    (hashlib.sha256(data).hexdigest(), len(data), tid, tok))
                    if cur.rowcount != 1:
                        self._send(409, {"error": "task_not_claimed"})
                        return
                    # Lease stays ACTIVE: production verifier needs it to check
                    # fencing before VERIFYING -> DONE.
                    conn.commit()
                    _audit(worker, "submit", tid, "ok")
                    self._send(200, {"ok": True, "state": "VERIFYING"})
                    return
                if path == "/v1/fail":
                    tid, tok = body.get("task_id"), body.get("fencing_token")
                    if not _UUID_RE.match(str(tid or "")):
                        self._send(400, {"error": "bad_task_id"})
                        return
                    cur.execute("SELECT current_fencing_token, retry_count, max_retries, workdir FROM central_tasks WHERE task_id=%s", (tid,))
                    r = cur.fetchone()
                    if not r or r[0] != tok:
                        self._send(409, {"error": "fence_mismatch"})
                        return
                    nxt = "RETRY_WAIT" if r[1] < r[2] else "QUARANTINED"
                    # F2 2026-11-10 (failure_id 11): terminal quarantine must not +1 (was max+1 overshoot).
                    if nxt == "QUARANTINED":
                        cur.execute("UPDATE central_tasks SET status='QUARANTINED' WHERE task_id=%s AND status IN ('CLAIMED','RUNNING')", (tid,))
                    else:
                        cur.execute("UPDATE central_tasks SET status='RETRY_WAIT', retry_count=retry_count+1 WHERE task_id=%s AND status IN ('CLAIMED','RUNNING')", (tid,))
                    if cur.rowcount != 1:
                        self._send(409, {"error": "task_not_claimed"})
                        return
                    cur.execute("UPDATE central_leases SET status='RELEASED' WHERE task_id=%s AND status='ACTIVE'", (tid,))
                    # F1 2026-11-10 (failure_id 12): stamp the just-created
                    # STATUS_CHANGE event row with the worker-supplied
                    # reason (pull_once sends it; absent for old clients).
                    _freason = str(body.get("reason") or "unknown")[:200]
                    _rdetail = _freason + (" workdir=" + r[3] if r[3] else "")
                    # Harden 2026-11-12: _stamp_event is best-effort. A stamp
                    # failure must not roll back the whole fail transaction
                    # (that is what made every /v1/fail drop 2026-11-10/11).
                    try:
                        _stamp_event(cur, tid, nxt, _fail_class(_freason), _rdetail)
                    except Exception as _se:
                        print("queue_api: _stamp_event best-effort failed task=%s err=%s" % (tid, _se), flush=True)
                    conn.commit()
                    _audit(worker, "fail", tid, nxt)
                    self._send(200, {"ok": True, "state": nxt})
                    return
                self._send(404, {"error": "unknown_route"})
    def _artifact(self, dsn):
        tid = self.headers.get("X-Task-Id")
        tok = self.headers.get("X-Fencing-Token")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send(400, {"error": "bad_content_length"})
            return
        if n <= 0 or n > MAX_ARTIFACT_BYTES:
            self._send(413, {"error": "artifact_too_large_or_empty"})
            return
        if not _UUID_RE.match(str(tid or "")):
            self._send(400, {"error": "bad_task_id"})
            return
        try:
            tok = int(tok)
        except (TypeError, ValueError):
            self._send(400, {"error": "bad_fencing_token"})
            return
        worker = self._auth()
        if not worker:
            self._send(401, {"error": "unauthorized"})
            return
        data = self.rfile.read(n)
        if len(data) != n:
            self._send(400, {"error": "short_read"})
            return
        import psycopg
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_fencing_token FROM central_tasks WHERE task_id=%s", (tid,))
                r = cur.fetchone()
                if not r or r[0] != tok:
                    self._send(409, {"error": "fence_mismatch"})
                    return
                cur.execute("SELECT 1 FROM central_leases WHERE task_id=%s AND worker_id=%s AND fencing_token=%s AND status='ACTIVE'",
                            (tid, worker, tok))
                if not cur.fetchone():
                    self._send(409, {"error": "no_active_lease"})
                    return
                cur.execute("SELECT payload->>'type' FROM central_tasks WHERE task_id=%s", (tid,))
                _trow = cur.fetchone()
                _ptype = (_trow[0] if _trow else None)
        # 2026-11-13 hub-exec fix: the c2-verifier requires llm_doc
        # deliverables at <task_id>.md (exact name); code uses a glob so
        # .bin is fine there. Write the extension the verifier expects.
        _ext = ".md" if _ptype == "llm_doc" else ".bin"
        os.makedirs(QUARANTINE_DIR, exist_ok=True)
        tmp = os.path.join(QUARANTINE_DIR, str(tid) + _ext + ".tmp.%d" % os.getpid())
        final = os.path.join(QUARANTINE_DIR, str(tid) + _ext)
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, final)
        # Canonical verifier placement: identical bytes, atomic replace.
        os.makedirs(DELIV_DIR, exist_ok=True)
        _dtmp = os.path.join(DELIV_DIR, str(tid) + _ext + ".tmp.%d" % os.getpid())
        _dfinal = os.path.join(DELIV_DIR, str(tid) + _ext)
        with open(_dtmp, "wb") as f:
            f.write(data)
        os.replace(_dtmp, _dfinal)
        _audit(worker, "artifact", tid, "ok")
        self._send(200, {"ok": True, "bytes": len(data),
                         "sha256": hashlib.sha256(data).hexdigest()})
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    a = ap.parse_args()
    print("queue_api PROD 20261105 (binds 127.0.0.1:%d, worker-1 only)" % a.port, flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()

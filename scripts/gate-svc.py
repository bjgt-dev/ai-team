#!/usr/bin/env python3
"""gate-svc: evidence gateway (chairman order S2, cutover 2026-11-10).

Stdlib only (http.server ThreadingHTTPServer). Binds $GATE_BIND_HOST:$GATE_BIND_PORT (default 127.0.0.1:8093).
DB: central_queue in ops-pg container, reached over TCP via psql CLI
(password from /etc/noesis/gate-db.env, never logged, never in argv).

Endpoints:
  GET  /health
  POST /search  {seat, slug, query}            -> grep knowledge/, write 2 receipts
  POST /approve {chairman_token, task_slug}    -> write plan_approvals
  POST /verify  {receipt_ids, plan_id?, slug}  -> recompute sig, 24h, slug match
  POST /act     {seat, task, action, plan_id, receipt_ids} -> verify, then gate.mode
  POST /gate/dispatch {task_id, contract_hash, contract} -> run the six dispatch
             gates server-side (taskcontract-strict, decision-code-gate, preflight,
             m5-switch-guard, dispatch-gate-hub, duration-limit); on pass write a
             dispatch_receipts row (gatesvc role only). Cutover51 W1 single throat.
  POST /decision {seat, slug, text, kb_ids, needs_chairman?} -> schema-validated
             decisions insert (W2); kb_ids must exist in kb_index and a kb receipt
             for seat+slug must exist within 24h (same rule as the DB trigger).

Receipt sig: HMAC-SHA256(gate.key, "seat|slug|kind|query_hash|result_hash|ts")
where ts is canonical UTC second precision "YYYY-MM-DDTHH:MM:SS+00:00", the same
string stored in the DB (timestamptz) and read back via to_char, so verify can
recompute the signature exactly.
"""
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BIND_HOST = os.environ.get("GATE_BIND_HOST", "127.0.0.1")
BIND_PORT = int(os.environ.get("GATE_BIND_PORT", "8093"))
KNOWLEDGE_DIR = "/home/ubuntu/ai-team/knowledge"
KEY_FILE = "/etc/noesis/gate.key"
DB_ENV_FILE = "/etc/noesis/gate-db.env"
TOKEN_FILE = "/etc/noesis/chairman.token"
MODE_FILE = "/etc/noesis/gate.mode"
LEDGER_FILE = "/home/ubuntu/ai-team/state/gate-act-ledger.jsonl"

TS_FMT = "%Y-%m-%dT%H:%M:%S+00:00"  # canonical; matches to_char(..., 'YYYY-MM-DD"T"HH24:MI:SS+00:00')
TO_CHAR_FMT = 'YYYY-MM-DD"T"HH24:MI:SS+00:00'
MAX_BODY = 64 * 1024
MAX_QUERY_LEN = 500
MAX_RESULT_CHARS = 4000
RECEIPT_TTL = timedelta(hours=24)
FUTURE_SKEW = timedelta(minutes=5)

SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
ACTION_WHITELIST = {"deploy", "写生产库", "git-push", "systemctl", "ssh-vps", "db-migrate"}
# Enforce-mode execution: each whitelisted action maps to ONE fixed operator
# script (task slug as sole argument, no shell). Scripts do not exist yet on
# purpose: enforce mode therefore fails closed and logs the miss until the
# chairman defines them. gate.mode is currently "off", so nothing executes.
ACTION_SCRIPTS = {
    "deploy": "/home/ubuntu/ai-team/scripts/gate-action-deploy.sh",
    "写生产库": "/home/ubuntu/ai-team/scripts/gate-action-dbwrite.sh",
    "git-push": "/home/ubuntu/ai-team/scripts/gate-action-gitpush.sh",
    "systemctl": "/home/ubuntu/ai-team/scripts/gate-action-systemctl.sh",
    "ssh-vps": "/home/ubuntu/ai-team/scripts/gate-action-sshvps.sh",
    # Cutover51 D-3: db-migrate runs a whitelisted migration file only
    # (slug -> fixed file map inside the script; no arbitrary paths).
    # NOTE (2026-11-10 verified): the gatesvc OS user is NOT in the docker
    # group and the gatesvc DB role has no CREATE on public, so the script
    # fails closed until the chairman decides the DDL execution identity
    # (docker group membership or a TCP owner credential). DDL for 004 was
    # executed by the DB owner operator (docker exec -u postgres) instead.
    "db-migrate": "/home/ubuntu/ai-team/scripts/gate-action-dbmigrate.sh",
}
SCRIPTS_DIR = "/home/ubuntu/ai-team/scripts"


def log(*args):
    print(f"[{datetime.now(timezone.utc).isoformat()}]", *args, flush=True)


def load_hex_key(path):
    with open(path, "r") as f:
        return bytes.fromhex(f.read().strip())


def load_db_env(path):
    env = {}
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    for k in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"):
        if k not in env:
            raise RuntimeError(f"missing {k} in {path}")
    return env


KEY = load_hex_key(KEY_FILE)
DB = load_db_env(DB_ENV_FILE)


def now_ts():
    return datetime.now(timezone.utc).strftime(TS_FMT)


def parse_ts(ts_str):
    return datetime.strptime(ts_str, TS_FMT).replace(tzinfo=timezone.utc)


def receipt_sig(seat, slug, kind, query_hash, result_hash, ts_str):
    msg = f"{seat}|{slug}|{kind}|{query_hash}|{result_hash}|{ts_str}".encode()
    return hmac.new(KEY, msg, hashlib.sha256).hexdigest()


def plan_sig(task_slug, ts_str):
    msg = f"plan|{task_slug}|{ts_str}".encode()
    return hmac.new(KEY, msg, hashlib.sha256).hexdigest()


def sql_esc(s):
    return s.replace("'", "''")


def psql(sql):
    """Run one SQL statement via psql CLI; return stdout. Password via env only."""
    env = dict(os.environ)
    env["PGPASSWORD"] = DB["DB_PASSWORD"]
    env["PGCONNECT_TIMEOUT"] = "5"
    cmd = ["psql", "-h", DB["DB_HOST"], "-p", DB["DB_PORT"], "-U", DB["DB_USER"],
           "-d", DB["DB_NAME"], "-v", "ON_ERROR_STOP=1", "-q", "-t", "-A", "-F", "\t"]
    r = subprocess.run(cmd, input=sql, text=True, capture_output=True, env=env, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"psql rc={r.returncode}: {r.stderr.strip()[:300]}")
    return r.stdout


def grep_knowledge(query):
    """Fixed-string case-insensitive grep over knowledge/. Returns (text, partial)."""
    cmd = ["grep", "-r", "-F", "-i", "-n", "-m", "2", "--", query, KNOWLEDGE_DIR]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return "grep timed out", True
    out = r.stdout or ""
    files = sorted({line.split(":", 1)[0] for line in out.splitlines() if ":" in line})
    partial = (r.returncode == 2)
    header = f"files_matched={len(files)}"
    if partial:
        header += " (partial: some knowledge files are not readable by gatesvc)"
    header += "\n"
    text = header + out
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + "\n[truncated]"
    # rc 0 = match, 1 = no match, 2 = some files unreadable
    return text, partial


def read_mode():
    try:
        with open(MODE_FILE, "r") as f:
            return f.read().strip()
    except OSError:
        return ""


def ledger_append(entry):
    entry["ts"] = datetime.now(timezone.utc).isoformat()
    try:
        with open(LEDGER_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except OSError as e:
        log("LEDGER-WRITE-FAILED", str(e))
        return False


def verify_receipts(receipt_ids, plan_id, slug):
    """Returns (ok, reasons, detail). Check order: exists -> slug -> 24h -> sig."""
    reasons = []
    detail = {"receipts": [], "plan": None}
    now = datetime.now(timezone.utc)
    for rid in receipt_ids:
        if not UUID_RE.match(rid or ""):
            reasons.append(f"bad receipt id format: {rid}")
            continue
        rows = psql(
            "SELECT seat, kind, slug, query_hash, result_hash, "
            f"to_char(ts, '{TO_CHAR_FMT}'), sig FROM public.receipts WHERE id='{rid}';"
        ).strip()
        if not rows:
            reasons.append(f"receipt not found: {rid}")
            continue
        parts = rows.split("\t")
        if len(parts) != 7:
            reasons.append(f"receipt unreadable: {rid}")
            continue
        seat, kind, rslug, qh, rh, ts_str, sig = parts
        try:
            ts = parse_ts(ts_str)
        except ValueError:
            reasons.append(f"receipt bad ts: {rid}")
            continue
        if rslug != slug:
            reasons.append(f"slug mismatch: {rid} (receipt slug={rslug}, expected={slug})")
        age = now - ts
        if age > RECEIPT_TTL:
            reasons.append(f"receipt expired: {rid} (age {age})")
        elif age < -FUTURE_SKEW:
            reasons.append(f"receipt from future: {rid}")
        want = receipt_sig(seat, rslug, kind, qh, rh, ts_str)
        if not hmac.compare_digest(want, sig):
            reasons.append(f"sig mismatch: {rid}")
        detail["receipts"].append({"id": rid, "seat": seat, "kind": kind, "ts": ts_str})
    if plan_id:
        if not UUID_RE.match(plan_id or ""):
            reasons.append(f"bad plan_id format: {plan_id}")
        else:
            rows = psql(
                "SELECT task_slug, "
                f"to_char(ts, '{TO_CHAR_FMT}'), sig FROM public.plan_approvals WHERE plan_id='{plan_id}';"
            ).strip()
            if not rows:
                reasons.append(f"plan not found: {plan_id}")
            else:
                parts = rows.split("\t")
                if len(parts) != 3:
                    reasons.append(f"plan unreadable: {plan_id}")
                else:
                    pslug, ts_str, sig = parts
                    try:
                        ts = parse_ts(ts_str)
                    except ValueError:
                        ts = None
                        reasons.append(f"plan bad ts: {plan_id}")
                    if pslug != slug:
                        reasons.append(f"plan slug mismatch: {plan_id} (plan slug={pslug}, expected={slug})")
                    if ts is not None:
                        age = now - ts
                        if age > RECEIPT_TTL:
                            reasons.append(f"plan approval expired: {plan_id} (age {age})")
                        elif age < -FUTURE_SKEW:
                            reasons.append(f"plan approval from future: {plan_id}")
                    want = plan_sig(pslug, ts_str)
                    if not hmac.compare_digest(want, sig):
                        reasons.append(f"plan sig mismatch: {plan_id}")
                    detail["plan"] = {"plan_id": plan_id, "task_slug": pslug, "ts": ts_str}
    return (len(reasons) == 0, reasons, detail)


# ---- Cutover51 W1: single dispatch throat (/gate/dispatch) ----
def canonical_hash(obj):
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def gate_mode(gate_id):
    try:
        return psql(
            "SELECT mode FROM public.gate_registry WHERE gate_id='%s';" % sql_esc(gate_id)
        ).strip().split("\n")[0].strip()
    except Exception:
        return ""


def gate_overridden(gate_id):
    try:
        row = psql(
            "SELECT 1 FROM public.gate_overrides WHERE gate_id='%s' AND expires_at > now();"
            % sql_esc(gate_id)
        ).strip()
        return bool(row)
    except Exception:
        return False


def mark_blocked(gate_id):
    try:
        psql(
            "UPDATE public.gate_registry SET last_block_ts=now(), updated_at=now() "
            "WHERE gate_id='%s';" % sql_esc(gate_id)
        )
    except Exception as e:
        log("MARK-BLOCKED-FAILED", gate_id, str(e)[:120])


def _run_gate(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        return r.returncode == 0, out[-400:]
    except Exception as e:
        return False, "gate runner error: %s" % str(e)[:200]


def dispatch_gates(task_slug, contract):
    """Run the seven dispatch gates server-side. Returns list of
    (gate_id, passed, detail). Each gate honours its gate_registry mode:
    off -> skipped, log -> failure recorded but not blocking,
    enforce -> failure blocks the receipt."""
    results = []
    ctype = contract.get("task_type")
    # 1. taskcontract (content-validation version)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
        json.dump(contract, tf, ensure_ascii=False)
        cpath = tf.name
    try:
        ok, out = _run_gate(["python3", os.path.join(SCRIPTS_DIR, "taskcontract_gate.py"),
                             "--validate", cpath], timeout=30)
        results.append(("taskcontract-strict", ok, out))
    finally:
        try:
            os.unlink(cpath)
        except OSError:
            pass
    # 2. decision-code-gate: contract must declare the code files + evidence
    # that prove the decision landed in code.
    # G4 (Eval OS phase 2, 2026-11-11): task_type == "eval" has no code
    # decision to land, so this gate accepts eval evidence instead: the
    # contract must declare non-empty eval_id + eval_run_id (trace linkage
    # keys; the 8-field "evidence" text itself is already enforced by
    # gate 1). An eval contract without either field still FAILS here.
    # Every other type keeps the exact old behaviour below.
    if ctype == "eval":
        _eid = contract.get("eval_id")
        _erun = contract.get("eval_run_id")
        if isinstance(_eid, str) and _eid.strip() and isinstance(_erun, str) and _erun.strip():
            results.append(("decision-code-gate", True,
                            "eval evidence: eval_id=%s eval_run_id=%s (no code_files required)" % (_eid.strip(), _erun.strip())))
        else:
            results.append(("decision-code-gate", False,
                            "eval task must declare non-empty eval_id + eval_run_id (eval evidence)"))
    else:
        code_files = contract.get("code_files") or []
        evidence = contract.get("evidence_files") or []
        if not code_files or not evidence:
            results.append(("decision-code-gate", False,
                            "contract declares no code_files/evidence_files (decision must land in code)"))
        else:
            ok, out = _run_gate(["python3", os.path.join(SCRIPTS_DIR, "decision-code-gate.py"),
                                 "--decision", task_slug,
                                 "--code-files", ",".join(code_files),
                                 "--evidence", ",".join(evidence)], timeout=30)
            results.append(("decision-code-gate", ok, out))
    # 3. preflight (four questions + queue three)
    ok1, out1 = _run_gate(["python3", os.path.join(SCRIPTS_DIR, "preflight-gate-20261107.py"),
                           "--task", task_slug, "--check"], timeout=30)
    ok2, out2 = _run_gate(["python3", os.path.join(SCRIPTS_DIR, "preflight-gate-20261107.py"),
                           "--task", task_slug, "--check-queue3"], timeout=30)
    results.append(("preflight-gate", bool(ok1 and ok2), (out1 + " | " + out2)[-400:]))
    # 4. M5 switch guard: the safe-state check must PASS; an autonomous
    # request while the switch is CLOSED is refused outright.
    if contract.get("autonomous") is True:
        results.append(("m5-switch-guard", False,
                        "autonomous dispatch requested while M5 switch is CLOSED"))
    else:
        ok, out = _run_gate(["bash", os.path.join(SCRIPTS_DIR, "check-m5-switch.sh")], timeout=30)
        results.append(("m5-switch-guard", ok, out))
    # 5. dispatch-gate-hub (PLAN + CONTRACT + VERDICT + PREFLIGHT)
    ok, out = _run_gate(["python3", os.path.join(SCRIPTS_DIR, "dispatch-gate-hub.py"),
                         task_slug], timeout=60)
    results.append(("dispatch-gate-hub", ok, out))
    # 6. duration limit (W3.4): >20 min without segmented checkpoints refused.
    est = contract.get("estimated_seconds")
    cps = contract.get("checkpoints")
    if isinstance(est, (int, float)) and est > 1200 and not (isinstance(cps, list) and cps):
        results.append(("duration-limit", False,
                        "estimated_seconds %s > 1200 without checkpoints" % est))
    else:
        results.append(("duration-limit", True, "within limit or checkpoints present"))
    # 7. plan-id-exists (Plan A, g5-planid-dispatch-fix-20261111): a
    # code/script contract that carries plan_id must reference a row that
    # REALLY EXISTS in plan_approvals. Existence-only — same semantics as
    # the S4 lease trigger (never the 24h TTL / slug rule, or dispatches
    # would be refused after the approval's 24h window). A contract
    # without plan_id is left to the taskcontract gate (gate 1) and to
    # dispatch_task.py; this gate only judges a plan_id that is present.
    # Requires the gate_registry row plan-id-exists (mode enforce), or
    # _handle_dispatch treats a failure as log-mode (not blocking).
    if ctype in ("code", "script"):
        _pid = contract.get("plan_id")
        if isinstance(_pid, str) and _pid.strip():
            try:
                _row = psql("SELECT 1 FROM public.plan_approvals WHERE plan_id='%s';" % sql_esc(_pid.strip())).strip()
            except Exception as _e:
                results.append(("plan-id-exists", False, "plan_approvals lookup failed: %s" % str(_e)[:200]))
            else:
                if _row:
                    results.append(("plan-id-exists", True, "plan_id %s exists in plan_approvals" % _pid.strip()))
                else:
                    results.append(("plan-id-exists", False, "plan_id %s not in plan_approvals" % _pid.strip()))
        else:
            results.append(("plan-id-exists", True, "no plan_id in contract (taskcontract gate decides)"))
    else:
        results.append(("plan-id-exists", True, "not a code/script contract"))
    return results


def issue_dispatch_receipt(task_id, contract_hash, gates_passed):
    sql = ("INSERT INTO public.dispatch_receipts(task_id, contract_hash, gates_passed) "
           "VALUES ('%s','%s',ARRAY[%s]) ON CONFLICT (task_id, contract_hash) DO NOTHING;"
           % (sql_esc(task_id), sql_esc(contract_hash),
              ",".join("'%s'" % sql_esc(g) for g in gates_passed)))
    psql(sql)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # keep stdout clean; we log explicitly
        pass

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            raise ValueError("bad Content-Length")
        raw = self.rfile.read(n)
        return json.loads(raw.decode())

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "service": "gate-svc",
                             "mode": read_mode() or "unknown",
                             "time": datetime.now(timezone.utc).isoformat()})
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        try:
            data = self._read_json()
        except Exception:
            self._send(400, {"ok": False, "error": "invalid json body"})
            return
        try:
            if self.path == "/search":
                self._handle_search(data)
            elif self.path == "/gate/dispatch":
                self._handle_dispatch(data)
            elif self.path == "/decision":
                self._handle_decision(data)
            elif self.path == "/approve":
                self._handle_approve(data)
            elif self.path == "/verify":
                self._handle_verify(data)
            elif self.path == "/act":
                self._handle_act(data)
            else:
                self._send(404, {"ok": False, "error": "not found"})
        except Exception as e:
            log("ERROR", self.path, str(e)[:300])
            self._send(500, {"ok": False, "error": str(e)[:300]})

    def _handle_search(self, data):
        seat, slug, query = data.get("seat"), data.get("slug"), data.get("query")
        if not (isinstance(seat, str) and SLUG_RE.match(seat)):
            self._send(400, {"ok": False, "error": "bad seat"}); return
        if not (isinstance(slug, str) and SLUG_RE.match(slug)):
            self._send(400, {"ok": False, "error": "bad slug"}); return
        if not (isinstance(query, str) and 1 <= len(query) <= MAX_QUERY_LEN):
            self._send(400, {"ok": False, "error": "bad query"}); return
        result_text, partial = grep_knowledge(query)
        qh = hashlib.sha256(query.encode()).hexdigest()
        rh = hashlib.sha256(result_text.encode()).hexdigest()
        ts_str = now_ts()
        receipts = []
        for kind in ("memory", "kb"):
            sig = receipt_sig(seat, slug, kind, qh, rh, ts_str)
            sql = ("INSERT INTO public.receipts(seat,kind,slug,query_hash,result_hash,ts,sig) "
                   f"VALUES ('{sql_esc(seat)}','{kind}','{sql_esc(slug)}','{qh}','{rh}',"
                   f"'{ts_str}'::timestamptz,'{sig}') RETURNING id;")
            rid = psql(sql).strip().split("\n")[0]
            receipts.append({"kind": kind, "receipt_id": rid})
        log("SEARCH", seat, slug, "query_hash=", qh[:12], "receipts=", len(receipts), "partial=", partial)
        self._send(200, {"ok": True, "seat": seat, "slug": slug, "query_hash": qh,
                         "result_hash": rh, "ts": ts_str, "partial": partial,
                         "receipts": receipts, "results": result_text})

    def _handle_approve(self, data):
        token, task_slug = data.get("chairman_token"), data.get("task_slug")
        if not (isinstance(task_slug, str) and SLUG_RE.match(task_slug)):
            self._send(400, {"ok": False, "error": "bad task_slug"}); return
        if not isinstance(token, str):
            self._send(400, {"ok": False, "error": "bad chairman_token"}); return
        try:
            with open(TOKEN_FILE, "r") as f:
                stored = f.read().strip()
        except OSError:
            self._send(500, {"ok": False, "error": "token store unreadable"}); return
        if not hmac.compare_digest(token, stored):
            log("APPROVE-REJECTED", task_slug)
            self._send(403, {"ok": False, "error": "chairman_token rejected"}); return
        ts_str = now_ts()
        sig = plan_sig(task_slug, ts_str)
        sql = ("INSERT INTO public.plan_approvals(task_slug,approved_by,ts,sig) "
               f"VALUES ('{sql_esc(task_slug)}','chairman','{ts_str}'::timestamptz,'{sig}') RETURNING plan_id;")
        plan_id = psql(sql).strip().split("\n")[0]
        log("APPROVED", task_slug, "plan_id=", plan_id)
        self._send(200, {"ok": True, "plan_id": plan_id, "task_slug": task_slug, "ts": ts_str})

    def _handle_verify(self, data):
        receipt_ids = data.get("receipt_ids")
        plan_id = data.get("plan_id")
        slug = data.get("slug")
        if not (isinstance(receipt_ids, list) and receipt_ids and
                all(isinstance(x, str) for x in receipt_ids)):
            self._send(400, {"ok": False, "error": "bad receipt_ids"}); return
        if not (isinstance(slug, str) and SLUG_RE.match(slug)):
            self._send(400, {"ok": False, "error": "bad slug"}); return
        ok, reasons, detail = verify_receipts(receipt_ids, plan_id, slug)
        log("VERIFY", slug, "ok=", ok, "reasons=", reasons)
        self._send(200, {"ok": ok, "reasons": reasons, "detail": detail})

    def _handle_dispatch(self, data):
        task_id = data.get("task_id")
        contract_hash = data.get("contract_hash")
        contract = data.get("contract")
        if not (isinstance(task_id, str) and UUID_RE.match(task_id)):
            self._send(400, {"ok": False, "error": "bad task_id (must be uuid)"}); return
        if not isinstance(contract, dict):
            self._send(400, {"ok": False, "error": "bad contract (must be object)"}); return
        if not (isinstance(contract_hash, str) and re.fullmatch(r"[0-9a-f]{64}", contract_hash or "")):
            self._send(400, {"ok": False, "error": "bad contract_hash (must be sha256 hex)"}); return
        if canonical_hash(contract) != contract_hash:
            self._send(400, {"ok": False, "error": "contract_hash mismatch (canonical sha256)"}); return
        task_slug = contract.get("task_id") or task_id
        if not (isinstance(task_slug, str) and SLUG_RE.match(task_slug)):
            self._send(400, {"ok": False, "error": "bad contract.task_id slug"}); return
        mode = gate_mode("dispatch-receipt")
        if mode not in ("off", "log", "enforce"):
            self._send(503, {"ok": False, "error": "dispatch-receipt gate not registered (mode=%r)" % mode}); return
        results = dispatch_gates(task_slug, contract)
        passed, failed, overridden = [], [], []
        for gate_id, ok, detail in results:
            if gate_overridden(gate_id):
                overridden.append(gate_id)
                passed.append(gate_id)
                continue
            gmode = gate_mode(gate_id) or "log"
            if ok:
                passed.append(gate_id)
            elif gmode == "enforce":
                failed.append({"gate": gate_id, "mode": gmode, "detail": detail})
                mark_blocked(gate_id)
            else:
                # log/off mode: record the failure, do not block
                passed.append(gate_id)
                log("DISPATCH-GATE-LOG", task_slug, gate_id, "failed-but-log-mode:", detail[:200])
        # Cutover51 fix (2026-11-10 20:2x): an enforce-mode gate failure must
        # refuse the receipt on its own. The old condition (`mode ==
        # "enforce" and failed`) let enforce failures through whenever the
        # dispatch-receipt master mode was log -- the receipt was issued
        # while last_block_ts was being marked. dispatch-receipt's own mode
        # governs only the DB trigger (off = trigger skips the check).
        if failed:
            ledger_append({"endpoint": "gate/dispatch", "task_id": task_id, "task_slug": task_slug,
                           "mode": mode, "executed": False, "reason": "dispatch gates failed",
                           "failed": failed})
            log("DISPATCH-REFUSED", task_slug, [f["gate"] for f in failed])
            self._send(403, {"ok": False, "error": "dispatch gates failed", "failed": failed,
                             "passed": passed, "overridden": overridden})
            return
        try:
            issue_dispatch_receipt(task_id, contract_hash, passed)
        except Exception as e:
            self._send(500, {"ok": False, "error": "receipt write failed: %s" % str(e)[:200]}); return
        log("DISPATCH-RECEIPT", task_slug, "mode=", mode, "passed=", passed, "overridden=", overridden)
        self._send(200, {"ok": True, "task_id": task_id, "contract_hash": contract_hash,
                         "mode": mode, "gates_passed": passed, "overridden": overridden,
                         "failed_log_mode": [g for g, ok, _ in results if not ok and g in passed]})

    def _handle_decision(self, data):
        seat, slug, text = data.get("seat"), data.get("slug"), data.get("text")
        kb_ids = data.get("kb_ids")
        needs_chairman = data.get("needs_chairman", True)
        if not (isinstance(seat, str) and SLUG_RE.match(seat)):
            self._send(400, {"ok": False, "error": "bad seat"}); return
        if not (isinstance(slug, str) and SLUG_RE.match(slug)):
            self._send(400, {"ok": False, "error": "bad slug"}); return
        if not (isinstance(text, str) and len(text.strip()) >= 10):
            self._send(400, {"ok": False, "error": "bad text (min 10 chars)"}); return
        if not (isinstance(kb_ids, list) and kb_ids and all(isinstance(k, str) and k for k in kb_ids)):
            self._send(400, {"ok": False, "error": "bad kb_ids (non-empty list required)"}); return
        if not isinstance(needs_chairman, bool):
            self._send(400, {"ok": False, "error": "bad needs_chairman"}); return
        try:
            for kb in kb_ids:
                row = psql("SELECT 1 FROM public.kb_index WHERE file_id='%s';" % sql_esc(kb)).strip()
                if not row:
                    self._send(403, {"ok": False, "error": "kb_id not in knowledge base: %s" % kb}); return
            row = psql(
                "SELECT 1 FROM public.receipts WHERE seat='%s' AND kind='kb' AND slug='%s' "
                "AND ts > now() - interval '24 hours';" % (sql_esc(seat), sql_esc(slug))).strip()
            if not row:
                self._send(403, {"ok": False, "error": "no kb receipt for seat+slug in 24h"}); return
            arr = "ARRAY[" + ",".join("'%s'" % sql_esc(k) for k in kb_ids) + "]"
            did = psql(
                "INSERT INTO public.decisions(seat, slug, text, kb_ids, needs_chairman) "
                "VALUES ('%s','%s','%s',%s,%s) RETURNING id;"
                % (sql_esc(seat), sql_esc(slug), sql_esc(text.strip()), arr,
                   "true" if needs_chairman else "false")).strip().split("\n")[0]
        except Exception as e:
            self._send(500, {"ok": False, "error": "decision write failed: %s" % str(e)[:200]}); return
        log("DECISION", seat, slug, "id=", did)
        self._send(200, {"ok": True, "id": did, "seat": seat, "slug": slug})

    def _handle_act(self, data):
        seat, task, action = data.get("seat"), data.get("task"), data.get("action")
        plan_id, receipt_ids = data.get("plan_id"), data.get("receipt_ids")
        base = {"seat": seat, "task": task, "action": action, "plan_id": plan_id,
                "receipt_ids": receipt_ids}
        if not (isinstance(seat, str) and SLUG_RE.match(seat)):
            self._send(400, {"ok": False, "error": "bad seat"}); return
        if not (isinstance(task, str) and SLUG_RE.match(task)):
            self._send(400, {"ok": False, "error": "bad task"}); return
        if action not in ACTION_WHITELIST:
            ledger_append({**base, "mode": read_mode(), "executed": False,
                           "reason": f"action not whitelisted: {action}"})
            self._send(403, {"ok": False, "error": f"action not whitelisted: {action}"}); return
        if not (isinstance(receipt_ids, list) and receipt_ids and
                all(isinstance(x, str) for x in receipt_ids)):
            self._send(400, {"ok": False, "error": "bad receipt_ids"}); return
        try:
            ok, reasons, _detail = verify_receipts(receipt_ids, plan_id, task)
        except Exception as e:
            self._send(500, {"ok": False, "error": f"verify failed: {e}"}); return
        if not ok:
            ledger_append({**base, "mode": read_mode(), "executed": False,
                           "reason": "verify failed", "verify_reasons": reasons})
            self._send(403, {"ok": False, "error": "verify failed", "reasons": reasons}); return
        mode = read_mode()
        if mode == "off":
            ledger_append({**base, "mode": mode, "executed": False, "reason": "gate.mode=off"})
            self._send(403, {"ok": False, "error": "gate.mode=off: action refused"}); return
        if mode == "log":
            ledger_append({**base, "mode": mode, "executed": False, "reason": "gate.mode=log: recorded only"})
            self._send(200, {"ok": True, "mode": "log", "executed": False,
                             "note": "recorded only, not executed"}); return
        if mode != "enforce":
            ledger_append({**base, "mode": mode, "executed": False,
                           "reason": f"unknown gate.mode={mode!r}: fail closed"})
            self._send(403, {"ok": False, "error": f"unknown gate.mode={mode!r}: fail closed"}); return
        script = ACTION_SCRIPTS[action]
        try:
            r = subprocess.run([script, task], capture_output=True, text=True, timeout=120)
            entry = {**base, "mode": mode, "executed": r.returncode == 0,
                     "returncode": r.returncode,
                     "stdout_tail": r.stdout[-500:], "stderr_tail": r.stderr[-500:],
                     "reason": "executed" if r.returncode == 0 else f"action script rc={r.returncode}"}
            ledger_append(entry)
            log("ACT", seat, task, action, "rc=", r.returncode)
            if r.returncode == 0:
                self._send(200, {"ok": True, "mode": "enforce", "executed": True,
                                 "returncode": 0, "stdout_tail": r.stdout[-500:]})
            else:
                self._send(502, {"ok": False, "mode": "enforce", "executed": False,
                                 "returncode": r.returncode, "stderr_tail": r.stderr[-500:]})
        except FileNotFoundError:
            ledger_append({**base, "mode": mode, "executed": False,
                           "reason": f"action script missing: {script}"})
            self._send(502, {"ok": False, "error": f"action script missing: {script}"})
        except subprocess.TimeoutExpired:
            ledger_append({**base, "mode": mode, "executed": False, "reason": "action timed out"})
            self._send(502, {"ok": False, "error": "action timed out"})


def main():
    server = ThreadingHTTPServer((BIND_HOST, BIND_PORT), Handler)
    server.daemon_threads = True
    log(f"gate-svc listening on {BIND_HOST}:{BIND_PORT} (mode={read_mode() or 'unknown'})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

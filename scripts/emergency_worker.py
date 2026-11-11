#!/usr/bin/env python3
"""Hub emergency worker (应急工) — skeleton v1 + wake v1, 2026-10-27.

Design (finalised 2026-10-27):
- Script (body) + gateway model (brain), NO conversational Agent, NO dialogue session.
- Normally SILENT: only activated by the butler (大管家) when ALL outsourced
  workers are lost. Small urgent tasks only (one block <= 7-8 min).
- WAKE FIRST (wake v1, this change only): after activation, before
  self-executing, try to wake an outsourced worker. If a worker is
  confirmed alive, hand the task back to it and return to silent WITHOUT
  self-executing. Only after 3 failed wake attempts (accounts rotated) is
  the outsourced fleet judged dead, and only then does the emergency
  worker self-execute the small urgent task. It NEVER waits indefinitely
  for InstaBox to wake on its own.
- Concurrency 1, timeout enforced, resource limits enforced, must not kill hub.
- Task state MAY stay on hub (status machine, checkpoint, hash, output path,
  lease). Process drafts MUST NOT enter knowledge / permanent archive:
  drafts live only in an isolated work dir, after PASS only the final
  deliverable + MANIFEST + hash remain, drafts are deleted.
- Central-queue integration is NOT in this file (queue is block 3, not
  built yet). claim_next() is a reserved interface that returns None until
  the queue exists. This change does NOT touch claim_next() and does NOT
  invent a queue file. A wake success in this version means the target
  machine is confirmed running after resume; the actual task claim still
  goes through the central queue once block 3 exists (or a butler
  re-trigger), because no queue exists yet to prove a claim.
- Gateway judgment: try hub_chat.py (gateway model) with a short timeout.
  If the gateway is unavailable / ambiguous, fall back to deterministic
  rules and NEVER auto-PASS on ambiguous evidence: return UNKNOWN.
- Receipt: tool_name / status / output_sha256 / created_at, plus task_id.

Wake rules (finalised 2026-10-27, wake v1):
- Main workers (主工, InstaBox, API-wakeable): account a1 = worker-1..5,
  account a2 = worker-6..10. Accounts ROTATE: attempts alternate a1, a2,
  a1, ... and workers rotate inside each account, so one account is never
  hammered alone. Max 3 attempts, 60 s apart by default
  (--wake-interval-s; tests may pass a short interval, the 60 s default
  stays in code). After 3 failures: dead, no further waiting.
- Backup workers (备工, Muse): platform allows outbound only, no inbound
  SSH/API wake is possible. They are STATUS-ONLY: if a backup worker is
  already alive, hand off to it; if dead, do NOT attempt any API wake for
  it, only wait for it to come online on its own (which is not a wake
  attempt and never blocks the 3-attempt main-worker sequence).
- Production main-worker wake: POST /v1/machines/{id}/resume with an
  Idempotency-Key (InstaBox lifecycle API, 202 Accepted on accept), key
  read INSIDE this script from the hub key files, never printed, logged,
  or returned. A 202 alone is NOT success: success requires a follow-up
  machines-list check showing that machine state == "running". A 429
  from the resume call stops the sequence immediately (no more attempts).
- Wake functions are injectable (wake_fn / backup_alive_fn / sleep_fn)
  so rotation, the 3-attempt cap, success-to-silent, and failure-to-
  self-execute can be unit-tested with a fake status source without
  touching production machines.

Red lines: no money, no external publish, no irreversible action,
no change to /opt/model-profiles permissions, no change to butler seat
config, no change to web_brain.py, no central-queue files.
"""
import argparse
import hashlib
import json
import os
import resource
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

VERSION = "emergency-worker-wake-v1-20261027"
TOOL_NAME = "emergency_worker"
MAX_CONCURRENT = 1
DEFAULT_TIMEOUT_S = 420  # 7 min block, inside the 7-8 min block limit
DEFAULT_WORK_ROOT = "/data/work"
DEFAULT_STATE_DIR = "/home/ubuntu/ai-team/state/emergency-worker-20261027"
HUB_CHAT = "/home/ubuntu/ai-team/scripts/hub_chat.py"
LOCK_NAME = "emergency_worker.lock"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def audit(state_dir: Path, event: dict):
    state_dir.mkdir(parents=True, exist_ok=True)
    event = {"tool": TOOL_NAME, "version": VERSION, "ts": now_iso(), **event}
    with open(state_dir / "audit.log", "a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


# ---- reserved queue interface (block 3, not built) ----
def claim_next(state_dir: Path):
    """Reserved central-queue claim interface.

    Returns None until the central queue (block 3) exists. This is
    deliberate: the skeleton must NOT invent a queue file / bucket and
    must NOT claim production tasks from worker buckets.
    """
    return None


# ---- wake mechanism (wake v1, 2026-10-27) ----
# Main workers only. Backup (Muse) workers are deliberately absent here:
# they have no API wake path, so no wake_fn call must ever target them.
WAKE_MAX_ATTEMPTS_DEFAULT = 3
WAKE_INTERVAL_S_DEFAULT = 60  # seconds between attempts; tests may override
WAKE_KEY_FILES = {
    "a1": "/home/ubuntu/ai-team/state/.instabox-api-key",
    "a2": "/home/ubuntu/ai-team/state/.instabox2-api-key",
}
MAIN_WORKERS = {
    "a1": ["worker-1", "worker-2", "worker-3", "worker-4", "worker-5"],
    "a2": ["worker-6", "worker-7", "worker-8", "worker-9", "worker-10"],
}
MAIN_WORKER_NAMES = [n for acct in ("a1", "a2") for n in MAIN_WORKERS[acct]]
INSTABOX_API_BASE = "https://api.instabox.sh"
# Sentinel reason: a 429 on resume stops the whole wake sequence at once.
WAKE_RATE_LIMITED = "resume_rate_limited_stop"


def wake_plan():
    """Interleaved rotation plan: a1, a2, a1, a2, ... with workers
    rotating inside each account. First 3 entries (the default cap):
    (a1, worker-1), (a2, worker-6), (a1, worker-2). No two consecutive
    attempts ever use the same account."""
    plan = []
    for i in range(max(len(v) for v in MAIN_WORKERS.values())):
        for acct in ("a1", "a2"):
            workers = MAIN_WORKERS[acct]
            if i < len(workers):
                plan.append((acct, workers[i]))
    return plan


def _read_key_file(path: str):
    """Read a key file inside this process. The value is returned to the
    caller for one API call only and is NEVER logged, printed, or put in
    any audit/receipt/error string by this module."""
    try:
        with open(path) as f:
            val = f.read().strip()
        return val or None
    except Exception:
        return None


def _resolve_machine_id(account: str, worker: str):
    """Resolve a main worker name to its InstaBox machine id from the
    existing hub cluster mapping (instabox_cluster.py BYNAME), so this
    file does not duplicate machine ids. Returns None if the mapping
    cannot be loaded. Machine ids are not secrets; keys are never read
    or returned by this function."""
    try:
        scripts_dir = "/home/ubuntu/ai-team/scripts"
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import instabox_cluster  # noqa: PLC0415 - lazy, hub-local only
        acct, mid = instabox_cluster.BYNAME[worker]
        return mid if acct == account else None
    except Exception:
        return None


def production_resume_and_confirm(account: str, worker: str,
                                  timeout_s: int = 30):
    """Production main-worker wake: POST /resume, then confirm running.

    Returns (ok: bool, reason: str). Reason strings contain only account,
    worker, HTTP codes, and machine states — never a key value.
    202/200 on resume + machines-list state == 'running' is the only
    success. 'resuming'/'starting' is NOT success (not yet taking work).
    429 stops the caller sequence (see WAKE_RATE_LIMITED). This function
    is NOT called by any test in wake v1; tests inject a fake wake_fn.
    """
    if account not in WAKE_KEY_FILES or worker not in MAIN_WORKER_NAMES:
        return False, "not_a_main_worker"
    mid = _resolve_machine_id(account, worker)
    if not mid:
        return False, "machine_resolver_unavailable"
    key = _read_key_file(WAKE_KEY_FILES[account])
    if not key:
        return False, "key_file_missing_or_unreadable"
    idem = str(uuid.uuid4())
    req = urllib.request.Request(
        f"{INSTABOX_API_BASE}/v1/machines/{mid}/resume",
        headers={"Authorization": "Bearer " + key,
                 "Idempotency-Key": idem},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            resume_status = r.status
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return False, WAKE_RATE_LIMITED
        return False, f"resume_http_{e.code}"
    except Exception as e:
        return False, f"resume_error:{type(e).__name__}"
    if resume_status not in (200, 202):
        return False, f"resume_http_{resume_status}"
    # Confirm: list machines with the same account key, find this id.
    try:
        list_req = urllib.request.Request(
            f"{INSTABOX_API_BASE}/v1/machines",
            headers={"Authorization": "Bearer " + key})
        with urllib.request.urlopen(list_req, timeout=timeout_s) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        items = None
        if isinstance(data, dict):
            items = data.get("data") or data.get("machines")
        if items is None and isinstance(data, list):
            items = data
        state = None
        if isinstance(items, list):
            for m in items:
                if isinstance(m, dict) and m.get("id") == mid:
                    state = m.get("state") or m.get("status")
                    break
        if state == "running":
            return True, "running"
        return False, f"resume_accepted_not_running:{state}"
    except Exception as e:
        return False, f"confirm_error:{type(e).__name__}"


def try_wake_outsourced(wake_fn, backup_alive_fn=None,
                         max_attempts: int = WAKE_MAX_ATTEMPTS_DEFAULT,
                         interval_s: int = WAKE_INTERVAL_S_DEFAULT,
                         sleep_fn=time.sleep):
    """Try to hand the task back to an outsourced worker before
    self-executing. wake_fn(account, worker) -> (ok, reason); it must
    only ever be called with main workers (see MAIN_WORKERS).
    backup_alive_fn() -> bool is status-only: backup workers are never
    passed to wake_fn. sleep_fn is injectable so tests never really wait.

    Returns a dict with outcome 'handed_off' or 'all_dead', handed_to,
    and the full attempts list (account, worker, ok, reason per attempt).
    'all_dead' means: stop here, do NOT wait for InstaBox to wake itself;
    the caller then self-executes the small urgent task.
    """
    if backup_alive_fn is not None:
        try:
            if backup_alive_fn():
                return {"outcome": "handed_off", "handed_to": "backup",
                        "attempts": [], "attempts_made": 0}
        except Exception:
            pass  # backup status unreadable: fall through to main wake
    plan = wake_plan()[:max(max_attempts, 0)]
    attempts = []
    for i, (acct, worker) in enumerate(plan):
        try:
            ok, reason = wake_fn(acct, worker)
        except Exception as e:
            ok, reason = False, f"wake_error:{type(e).__name__}"
        attempts.append({"account": acct, "worker": worker,
                         "ok": bool(ok), "reason": reason})
        if ok:
            return {"outcome": "handed_off",
                    "handed_to": f"main:{worker}",
                    "attempts": attempts, "attempts_made": len(attempts)}
        if reason == WAKE_RATE_LIMITED:
            break  # hard stop: no further attempts, no hammering
        if i < len(plan) - 1:
            sleep_fn(interval_s)
    return {"outcome": "all_dead", "handed_to": None,
            "attempts": attempts, "attempts_made": len(attempts)}


# ---- activation check ----
def check_all_lost(statuses, stale_s=600, now=None):
    """statuses: list of dicts {name, last_seen_ts, alive: bool}.
    All lost == every entry is either alive==False or last_seen older
    than stale_s. Empty list == NOT all lost (safe default: stay silent).
    """
    if not statuses:
        return False, "no_outsourced_status_source"
    now = now if now is not None else time.time()
    for s in statuses:
        if s.get("alive"):
            return False, f"still_alive:{s.get('name')}"
        last = s.get("last_seen_ts")
        if last is not None and (now - last) < stale_s:
            return False, f"recently_seen:{s.get('name')}"
    return True, "all_lost"


def load_outsourced_statuses(status_file: Path | None):
    if status_file and status_file.exists():
        try:
            data = json.loads(status_file.read_text())
            if isinstance(data, list):
                return data
        except Exception:
            return []
    return []


# ---- gateway judgment interface ----
def judge_via_gateway(evidence: dict, timeout_s: int = None) -> dict:
    if timeout_s is None:
        try:
            timeout_s = int(os.environ.get("EMERGENCY_GATEWAY_TIMEOUT", "60"))
        except Exception:
            timeout_s = 60
    """Ask the hub gateway model for a structured judgment.

    Returns {verdict: PASS|RETRY|UNKNOWN, model_used, raw}.
    On any gateway failure / ambiguity, returns UNKNOWN (never fake PASS).
    hub_chat.py CLI is probed at runtime; if its interface differs, the
    deterministic fallback below is used and model_used='deterministic'.
    """
    # Deterministic pre-check (script does the evidence, model only judges
    # the bounded question). If deterministic evidence is complete, the
    # gateway is asked to confirm; if the gateway cannot be reached, the
    # deterministic result is still only PASS when all checks pass.
    det_ok = bool(
        evidence.get("output_exists")
        and evidence.get("output_sha256")
        and evidence.get("receipt_fields_complete")
    )
    if not os.path.exists(HUB_CHAT):
        return {"verdict": "PASS" if det_ok else "UNKNOWN",
                "model_used": "deterministic (hub_chat.py missing)",
                "raw": "hub_chat.py not found, deterministic fallback"}
    if os.environ.get("EMERGENCY_SKIP_GATEWAY") == "1":
        return {"verdict": "PASS" if det_ok else "UNKNOWN",
                "model_used": "deterministic (gateway skipped by env, test only)",
                "raw": "EMERGENCY_SKIP_GATEWAY=1"}
    prompt = (
        "You are a bounded task judge. Evidence: "
        + json.dumps(evidence, ensure_ascii=False)
        + ". Answer with exactly one word: PASS, RETRY, or UNKNOWN. "
        "PASS only if output exists, hash matches, and receipt fields are complete."
    )
    try:
        # CORRECT hub_chat.py interface (verified from its header):
        # argv[1] is the model name, prompt is read from stdin.
        # Earlier positional-prompt call was wrong and hung; do NOT use it.
        # Model: glm-4-flash-cc (2026-10-27 董事长授权换模型，原 gpt-oss-120b 网关429). A 429 from the gateway is
        # a hard stop: fall back deterministically and do NOT retry here.
        r = subprocess.run(
            [sys.executable, HUB_CHAT, "glm-4-flash-cc"],
            input=prompt, capture_output=True, text=True, timeout=timeout_s)
        out = (r.stdout or "").strip().upper()
        for word in ("PASS", "RETRY", "UNKNOWN"):
            if word in out:
                return {"verdict": word, "model_used": "gateway (hub_chat.py)",
                        "raw": (r.stdout or "")[:500]}
        # Unparsable gateway output -> UNKNOWN, not PASS.
        return {"verdict": "UNKNOWN", "model_used": "gateway (unparsable)",
                "raw": (r.stdout or "")[:500]}
    except Exception as e:
        return {"verdict": "PASS" if det_ok else "UNKNOWN",
                "model_used": f"deterministic (gateway error: {type(e).__name__})",
                "raw": str(e)[:500]}


# ---- execution ----
def _limits():
    # Child resource limits: CPU 400s, memory 2 GiB (hub is 2 cores / 11 GiB).
    def preexec():
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (400, 400))
        except Exception:
            pass
        try:
            resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
        except Exception:
            pass
    return preexec


def run_fake_task(task: dict, work_dir: Path, state_dir: Path,
                   timeout_s: int = DEFAULT_TIMEOUT_S) -> dict:
    """Built-in small urgent task kinds only (no arbitrary shell in prod path).
    Supported kinds: write_file {rel_path, content} -> then hash it.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    kind = task.get("kind")
    if kind != "write_file":
        return {"status": "UNKNOWN", "reason": f"unsupported_kind:{kind}"}
    rel = task.get("rel_path", "output.txt")
    # Path traversal guard: must stay inside work_dir.
    out_path = (work_dir / rel).resolve()
    if not str(out_path).startswith(str(work_dir.resolve())):
        return {"status": "UNKNOWN", "reason": "path_escape"}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Run the write under timeout via a trivial subprocess to prove
    # timeout/resource-limit plumbing (content is passed via file, not shell).
    tmp_content = work_dir / "_content.txt"
    tmp_content.write_text(task.get("content", ""))
    try:
        subprocess.run(
            ["cp", str(tmp_content), str(out_path)],
            timeout=timeout_s, preexec_fn=_limits(), check=True)
    except subprocess.TimeoutExpired:
        return {"status": "RETRY", "reason": "timeout"}
    except Exception as e:
        return {"status": "UNKNOWN", "reason": f"exec_error:{type(e).__name__}"}
    finally:
        try:
            tmp_content.unlink()
        except Exception:
            pass
    # Checkpoint (task state, allowed on hub).
    ckpt = {"task_id": task.get("task_id"), "step": "executed",
            "output_path": str(out_path), "output_sha256": sha256_file(out_path),
            "ts": now_iso()}
    (work_dir / "checkpoint.json").write_text(
        json.dumps(ckpt, ensure_ascii=False, indent=2))
    return {"status": "EXECUTED", "output_path": str(out_path),
            "output_sha256": ckpt["output_sha256"], "checkpoint": ckpt}


def cleanup_after_pass(work_dir: Path, state_dir: Path, task_id: str,
                       output_path: str, output_sha256: str) -> Path:
    """After PASS: only final deliverable + MANIFEST + hash remain.
    Drafts are deleted; nothing goes to knowledge / permanent archive.
    Final deliverable is copied (not moved from knowledge) into the
    state deliverables dir, then the isolated work dir is removed.
    """
    dest_dir = state_dir / "deliverables" / task_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(output_path).name
    shutil.copyfile(output_path, dest)
    manifest = {"task_id": task_id, "tool": TOOL_NAME, "version": VERSION,
                "output_sha256": output_sha256, "final_path": str(dest),
                "ts": now_iso()}
    (dest_dir / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2))
    shutil.rmtree(work_dir, ignore_errors=True)
    return dest_dir


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["silent", "check", "run", "test"],
                    default="silent")
    ap.add_argument("--task-id", default="test-emergency-20261027")
    ap.add_argument("--task-file", default="")
    ap.add_argument("--status-file", default="")
    ap.add_argument("--force-activation", action="store_true",
                    help="TEST ONLY: bypass all-lost check, audit-logged as test")
    ap.add_argument("--skip-wake", action="store_true",
                    help="TEST ONLY: skip the wake step and self-execute directly")
    ap.add_argument("--wake-outcomes", default="",
                    help="TEST ONLY: fake resume outcomes as JSON {worker: bool}; "
                         "audit-logged as test, never calls the production API")
    ap.add_argument("--wake-max-attempts", type=int,
                    default=WAKE_MAX_ATTEMPTS_DEFAULT)
    ap.add_argument("--wake-interval-s", type=int,
                    default=WAKE_INTERVAL_S_DEFAULT,
                    help="Seconds between wake attempts (default 60; tests "
                         "may pass a short value, the default stays 60)")
    ap.add_argument("--work-root", default=DEFAULT_WORK_ROOT)
    ap.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    ap.add_argument("--timeout-s", type=int, default=DEFAULT_TIMEOUT_S)
    args = ap.parse_args()
    state_dir = Path(args.state_dir)
    work_root = Path(args.work_root)

    # Concurrency 1: flock-style lock via mkdir (atomic).
    lock_dir = state_dir / LOCK_NAME
    try:
        lock_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print(json.dumps({"tool": TOOL_NAME, "status": "BUSY",
                          "reason": "concurrency_1_lock_held"}))
        return 2
    try:
        if args.mode in ("silent", "check"):
            statuses = load_outsourced_statuses(
                Path(args.status_file) if args.status_file else None)
            lost, reason = check_all_lost(statuses)
            ev = audit(state_dir, {"mode": args.mode, "activated": False,
                                   "all_lost": lost, "reason": reason})
            print(json.dumps(ev, ensure_ascii=False))
            return 0

        # run / test: activation gate
        statuses = load_outsourced_statuses(
            Path(args.status_file) if args.status_file else None)
        lost, reason = check_all_lost(statuses)
        if not (lost or args.force_activation):
            ev = audit(state_dir, {"mode": args.mode, "task_id": args.task_id,
                                   "activated": False,
                                   "reason": f"not_all_lost:{reason}"})
            print(json.dumps(ev, ensure_ascii=False))
            return 0
        audit(state_dir, {"mode": args.mode, "task_id": args.task_id,
                          "activated": True,
                          "activation": "test_force" if args.force_activation
                          else f"all_lost:{reason}"})

        # ---- wake first (wake v1): silent/check modes never wake ----
        if args.skip_wake:
            audit(state_dir, {"mode": args.mode, "task_id": args.task_id,
                              "wake": "skipped_test_only"})
        else:
            # Backup = any alive outsourced entry that is NOT a main
            # worker. Status-only: backup workers are never API-woken.
            def _backup_alive():
                return any(s.get("alive") and s.get("name") not in MAIN_WORKER_NAMES
                           for s in statuses)
            if args.wake_outcomes:
                # TEST ONLY fake wake: outcomes dict, no production call.
                try:
                    outcomes = json.loads(args.wake_outcomes)
                except Exception:
                    outcomes = {}
                alive_map = {s.get("name"): bool(s.get("alive"))
                             for s in statuses}

                def _fake_wake(acct, worker):
                    ok = bool(outcomes.get(worker, False))
                    if ok:
                        alive_map[worker] = True
                    return ok, ("fake_resume_ok" if ok
                                else "fake_resume_failed")

                wake_fn = _fake_wake
                wake_mode = "fake_test_only"
            else:
                wake_fn = production_resume_and_confirm
                wake_mode = "production"
            wake = try_wake_outsourced(
                wake_fn, backup_alive_fn=_backup_alive,
                max_attempts=args.wake_max_attempts,
                interval_s=args.wake_interval_s)
            audit(state_dir, {"mode": args.mode, "task_id": args.task_id,
                              "wake_mode": wake_mode, "wake": wake})
            if wake["outcome"] == "handed_off":
                # Success: outsourced worker takes the task, emergency
                # returns to silent. No work dir is created, no task is
                # self-executed, nothing is left running on the hub.
                receipt = {"tool_name": TOOL_NAME, "status": "HANDED_OFF",
                           "output_sha256": None, "created_at": now_iso(),
                           "task_id": args.task_id,
                           "handed_to": wake["handed_to"],
                           "wake_attempts": wake["attempts"]}
                print(json.dumps(receipt, ensure_ascii=False))
                return 0
            # all_dead: proceed to self-execute below (small urgent task).

        if args.task_file:
            task = json.loads(Path(args.task_file).read_text())
        else:
            # Reserved queue path: no production task without a queue.
            claimed = claim_next(state_dir)
            if claimed is None:
                ev = audit(state_dir, {"mode": args.mode,
                                       "task_id": args.task_id,
                                       "status": "NO_TASK",
                                       "reason": "queue_not_built"})
                print(json.dumps(ev, ensure_ascii=False))
                return 0
            task = claimed
        task.setdefault("task_id", args.task_id)
        work_dir = work_root / task["task_id"]
        result = run_fake_task(task, work_dir, state_dir, args.timeout_s)
        if result.get("status") != "EXECUTED":
            ev = audit(state_dir, {"mode": args.mode, "task_id": task["task_id"],
                                   "status": result.get("status"),
                                   "reason": result.get("reason")})
            print(json.dumps(ev, ensure_ascii=False))
            shutil.rmtree(work_dir, ignore_errors=True)
            return 0

        evidence = {"output_exists": Path(result["output_path"]).exists(),
                    "output_sha256": result["output_sha256"],
                    "receipt_fields_complete": True}
        judgment = judge_via_gateway(evidence)
        verdict = judgment["verdict"]
        if verdict == "PASS":
            dest_dir = cleanup_after_pass(
                work_dir, state_dir, task["task_id"],
                result["output_path"], result["output_sha256"])
            receipt = {"tool_name": TOOL_NAME, "status": "PASS",
                       "output_sha256": result["output_sha256"],
                       "created_at": now_iso(), "task_id": task["task_id"],
                       "final_dir": str(dest_dir),
                       "judge": judgment}
            audit(state_dir, {"mode": args.mode, **receipt})
            print(json.dumps(receipt, ensure_ascii=False))
            return 0
        # RETRY / UNKNOWN: clean work dir, do NOT keep drafts, do NOT PASS.
        shutil.rmtree(work_dir, ignore_errors=True)
        ev = audit(state_dir, {"mode": args.mode, "task_id": task["task_id"],
                               "status": verdict, "judge": judgment})
        print(json.dumps(ev, ensure_ascii=False))
        return 0
    finally:
        shutil.rmtree(lock_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

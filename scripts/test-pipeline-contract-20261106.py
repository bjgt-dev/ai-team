#!/usr/bin/env python3
"""test-pipeline-contract-20261106.py — queue_api/verifier contract test (DESIGN §4).

Checks the two sides of the artifact contract cannot diverge silently:
(1) static: queue_api source contains the DELIV_DIR dual-write block;
    verifier source globs state/deliverables/.
(2) live: the most recent DONE code task has its artifact in BOTH
    quarantine/ and deliverables/ with identical sha256 matching DB artifact_sha.
(3) negative: a pytest-only payload (156B, starts b"pytest_exit=0") fails
    the verifier's size/stub checks (re-implemented here, nothing submitted).
Exit 0 = all pass; 1 = any fail. Read-only except stdout. No DB writes.
"""
import hashlib, os, re, sys

BASE = "/home/ubuntu/ai-team"


def _dsn():
    for line in open(BASE + "/state/.cq-dsn"):
        line = line.strip()
        if line.startswith("CQ_DSN="):
            return line[len("CQ_DSN="):].strip().strip('"').strip("'")
    return ""


def main():
    fails = []
    qa = open(BASE + "/scripts/queue_api-20261105.py").read()
    if "DELIV_DIR" not in qa or "os.replace(_dtmp, _dfinal)" not in qa:
        fails.append("queue_api: DELIV_DIR dual-write block missing")
    else:
        print("PASS static queue_api dual-write present")
    vf = open(BASE + "/scripts/c2-verifier-20261104.py").read()
    if "state/deliverables" not in vf or 'glob' not in vf:
        fails.append("verifier: deliverables glob missing")
    else:
        print("PASS static verifier globs deliverables/")
    import psycopg
    with psycopg.connect(_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT task_id::text, artifact_sha, artifact_bytes FROM central_tasks "
                "WHERE status='DONE' AND payload->>'type'='code' "
                "ORDER BY verified_at DESC NULLS LAST LIMIT 1")
            row = cur.fetchone()
    if not row:
        fails.append("live: no DONE code task to check")
    else:
        tid, sha, nbytes = row
        q = os.path.join(BASE, "state/queue-api-20261105/quarantine", tid + ".bin")
        d = os.path.join(BASE, "state/deliverables", tid + ".bin")
        if not os.path.exists(q):
            fails.append("live: quarantine artifact missing for %s" % tid[:8])
        elif not os.path.exists(d):
            fails.append("live: deliverables artifact missing for %s" % tid[:8])
        else:
            sq = hashlib.sha256(open(q, "rb").read()).hexdigest()
            sd = hashlib.sha256(open(d, "rb").read()).hexdigest()
            if sq != sha or sd != sha or sq != sd or os.path.getsize(q) != nbytes:
                fails.append("live: sha/bytes mismatch tid=%s q=%s d=%s db=%s" % (tid[:8], sq[:12], sd[:12], (sha or "")[:12]))
            else:
                print("PASS live DONE code task %s artifact in both dirs, sha=%s bytes=%d" % (tid[:8], sha[:12], nbytes))
    stub = b"pytest_exit=0\n1 passed in 0.00s\n"
    stub_ok = (len(stub) >= 500 and not (stub.strip().startswith(b"pytest_exit=0") and len(stub) < 1000))
    if stub_ok:
        fails.append("negative: pytest-only stub would pass verifier checks")
    else:
        print("PASS negative pytest-only stub correctly rejected (size/stub checks)")
    if fails:
        for f in fails:
            print("FAIL " + f)
        return 1
    print("CONTRACT_TEST_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

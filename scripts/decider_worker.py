#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

BASE = "/home/ubuntu/ai-team"
CONTRACTS = os.path.join(BASE, "contracts")
PENDING = os.path.join(CONTRACTS, "pending")
CLAIMED = os.path.join(CONTRACTS, "claimed", "decider")
DONE = os.path.join(CONTRACTS, "done")
QUARANTINE = os.path.join(CONTRACTS, "quarantine")
INBOX = os.path.join(BASE, "inbox")
VERIFY = os.path.join(BASE, "scripts", "verify.py")
LOG = os.path.join(BASE, "state", "decider.log")
HIGH_RISK = {"external_send", "publish", "delete", "infra_change", "payment", "order", "irreversible"}


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def log(message):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{now()} {message}\n")


def candidate(path):
    if not path.endswith(".json"):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            c = json.load(f)
    except Exception:
        return False
    tid = str(c.get("task_id", ""))
    did = str(c.get("decision_id", ""))
    return tid.startswith("decision-phasee-") and did.lower().startswith("phasee-") and bool(c.get("acceptance_criteria"))


def scan():
    if not os.path.isdir(PENDING):
        return None
    for name in sorted(os.listdir(PENDING)):
        path = os.path.join(PENDING, name)
        if os.path.isfile(path) and candidate(path):
            return path
    return None


def claim(path):
    os.makedirs(CLAIMED, exist_ok=True)
    name = os.path.basename(path)
    dest = os.path.join(CLAIMED, name)
    if os.path.exists(dest):
        raise RuntimeError(f"already claimed: {dest}")
    os.replace(path, dest)
    log(f"claimed {name} -> {dest}")
    return dest


def decision_record(contract, contract_path):
    did = contract.get("decision_id")
    package_path = contract.get("decision_package_path")
    basis = []
    checks = []

    def check(name, ok, detail):
        checks.append({"check": name, "result": "PASS" if ok else "FAIL", "detail": detail})

    package = None
    if package_path and os.path.isfile(package_path):
        try:
            with open(package_path, encoding="utf-8") as f:
                package = json.load(f)
        except Exception as exc:
            check("decision package readable", False, str(exc))
    else:
        check("decision package readable", False, f"missing: {package_path}")

    if package is not None:
        check("decision id match", package.get("decision_id") == did, f"{package.get('decision_id')} == {did}")
        goal = package.get("goal") or package.get("objective") or package.get("title")
        check("decision goal present", bool(goal), str(goal)[:200])
        pkg_nc = {n.get("id") for n in package.get("negative_constraints", []) if isinstance(n, dict)}
        con_nc = {n.get("id") for n in contract.get("negative_constraints", []) if isinstance(n, dict)}
        check("negative constraints covered", pkg_nc <= con_nc, f"package={sorted(pkg_nc)} contract={sorted(con_nc)}")
        declared = set(package.get("side_effects") or [])
        risky = declared & HIGH_RISK
        check("no unapproved high-risk side effects", not risky or package.get("requires_approval") is True, f"side_effects={sorted(declared)} risky={sorted(risky)}")
        approval_ok = package.get("requires_approval") is not True
        if package.get("requires_approval") is True:
            approval = os.path.join(CONTRACTS, "approvals", f"{did}.json")
            approval_ok = os.path.isfile(approval)
            check("approval record for high-risk decision", approval_ok, approval)
        else:
            check("approval required", True, "requires_approval=false")
    else:
        goal = None
        declared = set()
        risky = set()
        approval_ok = False

    target = str(contract.get("output_file", ""))
    check("output confined to artifacts", target.startswith("artifacts/") and ".." not in target, target)
    ac = contract.get("acceptance_criteria") or []
    check("acceptance has file_exists", any(c.get("type") == "file_exists" for c in ac), json.dumps(ac, ensure_ascii=False))
    sizes = [int(c.get("expected", 0)) for c in ac if c.get("type") == "file_min_size"]
    check("acceptance size sane", all(0 < s <= 100000 for s in sizes) and bool(sizes), str(sizes))

    failed = [c for c in checks if c["result"] == "FAIL"]
    conclusion = "APPROVE" if not failed else "REVISE"
    basis = [f"{c['result']} {c['check']}: {c['detail']}" for c in checks]
    if failed:
        basis.append("驳回理由：" + "；".join(f"{c['check']}（{c['detail']}）" for c in failed))

    return {
        "decision_id": did,
        "task_id": contract.get("task_id"),
        "conclusion": conclusion,
        "basis": basis,
        "checks": checks,
        "source_contract": contract_path,
        "decision_package_path": package_path,
        "decision_package_sha256": sha256_file(package_path) if package_path and os.path.isfile(package_path) else None,
        "record_path": f"inbox/{contract['task_id']}/{target}",
        "created_at": now(),
        "decider": "decider-mvp-20261023",
    }


def render(record):
    lines = [
        "# 决策记录",
        "",
        f"- decision_id：{record['decision_id']}",
        f"- task_id：{record['task_id']}",
        f"- 结论：{record['conclusion']}",
        f"- 决策包：{record['decision_package_path']}",
        f"- 决策包 sha256：{record['decision_package_sha256']}",
        f"- 落盘路径：{record['record_path']}",
        f"- 生成时间：{record['created_at']}",
        "",
        "## 依据",
        "",
    ]
    lines += [f"- {b}" for b in record["basis"]]
    lines += [
        "",
        "## 口径",
        "",
        "本记录由最小 decider 对真实历史 decision-phaseE 决策包回放生成；不执行原决策任务，不新增目标、约束或副作用。",
        "结论只用于判定决策包能否进入拆解/执行链，不替代董事长审批。",
        "",
    ]
    return "\n".join(lines)


def write_bundle(contract, record, contract_path):
    task_id = contract["task_id"]
    bundle = os.path.join(INBOX, task_id)
    os.makedirs(os.path.join(bundle, "artifacts"), exist_ok=True)
    md_path = os.path.join(bundle, contract["output_file"])
    json_path = os.path.join(bundle, "artifacts", "decision-record.json")
    md = render(record)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
        f.write("\n")
    ts = now()
    files = []
    for rel in (contract["output_file"], "artifacts/decision-record.json"):
        p = os.path.join(bundle, rel)
        files.append({"path": rel, "sha256": sha256_file(p), "size": os.path.getsize(p)})
    manifest = {"task_id": task_id, "files": files, "created_at": ts}
    with open(os.path.join(bundle, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")
    receipt = {
        "tool_name": "write_file",
        "status": "ok",
        "output_sha256": files[0]["sha256"],
        "created_at": ts,
        "args": {"path": contract["output_file"]},
    }
    with open(os.path.join(bundle, "receipts.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(receipt, ensure_ascii=False) + "\n")
    checks = []
    for crit in contract.get("acceptance_criteria", []):
        target = crit.get("target")
        p = os.path.join(bundle, target)
        if crit.get("type") == "file_exists":
            checks.append({**crit, "actual": os.path.exists(p), "pass": os.path.exists(p)})
        elif crit.get("type") == "file_min_size":
            actual = os.path.getsize(p) if os.path.exists(p) else 0
            checks.append({**crit, "actual": actual, "pass": actual >= int(crit.get("expected", 0))})
    with open(os.path.join(bundle, "acceptance_self_check.json"), "w", encoding="utf-8") as f:
        json.dump({"task_id": task_id, "checks": checks, "all_pass": all(c.get("pass") for c in checks)}, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return bundle


def verify_bundle(bundle, contract_path):
    proc = subprocess.run([sys.executable, VERIFY, bundle, contract_path], capture_output=True, text=True)
    out = proc.stdout.strip()
    try:
        verdict = json.loads(out).get("verdict")
    except Exception:
        verdict = "REJECT"
    with open(os.path.join(bundle, "verify.json"), "w", encoding="utf-8") as f:
        f.write(out + "\n")
    return proc.returncode, verdict, out


def finish(contract_path, task_id, verdict, verify_out):
    if verdict == "PASS":
        os.makedirs(DONE, exist_ok=True)
        dest = os.path.join(DONE, os.path.basename(contract_path))
        os.replace(contract_path, dest)
        with open(os.path.join(DONE, f"{task_id}.verify.json"), "w", encoding="utf-8") as f:
            f.write(verify_out + "\n")
        log(f"verified PASS -> {dest}")
    else:
        qdir = os.path.join(QUARANTINE, f"decider-{task_id}")
        os.makedirs(qdir, exist_ok=True)
        dest = os.path.join(qdir, os.path.basename(contract_path))
        os.replace(contract_path, dest)
        log(f"verified {verdict} -> {dest}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--contract")
    args = ap.parse_args()
    path = args.contract or scan()
    if not path:
        print("NO_PENDING_DECISION")
        return 0
    contract_path = claim(path)
    with open(contract_path, encoding="utf-8") as f:
        contract = json.load(f)
    record = decision_record(contract, contract_path)
    bundle = write_bundle(contract, record, contract_path)
    rc, verdict, out = verify_bundle(bundle, contract_path)
    finish(contract_path, contract["task_id"], verdict, out)
    print(json.dumps({"task_id": contract["task_id"], "decision": record["conclusion"], "verdict": verdict, "bundle": bundle, "contract": contract_path}, ensure_ascii=False))
    return 0 if verdict == "PASS" else rc


if __name__ == "__main__":
    sys.exit(main())

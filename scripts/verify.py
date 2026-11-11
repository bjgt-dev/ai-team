#!/usr/bin/env python3
"""
Independent verifier for AI team evidence bundles.
Zero-LLM gate: receipt cross-check + code-decidable acceptance criteria.
Never reads executor free-text summary.
"""
import json, hashlib, subprocess, sys, os
from pathlib import Path

def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)

def check_manifest(bundle: Path):
    manifest_p = bundle / "manifest.json"
    if not manifest_p.exists():
        return ["manifest.json missing"]
    manifest = load_json(manifest_p)
    errors = []
    for item in manifest.get("files", []):
        rel = item["path"]
        fp = bundle / rel
        if not fp.exists():
            errors.append("manifest file missing: " + rel)
            continue
        actual = sha256_file(fp)
        if actual != item["sha256"]:
            errors.append("sha256 mismatch: %s (declared %s, actual %s)" % (rel, item["sha256"][:12], actual[:12]))
    return errors

def check_receipts(bundle: Path, contract: dict):
    receipts_p = bundle / "receipts.jsonl"
    if not receipts_p.exists():
        return ["receipts.jsonl missing"]
    receipts = []
    for line in receipts_p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            receipts.append(json.loads(line))
        except json.JSONDecodeError:
            return ["malformed receipt line: " + line[:80]]
    errors = []
    # Timestamp anti-forgery: contract must have created_at, receipts must not predate it
    contract_ts = contract.get("created_at") or contract.get("timestamp")
    if not contract_ts:
        errors.append("contract missing created_at timestamp")
    else:
        def _parse_ts(v):
            from datetime import datetime, timezone
            try:
                d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            except ValueError:
                return None
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        c_ts = _parse_ts(contract_ts)
        for i, r in enumerate(receipts):
            r_ts = r.get("created_at") or r.get("ts")
            r_parsed = _parse_ts(r_ts) if r_ts else None
            if r_parsed and c_ts and r_parsed < c_ts:
                errors.append("receipt[%d] predates contract (forgery/reuse): %s < %s" % (i, r_ts, contract_ts))
    required = {"tool_name", "status", "output_sha256", "created_at"}
    for i, r in enumerate(receipts):
        missing = required - set(r)
        if missing:
            errors.append("receipt[%d] missing fields: %s" % (i, sorted(missing)))
    claimed_paths = set()
    for crit in contract.get("acceptance_criteria", []):
        if crit.get("type") == "file_exists":
            if crit.get("target"):
                claimed_paths.add(crit["target"])
            else:
                errors.append("criterion file_exists missing target")
    receipt_write_paths = {
        r.get("args", {}).get("path") or r.get("path")
        for r in receipts
        if r.get("tool_name") in ("write_file", "write", "edit", "create_file")
    }
    for p in claimed_paths:
        if not any(p in str(rp) for rp in receipt_write_paths if rp):
            errors.append("acceptance requires %s but no write receipt found" % p)
    return errors

def check_criteria(bundle: Path, contract: dict):
    results, failed = [], []
    bundle_resolved = bundle.resolve()
    for crit in contract.get("acceptance_criteria", []):
        ctype = crit["type"]
        target = crit.get("target", "")
        expected = crit.get("expected")
        result = {"criterion": crit, "result": "UNVERIFIED", "evidence": None}
        fp = bundle / target if not os.path.isabs(target) else Path(target)
        # Path security: resolved target must stay inside bundle (no .. traversal, no absolute escape)
        try:
            fp_resolved = fp.resolve()
        except Exception:
            fp_resolved = fp
        inside = (fp_resolved == bundle_resolved
                  or str(fp_resolved).startswith(str(bundle_resolved) + os.sep))
        if not inside:
            result.update(result="FAIL", evidence="path escapes bundle: " + target)
            results.append(result)
            failed.append(result["criterion"])
            continue
        fp = fp_resolved
        if ctype == "file_exists":
            ok = fp.exists()
            result.update(result="PASS" if ok else "FAIL", evidence=str(fp))
        elif ctype == "file_min_size":
            ok = fp.exists() and fp.stat().st_size >= int(expected)
            result.update(result="PASS" if ok else "FAIL",
                          evidence="size=%s" % (fp.stat().st_size if fp.exists() else "missing"))
        elif ctype == "file_contains":
            ok = fp.exists() and str(expected) in fp.read_text(encoding="utf-8", errors="ignore")
            result.update(result="PASS" if ok else "FAIL", evidence=target)
        else:
            result["result"] = "UNVERIFIED"
        results.append(result)
        if result["result"] == "FAIL":
            failed.append(result["criterion"])
    return results, failed


def sensors_exit_gate(bundle, contract, errors):
    """SENSORS_EXIT_GATE 2026-10-31: 生产出口强制切流。契约含 sha256/criteria 且失败 -> 追加错误，PASS 降级 REJECT。无 sha256/criteria/output_file 一律 REJECT，老契约豁免作废 20261031-1735。"""
    out = contract.get("output_file") or contract.get("deliverable_path")
    if not out:
        errors.append("sensors: contract missing output_file/deliverable_path")
        return
    fp = Path(out) if str(out).startswith("/") else bundle / out
    if not contract.get("sha256"):
        errors.append("sensors: contract missing sha256")
    if not contract.get("criteria"):
        errors.append("sensors: contract missing criteria")
    if contract.get("sha256") and fp.exists():
        if sha256_file(fp) != contract["sha256"]:
            errors.append("sensors: sha256 mismatch for deliverable")
    if contract.get("criteria") and fp.exists():
        text = fp.read_text(encoding="utf-8", errors="ignore")
        for crit in contract["criteria"]:
            if isinstance(crit, dict):
                if crit.get("keyword") and crit["keyword"] not in text:
                    errors.append("sensors: criteria keyword missing: " + crit["keyword"])
                if crit.get("min_chars") and len(text) < crit["min_chars"]:
                    errors.append("sensors: criteria chars below min")

def main(bundle_dir: str, contract_path: str):
    bundle = Path(bundle_dir)
    contract = load_json(Path(contract_path))
    errors = []
    errors += check_manifest(bundle)
    errors += check_receipts(bundle, contract)
    results, failed = check_criteria(bundle, contract)
    unverified = [r for r in results if r["result"] == "UNVERIFIED"]
    # Cascade: if criteria pass, content gate must also pass on the acceptance output file.
    if not errors and not failed and not unverified:
        try:
            import subprocess as _sp
            _target = None
            for _c in contract.get("acceptance_criteria", []):
                if isinstance(_c, dict) and _c.get("type") == "file_exists":
                    _target = _c.get("target") or _c.get("path")
                    break
            if _target:
                _fp = bundle / _target if not str(_target).startswith("/") else Path(_target)
                _cv = Path("/home/ubuntu/ai-team/scripts/content_verify.py")
                if _cv.exists() and _fp.exists():
                    _r = _sp.run(["python3", str(_cv), str(_fp)], capture_output=True, text=True, timeout=60)
                    if _r.returncode != 0:
                        errors.append("content_verify failed: " + (_r.stdout.strip() or _r.stderr.strip())[:300])
                else:
                    errors.append("content_verify unavailable or acceptance file missing")
        except Exception as _e:
            errors.append("content_verify error: " + str(_e)[:200])
    sensors_exit_gate(bundle, contract, errors)
    verdict = "PASS" if not errors and not failed and not unverified else "REJECT"
    if not errors and not failed and unverified:
        verdict = "NEED_HUMAN"
    out = {
        "verdict": verdict,
        "failed_criteria": failed,
        "unverified_criteria": [r["criterion"] for r in unverified],
        "receipt_errors": errors,
        "criterion_results": results,
        "recommended_action": "INGEST" if verdict == "PASS" else ("QUARANTINE" if verdict == "REJECT" else "HUMAN_REVIEW"),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if verdict == "PASS" else 1

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: verify.py <evidence_bundle_dir> <contract.json>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))

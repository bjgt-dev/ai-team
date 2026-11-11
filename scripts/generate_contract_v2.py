#!/usr/bin/env python3
# Contract generator v2 (2026-10-25)
# Rule: objective must contain full task file text, acceptance details, input file paths.
# task_type stays schema-compliant (llm_generate), execution_mode=shell_worker_proxy
# indicates shell-capable execution, not llm-only text generation.
import json, sys, pathlib, datetime
task_file = pathlib.Path(sys.argv[1])
task_id = sys.argv[2]
output_file = sys.argv[3]
acceptance = json.loads(sys.argv[4]) if len(sys.argv) > 4 else [{"type": "file_exists", "target": output_file}]
text = task_file.read_text()
contract = {
    "task_id": task_id,
    "task_type": "llm_generate",
    "execution_mode": "shell_worker_proxy",
    "objective": "FULL TASK TEXT:\n" + text + "\nACCEPTANCE:\n" + json.dumps(acceptance, ensure_ascii=False) + "\nEXECUTE WITH SHELL, DO NOT WRITE GENERIC TEXT. Shell receipts required: command, rc, output_sha256.",
    "output_file": output_file,
    "acceptance_criteria": acceptance,
    "input_files": [str(task_file)],
    "timeout_s": 1800,
    "max_retries": 2,
    "idempotency_key": task_id,
    "evidence_required": ["receipts.jsonl", "manifest.json", "acceptance_self_check.json"],
    "created_at": datetime.datetime.now().astimezone().isoformat(),
    "source": "contract-generator-v2-20261025",
    "negative_constraints": [
        {"id": "NC-01", "source_text": "no fabrication", "rule": "do not fabricate evidence", "acceptance_check": "verify.py PASS + shell receipts"},
        {"id": "NC-02", "source_text": "no irreversible change without backup", "rule": "backup and rollback before production change", "acceptance_check": "rollback file exists"},
    ],
}
out = pathlib.Path(sys.argv[5]) if len(sys.argv) > 5 else pathlib.Path(f"/home/ubuntu/ai-team/contracts/pending/muse/{task_id}.json")
out.write_text(json.dumps(contract, ensure_ascii=False, indent=2))
print(task_id, out)

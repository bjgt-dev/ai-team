{
  "task_id": "L3-001-reconcile-stage2-queue",
  "discovered_at": "2026-11-02T12:30:00+08:00",
  "stage2_entries": [
    {
      "id": "dec-20261102-020",
      "task_id": "stage2-review-quant-20261102",
      "summary": "兜底三级已用尽，任务 stage2-review-quant-20261102 仍未通过"
    },
    {
      "id": "dec-20261102-023",
      "task_id": "stage2-review-quant-20261102",
      "summary": "五步 step5 二次验证失败，任务 stage2-review-quant-20261102 仍未通过"
    }
  ],
  "fake_receipt_entries": [
    {
      "id": "dec-20261102-022",
      "task_id": "butler-seat-1793592475-1793592354"
    },
    {
      "id": "dec-20261102-024",
      "task_id": "butler-seat-1793592603-1793592354"
    },
    {
      "id": "dec-20261102-025",
      "task_id": "butler-seat-1793592902-1793592354"
    }
  ],
  "action": "DO NOT DELETE originals. Mark as superseded in a separate reconciliation note: stage2 rerun at 2026-11-02 12:08 passed with receipt, so dec-020/023 are stale. fake_receipt entries remain BLOCKED pending chairman decision, not auto-closed.",
  "evidence": "decision-queue.json pending list, stage2 receipt 2026-11-02 12:08"
}
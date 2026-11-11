#!/usr/bin/env python3
"""Write a butler session handoff file. Task text comes from env HANDOFF_TASK
to avoid shell quoting problems. Never deletes an old session."""
import json, os, sys, glob
out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/session-handoff.json"
task = os.environ.get("HANDOFF_TASK", "")
def mt(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None
all_tasks = sorted(os.path.basename(x) for x in glob.glob("/home/ubuntu/ai-team/ceo-seat/tasks/*.md"))
tasks = all_tasks[:50]
rec = {
    "task": task[:4000],
    "mode_attempt": "continue",
    "last_ack_mtime": mt("/home/ubuntu/ai-team/state/dispatch-ack.log"),
    "last_patrol_mtime": mt("/home/ubuntu/ai-team/ceo-seat/out/patrol-latest.md"),
    "unfinished_task_files": tasks,
    "task_files_total": len(all_tasks),
    "task_files_sampled": len(tasks),
    "unfinished_task_files_note": "仅按文件名排序取前 50 个任务文件样本，未按完成状态过滤；task_files_total 为实际任务文件总数。",
    "note": "If --continue fails, start a fresh session with this file, do not hard-continue the dead session, do not delete the old session.",
}
with open(out, "w") as f:
    json.dump(rec, f, ensure_ascii=False, indent=2)
print(out)

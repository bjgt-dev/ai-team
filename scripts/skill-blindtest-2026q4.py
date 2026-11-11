#!/usr/bin/env python3
"""skill-blindtest-2026q4.py -- Thorsten Ball Rule 6 / skill-blindtest-sop.md.

Before this script the quarterly blind test existed only as SOP text
(docs/sops/skill-blindtest-sop.md): no runner, no report, no queue task,
no measurement. This is the harness that runs it.

Design (follows the SOP exactly):
  * task spec JSON: {"task": "...", "success": "...", "knowledge_files": [...],
    "trials": 3, "runner": "<command>", "quarter": "2026q4"}
  * control arm: run the task 3x WITHOUT the knowledge files.
  * experiment arm: run the same task 3x WITH the knowledge files.
  * the runner is an external command (hub gateway handler in production;
    any command in tests). It receives env BLINDTEST_ARM (control|experiment),
    BLINDTEST_TRIAL (1..N), BLINDTEST_KNOWLEDGE (colon-joined paths, empty
    for control), BLINDTEST_TASK (task text). It must print a JSON object
    on stdout: {"compliant": bool, "seconds": float, "tokens": int,
    "errors": int}. Anything else -> the trial is recorded as an error
    trial (errors=1, compliant=false), never silently dropped.
  * report: comparison table + verdict (effective / ineffective /
    partially effective) + which guidance to keep / rewrite / delete,
    written to state/skill-blindtest-<quarter>-REPORT.md and, when
    --register is given, appended to the skills registry is out of scope
    for this script (registry write stays a human step; the report lists
    the exact lines to change).

Exit: 0 = report written (verdict is data, not an exit code);
      2 = usage/tool error (bad spec, runner missing, no trials recorded).
No network, no model calls inside this script: the runner owns that.
"""
import argparse
import json
import os
import subprocess
import sys
import time

DEFAULT_REPORT_DIR = "/home/ubuntu/ai-team/state"


def _run_one(runner, task, arm, trial, knowledge, timeout):
    env = dict(os.environ)
    env["BLINDTEST_ARM"] = arm
    env["BLINDTEST_TRIAL"] = str(trial)
    env["BLINDTEST_KNOWLEDGE"] = ":".join(knowledge) if arm == "experiment" else ""
    env["BLINDTEST_TASK"] = task
    started = time.time()
    try:
        proc = subprocess.run(runner, shell=True, capture_output=True,
                              text=True, timeout=timeout, env=env)
        elapsed = time.time() - started
        raw = (proc.stdout or "").strip()
        rec = json.loads(raw) if raw else {}
        if not isinstance(rec, dict):
            raise ValueError("runner output is not a JSON object")
        return {
            "arm": arm, "trial": trial,
            "compliant": bool(rec.get("compliant", False)),
            "seconds": float(rec.get("seconds", elapsed)),
            "tokens": int(rec.get("tokens", 0)),
            "errors": int(rec.get("errors", 0 if proc.returncode == 0 else 1)),
            "runner_rc": proc.returncode, "raw": raw[:500],
        }
    except Exception as exc:
        return {
            "arm": arm, "trial": trial, "compliant": False,
            "seconds": round(time.time() - started, 2), "tokens": 0,
            "errors": 1, "runner_rc": None,
            "raw": "runner error: %s" % exc,
        }


def _summarise(trials):
    n = len(trials)
    if n == 0:
        return {"n": 0, "compliant": 0, "mean_seconds": None,
                "mean_tokens": None, "errors": 0}
    return {
        "n": n,
        "compliant": sum(1 for t in trials if t["compliant"]),
        "mean_seconds": round(sum(t["seconds"] for t in trials) / n, 2),
        "mean_tokens": round(sum(t["tokens"] for t in trials) / n, 1),
        "errors": sum(t["errors"] for t in trials),
    }


def _verdict(control, experiment):
    """SOP comparison: more compliant, faster, fewer tokens = effective."""
    wins = 0
    if experiment["compliant"] > control["compliant"]:
        wins += 1
    if (experiment["mean_seconds"] is not None and control["mean_seconds"] is not None
            and experiment["mean_seconds"] < control["mean_seconds"]):
        wins += 1
    if (experiment["mean_tokens"] is not None and control["mean_tokens"] is not None
            and experiment["mean_tokens"] < control["mean_tokens"]):
        wins += 1
    if wins >= 2:
        return "effective"
    if wins == 1:
        return "partially effective"
    return "ineffective"


def render_report(spec, control_trials, experiment_trials, control_sum,
                  experiment_sum, verdict):
    def rows(trials):
        return "\n".join(
            "| %d | %s | %.2f | %d | %d |" % (
                t["trial"], "Y" if t["compliant"] else "N",
                t["seconds"], t["tokens"], t["errors"])
            for t in trials)
    return (
        "# Skill 盲测报告 %s\n\n" % spec.get("quarter", "")
        + "- 任务：%s\n- 完成标准：%s\n- 知识文件：%s\n"
        % (spec.get("task", ""), spec.get("success", ""),
           ", ".join(spec.get("knowledge_files", [])) or "（无）")
        + "- 每臂试验次数：%d\n- 结论：%s\n\n" % (len(control_trials), verdict)
        + "## 对照组（无知识文件）\n\n| 次数 | 遵守约定 | 用时（秒） | token | 出错 |\n|---|----|----|----|----|\n"
        + rows(control_trials)
        + "\n\n合计：遵守 %d/%d，平均 %.2f 秒，平均 %.1f token，出错 %d 次。\n\n"
        % (control_sum["compliant"], control_sum["n"],
           control_sum["mean_seconds"] or 0, control_sum["mean_tokens"] or 0,
           control_sum["errors"])
        + "## 实验组（带知识文件）\n\n| 次数 | 遵守约定 | 用时（秒） | token | 出错 |\n|---|----|----|----|----|\n"
        + rows(experiment_trials)
        + "\n\n合计：遵守 %d/%d，平均 %.2f 秒，平均 %.1f token，出错 %d 次。\n\n"
        % (experiment_sum["compliant"], experiment_sum["n"],
           experiment_sum["mean_seconds"] or 0, experiment_sum["mean_tokens"] or 0,
           experiment_sum["errors"])
        + "## 处置\n\n- 结论为 effective：指引保留，写进知识库（解决了什么、验证数字、以后什么活优先用它）。\n"
        + "- 结论为 ineffective：指引删掉或重写（附文件路径与行号，由执行人填）。\n"
        + "- 结论为 partially effective：只保留起作用的部分，其余重写。\n"
        + "- 连续两季度同一指引无效 → 直接删除，不再重测。\n"
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description="skill quarterly blind test (Rule 6)")
    ap.add_argument("--spec", required=True, help="task spec JSON file")
    ap.add_argument("--out-dir", default=DEFAULT_REPORT_DIR)
    ap.add_argument("--timeout", type=int, default=600,
                    help="per-trial runner timeout in seconds")
    args = ap.parse_args(argv)
    try:
        with open(args.spec, encoding="utf-8") as f:
            spec = json.load(f)
    except Exception as exc:
        print("TOOL-ERROR: cannot read spec %s: %s" % (args.spec, exc))
        return 2
    for key in ("task", "success", "runner"):
        if not spec.get(key):
            print("TOOL-ERROR: spec missing required field: %s" % key)
            return 2
    trials = int(spec.get("trials", 3))
    if trials < 1:
        print("TOOL-ERROR: trials must be >= 1")
        return 2
    knowledge = spec.get("knowledge_files") or []
    missing = [p for p in knowledge if not os.path.exists(p)]
    if missing:
        print("TOOL-ERROR: knowledge file(s) missing: %s" % ", ".join(missing))
        return 2
    control = [_run_one(spec["runner"], spec["task"], "control", i + 1,
                        knowledge, args.timeout) for i in range(trials)]
    experiment = [_run_one(spec["runner"], spec["task"], "experiment", i + 1,
                           knowledge, args.timeout) for i in range(trials)]
    csum, esum = _summarise(control), _summarise(experiment)
    verdict = _verdict(csum, esum)
    report = render_report(spec, control, experiment, csum, esum, verdict)
    quarter = spec.get("quarter", "unknown")
    out_path = os.path.join(args.out_dir, "skill-blindtest-%s-REPORT.md" % quarter)
    os.makedirs(args.out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    print("blindtest %s: control compliant %d/%d, experiment %d/%d, verdict=%s"
          % (quarter, csum["compliant"], csum["n"], esum["compliant"], esum["n"], verdict))
    print("report: %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

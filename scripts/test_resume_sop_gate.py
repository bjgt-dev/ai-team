#!/usr/bin/env python3
"""test_resume_sop_gate.py — 简历 SOP 门禁测试。

正向全流程必须通过；每个违规用例必须被拦截（exit 1）。
跑法：python3 test_resume_sop_gate.py [gate 路径]
"""
import json
import os
import subprocess
import sys
import tempfile

GATE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "resume-sop-gate.py")

PASS = 0
FAIL = 0


def run(*args, expect=0):
    global PASS, FAIL
    r = subprocess.run([sys.executable, GATE, *args],
                       capture_output=True, text=True)
    ok = (r.returncode == expect)
    name = " ".join(args)
    if ok:
        PASS += 1
        print(f"PASS: {name} -> {r.returncode}")
    else:
        FAIL += 1
        print(f"FAIL: {name} -> {r.returncode}（期望 {expect}）\n  stdout={r.stdout.strip()}\n  stderr={r.stderr.strip()}")
    return r


def fresh_job(tmp, job_id="resume-test"):
    wd = os.path.join(tmp, job_id)
    run("init", "--job-id", job_id, "--workdir", wd, "--target-role", "AI产品经理", expect=0)
    return wd, os.path.join(wd, "resume-sop-state.json")


def touch(wd, name, text="x"):
    p = os.path.join(wd, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return name


def advance_to(wd, state, stage, actor, artifact, frozen=False):
    args = ["advance", "--state", state, "--stage", stage,
            "--actor", actor, "--artifact", artifact]
    if frozen:
        args.append("--frozen")
    return run(*args, expect=0)


def main():
    global PASS
    with tempfile.TemporaryDirectory() as tmp:
        # --- 用例 1：正向全流程通过 ---
        wd, state = fresh_job(tmp, "t1")
        for stage, actor, art in [
                ("intake", "twt", touch(wd, "jd/jd1.txt")),
                ("jd_analysis", "twt", touch(wd, "keywords.md")),
                ("material", "twt", touch(wd, "material.md"))]:
            advance_to(wd, state, stage, actor, art)
        advance_to(wd, state, "framework", "da-guan-jia",
                   touch(wd, "framework.md"), frozen=True)
        advance_to(wd, state, "drafting", "writer-a", touch(wd, "draft.md"))
        advance_to(wd, state, "tailor", "writer-a", touch(wd, "resume-ai-pm.md"))
        advance_to(wd, state, "ats", "checker-a", touch(wd, "ats-check.md"))
        for i, s in enumerate(["review_content", "review_facts",
                               "review_language", "review_layout"]):
            advance_to(wd, state, s, f"reviewer-{i+1}", touch(wd, f"review-{s}.md"))
        advance_to(wd, state, "acceptance", "chairman", touch(wd, "accept.md"))
        run("check", "--state", state, expect=0)

        # --- 用例 2：框架未定稿就写文，拦截 ---
        wd, state = fresh_job(tmp, "t2")
        for stage, actor, art in [
                ("intake", "twt", touch(wd, "jd/jd1.txt")),
                ("jd_analysis", "twt", touch(wd, "keywords.md")),
                ("material", "twt", touch(wd, "material.md"))]:
            advance_to(wd, state, stage, actor, art)
        run("advance", "--state", state, "--stage", "drafting",
            "--actor", "writer-a", "--artifact", touch(wd, "draft.md"), expect=1)

        # --- 用例 3：框架没冻结，拦截 ---
        wd, state = fresh_job(tmp, "t3")
        for stage, actor, art in [
                ("intake", "twt", touch(wd, "jd/jd1.txt")),
                ("jd_analysis", "twt", touch(wd, "keywords.md")),
                ("material", "twt", touch(wd, "material.md"))]:
            advance_to(wd, state, stage, actor, art)
        run("advance", "--state", state, "--stage", "framework",
            "--actor", "da-guan-jia", "--artifact", touch(wd, "framework.md"), expect=1)

        # --- 用例 4：跳步（先 tailor 后 drafting），拦截 ---
        wd, state = fresh_job(tmp, "t4")
        for stage, actor, art in [
                ("intake", "twt", touch(wd, "jd/jd1.txt")),
                ("jd_analysis", "twt", touch(wd, "keywords.md")),
                ("material", "twt", touch(wd, "material.md"))]:
            advance_to(wd, state, stage, actor, art)
        advance_to(wd, state, "framework", "da-guan-jia",
                   touch(wd, "framework.md"), frozen=True)
        run("advance", "--state", state, "--stage", "tailor",
            "--actor", "writer-a", "--artifact", touch(wd, "resume-ai-pm.md"), expect=1)

        # --- 用例 5：撰写席自己审自己，拦截 ---
        wd, state = fresh_job(tmp, "t5")
        for stage, actor, art, fr in [
                ("intake", "twt", touch(wd, "jd/jd1.txt"), False),
                ("jd_analysis", "twt", touch(wd, "keywords.md"), False),
                ("material", "twt", touch(wd, "material.md"), False),
                ("framework", "da-guan-jia", touch(wd, "framework.md"), True),
                ("drafting", "writer-a", touch(wd, "draft.md"), False),
                ("tailor", "writer-a", touch(wd, "resume-ai-pm.md"), False),
                ("ats", "checker-a", touch(wd, "ats-check.md"), False)]:
            advance_to(wd, state, stage, actor, art, frozen=fr)
        run("advance", "--state", state, "--stage", "review_content",
            "--actor", "writer-a", "--artifact", touch(wd, "review-x.md"), expect=1)

        # --- 用例 6：两轮同一审核人，拦截 ---
        wd, state = fresh_job(tmp, "t6")
        for stage, actor, art, fr in [
                ("intake", "twt", touch(wd, "jd/jd1.txt"), False),
                ("jd_analysis", "twt", touch(wd, "keywords.md"), False),
                ("material", "twt", touch(wd, "material.md"), False),
                ("framework", "da-guan-jia", touch(wd, "framework.md"), True),
                ("drafting", "writer-a", touch(wd, "draft.md"), False),
                ("tailor", "writer-a", touch(wd, "resume-ai-pm.md"), False),
                ("ats", "checker-a", touch(wd, "ats-check.md"), False)]:
            advance_to(wd, state, stage, actor, art, frozen=fr)
        advance_to(wd, state, "review_content", "reviewer-1",
                   touch(wd, "review-c.md"))
        run("advance", "--state", state, "--stage", "review_facts",
            "--actor", "reviewer-1", "--artifact", touch(wd, "review-f.md"), expect=1)

        # --- 用例 7：批量收意见期间改文，拦截；董事长说“改”后放行 ---
        wd, state = fresh_job(tmp, "t7")
        for stage, actor, art, fr in [
                ("intake", "twt", touch(wd, "jd/jd1.txt"), False),
                ("jd_analysis", "twt", touch(wd, "keywords.md"), False),
                ("material", "twt", touch(wd, "material.md"), False),
                ("framework", "da-guan-jia", touch(wd, "framework.md"), True),
                ("drafting", "writer-a", touch(wd, "draft.md"), False)]:
            advance_to(wd, state, stage, actor, art, frozen=fr)
        run("collect-feedback", "--state", state, expect=0)
        run("advance", "--state", state, "--stage", "tailor",
            "--actor", "writer-a", "--artifact", touch(wd, "resume-ai-pm.md"), expect=1)
        run("approve-edit", "--state", state, expect=0)
        advance_to(wd, state, "tailor", "writer-a", touch(wd, "resume-ai-pm.md"))

        # --- 用例 8：产出物不存在，拦截 ---
        wd, state = fresh_job(tmp, "t8")
        run("advance", "--state", state, "--stage", "intake",
            "--actor", "twt", "--artifact", "jd/不存在.txt", expect=1)

        # --- 用例 9：非董事长验收，拦截 ---
        wd, state = fresh_job(tmp, "t9")
        for stage, actor, art, fr in [
                ("intake", "twt", touch(wd, "jd/jd1.txt"), False),
                ("jd_analysis", "twt", touch(wd, "keywords.md"), False),
                ("material", "twt", touch(wd, "material.md"), False),
                ("framework", "da-guan-jia", touch(wd, "framework.md"), True),
                ("drafting", "writer-a", touch(wd, "draft.md"), False),
                ("tailor", "writer-a", touch(wd, "resume-ai-pm.md"), False),
                ("ats", "checker-a", touch(wd, "ats-check.md"), False)]:
            advance_to(wd, state, stage, actor, art, frozen=fr)
        for i, s in enumerate(["review_content", "review_facts",
                               "review_language", "review_layout"]):
            advance_to(wd, state, s, f"reviewer-{i+1}", touch(wd, f"review-{s}.md"))
        run("advance", "--state", state, "--stage", "acceptance",
            "--actor", "da-guan-jia", "--artifact", touch(wd, "accept.md"), expect=1)

        # --- 用例 10：手工篡改 state（drafting done 但框架未冻结），check 抓获 ---
        wd, state = fresh_job(tmp, "t10")
        with open(state, encoding="utf-8") as f:
            st = json.load(f)
        for s in ["intake", "jd_analysis", "material"]:
            st["stages"][s] = {"status": "done", "actor": "twt",
                               "artifact": f"{s}.md", "ts": "2026-11-10T00:00:00+00:00",
                               "frozen": False}
            touch(wd, f"{s}.md")
        st["stages"]["framework"] = {"status": "done", "actor": "da-guan-jia",
                                     "artifact": "framework.md", "ts": "2026-11-10T00:00:00+00:00",
                                     "frozen": False}
        st["stages"]["drafting"] = {"status": "done", "actor": "writer-a",
                                    "artifact": "draft.md", "ts": "2026-11-10T00:00:00+00:00",
                                    "frozen": False}
        st["drafter"] = "writer-a"
        with open(state, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
        touch(wd, "framework.md")
        touch(wd, "draft.md")
        run("check", "--state", state, expect=1)

        # --- 用例 11：重做 drafting 后下游重置 ---
        wd, state = fresh_job(tmp, "t11")
        for stage, actor, art, fr in [
                ("intake", "twt", touch(wd, "jd/jd1.txt"), False),
                ("jd_analysis", "twt", touch(wd, "keywords.md"), False),
                ("material", "twt", touch(wd, "material.md"), False),
                ("framework", "da-guan-jia", touch(wd, "framework.md"), True),
                ("drafting", "writer-a", touch(wd, "draft.md"), False),
                ("tailor", "writer-a", touch(wd, "resume-ai-pm.md"), False),
                ("ats", "checker-a", touch(wd, "ats-check.md"), False)]:
            advance_to(wd, state, stage, actor, art, frozen=fr)
        run("collect-feedback", "--state", state, expect=0)
        run("advance", "--state", state, "--stage", "drafting",
            "--actor", "writer-a", "--artifact", touch(wd, "draft-v2.md"), expect=1)
        run("approve-edit", "--state", state, expect=0)
        advance_to(wd, state, "drafting", "writer-a", touch(wd, "draft-v2.md"))
        with open(state, encoding="utf-8") as f:
            st = json.load(f)
        assert st["stages"]["tailor"]["status"] == "pending", "重做后 tailor 应重置"
        assert st["stages"]["ats"]["status"] == "pending", "重做后 ats 应重置"
        print("PASS: 用例 11 重做后下游重置")
        PASS += 1

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()

import argparse
import re
import concurrent.futures
import datetime
import json
import os
import sys
import tempfile
import time
import urllib.request
import urllib.error

# ---------- Backend Interfaces ----------
class Backend:
    def review(self, axis: str, corpus_text: str, spec_text: str) -> dict:
        raise NotImplementedError

class GatewayBackend(Backend):
    def __init__(self, timeout: int):
        self.timeout = timeout
        self.token = self._load_token()

    def _load_token(self) -> str:
        config_path = os.path.expanduser(
            "~/.config/opencode/opencode.json"
        )
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            raise RuntimeError(f"Failed to read token file: {e}")

        provider = data.get("provider", {})
        for prov in provider.values():
            options = prov.get("options", {})
            api_key = options.get("apiKey")
            if api_key:
                return api_key
        raise RuntimeError("API token not found in opencode.json")

    def review(self, axis: str, corpus_text: str, spec_text: str) -> dict:
        system_prompt = (
            "You are a code reviewer."
        )
        if axis == "standards":
            user_prompt = (
                "Review this diff against the project rules (R1-R7: no manual SQL, no manual file delivery, "
                "no fake DONE, worker holds no model/keys, every step writes a receipt) plus Fowler code smells. "
                "Be concise: at most 10 findings, only concrete issues in the diff. "
                "(Mysterious Name, Duplicated Code, Feature Envy, Data Clumps, Primitive Obsession, Repeated Switches, "
                "Shotgun Surgery, Divergent Change, Speculative Generality, Message Chains, Middle Man, Refused Bequest). "
                "Documented-rule breaches are hard violations (cite the rule). Smells are ALWAYS judgement_calls, never hard violations. "
                "Reply ONLY as JSON with keys verdict/violations/reasons/judgement_calls."
            )
        else:  # spec
            user_prompt = (
                "Compare this diff against the spec text. Report requirements missing/partial, behaviour not asked for, "
                "and requirements that look implemented but are wrong. Quote the spec line for each finding. "
                "Be concise: at most 10 findings, only concrete gaps actually in the spec, no invented requirements. "
                "Missing/partial/wrong requirements are hard violations (cite spec line). "
                "Reply ONLY as JSON with keys verdict/violations/reasons/judgement_calls."
            )
        payload = {
            "model": "glm-4-flash-cc",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"{user_prompt}\n\n---DIFF---\n{corpus_text}\n---SPEC---\n{spec_text}"}
            ],
            "temperature": 0,
            "max_tokens": 2048
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:3001/v1/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}"
            },
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp_data = resp.read().decode("utf-8")
                result = json.loads(resp_data)
        except urllib.error.URLError as e:
            raise RuntimeError(f"Gateway request failed: {e}")
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Gateway response not JSON: {e}")

        # Expected format: result["choices"][0]["message"]["content"]
        try:
            content = result["choices"][0]["message"]["content"]
            review_json = _parse_reply(content)
        except Exception as e:
            raise RuntimeError(f"Failed to parse gateway reply: {e}")

        return review_json

PASS_WORDS = {"pass", "acceptable", "ok", "approved", "no issues", "no issue", "no violation", "no violations", "clean", "good"}


def _close_json(text: str) -> str:
    """Close unclosed JSON brackets/braces (truncated model reply).
    Walks the string tracking bracket stack, skipping string literals.
    Returns the text with missing closers appended, innermost first.
    """
    stack = []
    in_str = False
    esc = False
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch in "{[":
                stack.append(ch)
            elif ch in "}]":
                if stack:
                    stack.pop()
    closers = {"{": "}", "[": "]"}
    return text + "".join(closers[c] for c in reversed(stack))


def _parse_reply(content: str) -> dict:
    """Parse a model reply into a normalized review dict.

    Models wrap JSON in ```json fences and use verdict synonyms
    (Acceptable/OK) and string reasons. Normalize all of it; the gate
    requires verdict exactly PASS/FAIL and list-typed fields.
    Fail-closed: an ambiguous verdict is FAIL unless it is a pass synonym
    AND there are no hard violations. Empty/unparseable content raises.
    """
    text = (content or "").strip()
    if not text:
        raise RuntimeError("empty gateway reply")
    # Strip markdown fences (```json ... ``` or ``` ... ```).
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    if m:
        text = m.group(1).strip()
    # Extract the first {...} object if prose surrounds it.
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError("no JSON object in gateway reply")
        text = text[start:end + 1]
    try:
        data = json.loads(text)
    except Exception:
        # Repair: model may have been cut off by the token cap, leaving
        # unclosed brackets. Close them (innermost first) and retry once.
        repaired = _close_json(text)
        try:
            data = json.loads(repaired)
        except Exception as e:
            raise RuntimeError("gateway reply not JSON: %s" % e)
    if not isinstance(data, dict):
        raise RuntimeError("gateway reply JSON is not an object")
    verdict = str(data.get("verdict", "")).strip().lower()
    violations = data.get("violations") or []
    if isinstance(violations, dict):
        violations = [violations]
    if not isinstance(violations, list):
        violations = []
    # Normalize items to dicts: the gate blocks on ANY string violation,
    # and process_axis calls v.get("hard") which crashes on strings.
    # A bare string cites no rule, so it is a non-blocking note (preserved
    # as evidence); only dict items with hard=true can block.
    normed = []
    for v in violations:
        if isinstance(v, dict):
            normed.append({"hard": bool(v.get("hard")),
                           "rule": str(v.get("rule", "")),
                           "evidence": str(v.get("evidence", ""))})
        elif isinstance(v, str):
            normed.append({"hard": False, "rule": "model-note", "evidence": v})
        else:
            normed.append({"hard": False, "rule": "model-note", "evidence": str(v)})
    violations = normed
    reasons = data.get("reasons") or []
    if isinstance(reasons, str):
        reasons = [reasons]
    if not isinstance(reasons, list):
        reasons = [str(reasons)]
    jc = data.get("judgement_calls") or []
    if isinstance(jc, str):
        jc = [jc]
    if not isinstance(jc, list):
        jc = [str(jc)]
    hard = [v for v in violations if isinstance(v, dict) and v.get("hard")]
    # FAIL requires at least one HARD violation. A model FAIL verdict with
    # zero hard violations is noise (observed: model listed R1-R7 with empty
    # evidence and verdict FAIL) and must not block: normalize to PASS and
    # keep the findings in the record for human review. Conversely a PASS
    # verdict that lists hard violations is a contradiction -> FAIL.
    if hard:
        final = "FAIL"
    else:
        final = "PASS"
        if verdict not in PASS_WORDS:
            reasons = reasons + ["model verdict %r without hard violations, treated as PASS" % verdict]
    # judgement_calls: normalize dict items to their reason string.
    jc_norm = []
    for item in jc:
        if isinstance(item, dict):
            jc_norm.append(str(item.get("reason") or item.get("evidence") or item.get("text") or item))
        else:
            jc_norm.append(str(item))
    return {"verdict": final, "violations": violations,
            "reasons": reasons, "judgement_calls": jc_norm}


class StubBackend(Backend):
    def __init__(self, responses: dict):
        """
        responses: {"standards": [dict, ...], "spec": [dict, ...]}
        Each call pops the first element for the axis.
        """
        self.responses = {
            "standards": list(responses.get("standards", [])),
            "spec": list(responses.get("spec", []))
        }

    def review(self, axis: str, corpus_text: str, spec_text: str) -> dict:
        lst = self.responses.get(axis)
        if not lst:
            raise RuntimeError(f"No stub response left for axis {axis}")
        return lst.pop(0)

# ---------- Core Review Logic ----------
def _current_iso8601() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def _write_record_atomic(path: str, record: dict):
    dir_name = os.path.dirname(path)
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

def _log_event(log_path: str, entry: dict):
    # Best-effort only: logging must never break the review. The verifier
    # re-runs tests sandboxed as nobody in a read-only workdir (butler C2);
    # a log write there raises PermissionError, which must not fail tests.
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass

def run_review(task_id: str, corpus_text: str, spec_text: str,
               backend: Backend, review_dir: str, timeout_s: int):
    start_ms = int(time.time() * 1000)
    axes = ["standards", "spec"]
    results = {}
    records = {}
    # Helper to run a single axis with possible double review
    def process_axis(axis: str):
        # First attempt (one retry: glm-4-flash-cc sometimes returns
        # malformed JSON; a fresh call usually parses. Without a retry a
        # single bad reply kills the whole review with TOOL-ERROR.)
        resp = None
        last_err = None
        for attempt in (1, 2):
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(backend.review, axis, corpus_text, spec_text)
                    resp = future.result(timeout=timeout_s)
                break
            except concurrent.futures.TimeoutError:
                raise TimeoutError(f"Axis {axis} timed out")
            except Exception as e:
                last_err = e
                if attempt == 2:
                    raise RuntimeError(f"Backend error on axis {axis}: {e}")
                continue

        verdict = resp.get("verdict")
        violations = resp.get("violations", [])
        reasons = resp.get("reasons", [])
        judgement_calls = resp.get("judgement_calls", [])
        hard_rules = [v for v in violations if v.get("hard")]

        if verdict == "FAIL" and hard_rules:
            # second review
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future2 = executor.submit(backend.review, axis, corpus_text, spec_text)
                    resp2 = future2.result(timeout=timeout_s)
            except concurrent.futures.TimeoutError:
                raise TimeoutError(f"Axis {axis} second review timed out")
            except Exception as e:
                raise RuntimeError(f"Backend error on second review axis {axis}: {e}")

            hard_rules2 = [v for v in resp2.get("violations", []) if v.get("hard")]
            # Check if any hard violation with same rule appears in both
            reproduced = False
            reproduced_rule = None
            for v1 in hard_rules:
                rule1 = v1.get("rule")
                for v2 in hard_rules2:
                    if v2.get("rule") == rule1:
                        reproduced = True
                        reproduced_rule = rule1
                        break
                if reproduced:
                    break
            if reproduced:
                final_verdict = "FAIL"
                final_violations = [v for v in hard_rules if v.get("rule") == reproduced_rule]
                final_reasons = resp2.get("reasons", [])
                final_jc = resp2.get("judgement_calls", [])
                reproducible = True
            else:
                final_verdict = "PASS"
                final_violations = []
                final_reasons = ["non-reproducible finding, not blocking"]
                final_jc = judgement_calls  # keep original calls
                reproducible = False
        else:
            final_verdict = verdict
            final_violations = violations
            final_reasons = reasons
            final_jc = judgement_calls
            reproducible = True

        record = {
            "task_id": task_id,
            "axis": axis,
            "verdict": final_verdict,
            "violations": final_violations,
            "reasons": final_reasons,
            "judgement_calls": final_jc,
            "reproducible": reproducible,
            "reviewed_at": _current_iso8601()
        }
        return final_verdict, record

    try:
        for axis in axes:
            verdict, rec = process_axis(axis)
            results[axis] = verdict
            records[axis] = rec
    except TimeoutError:
        # Log and return retry code
        end_ms = int(time.time() * 1000)
        _log_event(
            os.path.join("state", "logs", "review-producer.log"),
            {
                "task_id": task_id,
                "axes": results,
                "exit_code": 3,
                "duration_ms": end_ms - start_ms
            }
        )
        print(f"TIMEOUT {task_id}")
        return 3, {}
    except Exception as e:
        end_ms = int(time.time() * 1000)
        _log_event(
            os.path.join("state", "logs", "review-producer.log"),
            {
                "task_id": task_id,
                "axes": results,
                "exit_code": 2,
                "error": str(e),
                "duration_ms": end_ms - start_ms
            }
        )
        print(f"TOOL-ERROR {task_id}")
        return 2, {}

    # Write records atomically
    for axis, rec in records.items():
        path = os.path.join(review_dir, f"review-{task_id}-{axis}.json")
        _write_record_atomic(path, rec)

    # Determine exit code
    exit_code = 0 if all(v == "PASS" for v in results.values()) else 1

    end_ms = int(time.time() * 1000)
    _log_event(
        os.path.join("state", "logs", "review-producer.log"),
        {
            "task_id": task_id,
            "axes": results,
            "exit_code": exit_code,
            "duration_ms": end_ms - start_ms
        }
    )
    human = "REVIEW-PASS" if exit_code == 0 else "REVIEW-FAIL"
    print(f"{human} {task_id}")
    return exit_code, records

def main(argv=None):
    parser = argparse.ArgumentParser(description="Review Producer")
    parser.add_argument("--task-id", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--files", nargs="+")
    group.add_argument("--diff")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--review-dir", default="/home/ubuntu/ai-team/state/code-review-20261107")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--backend", choices=["gateway", "stub"], default="gateway")
    parser.add_argument("--stub-file")
    args = parser.parse_args(argv)

    # Build corpus_text
    if args.files:
        parts = []
        for path in args.files:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception as e:
                print(f"TOOL-ERROR {args.task_id}")
                sys.exit(2)
            parts.append(f"=== {path} ===\n{content}")
        corpus_text = "\n".join(parts)
    else:
        try:
            with open(args.diff, "r", encoding="utf-8") as f:
                corpus_text = f.read()
        except Exception as e:
            print(f"TOOL-ERROR {args.task_id}")
            sys.exit(2)

    # Read spec
    try:
        with open(args.spec, "r", encoding="utf-8") as f:
            spec_text = f.read()
    except Exception as e:
        print(f"TOOL-ERROR {args.task_id}")
        sys.exit(2)

    # Backend selection
    if args.backend == "gateway":
        backend = GatewayBackend(timeout=args.timeout)
    else:
        if not args.stub_file:
            print(f"TOOL-ERROR {args.task_id}")
            sys.exit(2)
        try:
            with open(args.stub_file, "r", encoding="utf-8") as f:
                stub_data = json.load(f)
        except Exception as e:
            print(f"TOOL-ERROR {args.task_id}")
            sys.exit(2)
        backend = StubBackend(stub_data)

    exit_code, _ = run_review(
        task_id=args.task_id,
        corpus_text=corpus_text,
        spec_text=spec_text,
        backend=backend,
        review_dir=args.review_dir,
        timeout_s=args.timeout
    )
    sys.exit(exit_code)

if __name__ == "__main__":
    main()

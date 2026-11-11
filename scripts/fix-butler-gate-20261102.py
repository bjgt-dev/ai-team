#!/usr/bin/env python3
"""Harden butler_decide.py model output handling (2026-11-02, gate fix).

Backs up the script, then:
1. Prompt: demand ONLY a JSON object, no markdown fences, no prose, and
   state that conditions must be a JSON array of strings.
2. Parse: strip a surrounding markdown fence before json.loads.
Schema validation (validate_model_output) is NOT touched: verdict enum,
level enum, conditions-must-be-list, rationale non-empty all stay strict.
Run on the hub. Prints EDIT_OK on success; asserts anchor counts.
"""
import io
import shutil

P = "/home/ubuntu/ai-team/scripts/butler_decide.py"
FENCE = chr(96) * 3

s = io.open(P, encoding="utf-8").read()

old_prompt = (
    '"Decide strictly in JSON {verdict, level, conditions, rationale}. "\n'
    '                      "verdict in DONE/FAILED/DEAD/NO_CHANGE/DECIDE; '
    'level in L0/L1/L2.\\n" + pack'
)
new_prompt = (
    '"Output ONLY a JSON object, no markdown fences, no prose. '
    'Decide strictly in JSON {verdict, level, conditions, rationale}. "\n'
    '                      "verdict in DONE/FAILED/DEAD/NO_CHANGE/DECIDE; '
    'level in L0/L1/L2; conditions must be a JSON array of strings.\\n" + pack'
)
assert s.count(old_prompt) == 1, "prompt anchor count != 1"
s = s.replace(old_prompt, new_prompt)

old_parse = (
    '    text = data["choices"][0]["message"]["content"]\n'
    '    return json.loads(text), model_id'
)
new_parse = (
    '    text = data["choices"][0]["message"]["content"]\n'
    '    t = text.strip()\n'
    '    if t.startswith(FENCE):\n'
    '        lines = t.splitlines()\n'
    '        if lines and lines[0].startswith(FENCE):\n'
    '            lines = lines[1:]\n'
    '        if lines and lines[-1].strip() == FENCE:\n'
    '            lines = lines[:-1]\n'
    '        t = "\\n".join(lines).strip()\n'
    '    return json.loads(t), model_id'
)
assert s.count(old_parse) == 1, "parse anchor count != 1"
s = s.replace(old_parse, new_parse)

# FENCE constant must exist near the top of model_call's module scope.
# Simplest robust placement: define it right before model_call.
anchor = "def model_call(pack, model_id):"
assert s.count(anchor) == 1, "model_call anchor count != 1"
s = s.replace(anchor, "FENCE = chr(96) * 3\n\n\n" + anchor, 1)

shutil.copyfile(P, P + ".bak-20261102-gatefix")
io.open(P, "w", encoding="utf-8").write(s)
print("EDIT_OK prompt+parse hardened, schema validation untouched")

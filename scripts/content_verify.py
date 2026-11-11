#!/usr/bin/env python3
# Content gate v2 (housekeeper 2026-10-25 r2 suggestion)
# FAIL if: starts with ```sh or Command:, sha256 not 64 hex real value, placeholder strings present,
# or refusal phrases present.
import re, sys, pathlib
text = pathlib.Path(sys.argv[1]).read_text(errors='ignore')
fails = []
if text.lstrip().startswith('```sh') or text.lstrip().startswith('Command:'):
    fails.append('starts_with_shell_template_or_command')
if 'computed from stdout' in text or 'example.com' in text or 'placeholder' in text.lower():
    fails.append('placeholder')
for phrase in ['I am unable', 'Please provide', 'standing by to execute', 'not sure what you are asking', 'not sure what you’re asking', 'I cannot execute shell', 'I cannot fabricate']:
    if phrase in text:
        fails.append('refusal:' + phrase)
# sha256 check: if file claims output_sha256, it must be 64 hex
for m in re.findall(r'output_sha256[:\s=]+([0-9a-fA-F]+)', text):
    if not re.fullmatch(r'[0-9a-f]{64}', m):
        fails.append('sha256_not_64_hex:' + m[:20])
print('FAIL ' + ','.join(fails) if fails else 'PASS')
sys.exit(1 if fails else 0)

#!/usr/bin/env python3
"""Hub message-box pilot (TEST ONLY, 2026-11-04).
File-based inboxes + HTTP-ready functions. No production queue, no real tokens.
Accounts: muse-1, muse, muse-2. Each has an inbox dir.
Message fields: id, sender, receiver, time, content, read.
Auth: per-account test tokens stored in pilot dir (test only, 600, never production).
"""
import json, os, sys, uuid, datetime, pathlib
BASE = pathlib.Path("/home/ubuntu/ai-team/state/msgbox-pilot-20261104")
ACCOUNTS = ["muse-1", "muse", "muse-2"]
def _tokens():
    p = BASE / "tokens.json"
    if not p.exists():
        import secrets
        toks = {a: "test-" + secrets.token_hex(8) for a in ACCOUNTS}
        p.write_text(json.dumps(toks, indent=2))
        os.chmod(p, 0o600)
        return toks
    return json.loads(p.read_text())
def _check(account, token):
    toks = _tokens()
    if account not in ACCOUNTS: raise ValueError("unknown account")
    if toks.get(account) != token: raise ValueError("bad token")
def send(sender, receiver, content, token):
    _check(sender, token)
    if receiver not in ACCOUNTS: raise ValueError("unknown receiver")
    mid = uuid.uuid4().hex[:12]
    msg = {"id": mid, "sender": sender, "receiver": receiver,
           "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "content": content, "read": False}
    (BASE / "inbox" / receiver / f"{mid}.json").write_text(json.dumps(msg, ensure_ascii=False, indent=2))
    return msg
def list_msgs(receiver, token, unread_only=False):
    _check(receiver, token)
    out = []
    for f in sorted((BASE / "inbox" / receiver).glob("*.json")):
        m = json.loads(f.read_text())
        if unread_only and m.get("read"): continue
        out.append(m)
    return out
def mark_read(receiver, mid, token):
    _check(receiver, token)
    f = BASE / "inbox" / receiver / f"{mid}.json"
    if not f.exists(): raise ValueError("message not found")
    m = json.loads(f.read_text()); m["read"] = True
    f.write_text(json.dumps(m, ensure_ascii=False, indent=2)); return m
if __name__ == "__main__":
    toks = _tokens()
    # Test chain: muse-1 -> muse
    m = send("muse-1", "muse", "msgbox-pilot-20261104: muse-1 to muse test", toks["muse-1"])
    print("sent", m["id"], m["sender"], "->", m["receiver"])
    inbox = list_msgs("muse", toks["muse"])
    print("muse inbox count", len(inbox), "unread", sum(1 for x in inbox if not x["read"]))
    found = [x for x in inbox if x["id"] == m["id"]]
    print("found test message", bool(found), found[0]["content"] if found else "")
    r = mark_read("muse", m["id"], toks["muse"])
    print("mark_read", r["id"], r["read"])
    inbox2 = list_msgs("muse", toks["muse"], unread_only=True)
    print("muse unread after read", len(inbox2))
    # Negative test: wrong token must fail
    try:
        list_msgs("muse", "wrong-token")
        print("negative test FAILED")
    except ValueError as e:
        print("negative test ok:", e)

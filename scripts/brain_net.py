#!/usr/bin/env python3
# brain_net.py — 外脑网络绑死层（2026-10-20 董事长方案 A 落地）
# 职责：
#   1) 账号→fanout 槽位绑定（配置：ai-team/state/brain_bindings.json），会话全程只走绑定槽位，绝不跳槽、绝不跨国顶替。
#   2) 启动前预检：经绑定槽位实测出口 IP 与地理（国家/时区），实测国家 != 期望国家 → NetBlocked 拒绝放行。
#      （VPNGate 节点标注不可信，2026-10-20 实测有节点标注 US、实测出口意大利——只认实测。）
#   3) Chromium 不支持 socks5 凭据认证，故用枢纽自带 xray 起一个仅监听 127.0.0.1 的无凭据中继：
#      浏览器 → 本地中继 → (带凭据) fanout socks → 隧道出口。中继随会话生灭。
#   4) 返回 Playwright 需要的 proxy / timezone_id / locale（按实测地理同步，杜绝"美国 IP + 中国时区"式矛盾）。
# 用法（自检）：brain_net.py <provider>  → 打印预检结果 JSON（会起停一次中继）
import json, os, random, subprocess, sys, tempfile, time
from pathlib import Path

BINDINGS_PATH = Path(os.environ.get("BRAIN_BINDINGS", "/home/ubuntu/ai-team/state/brain_bindings.json"))
FANOUT_STATE = Path("/var/lib/fanout/state.json")
XRAY = "/var/lib/fanout/bin/xray"
GEO_URL = "http://ip-api.com/json/?fields=status,message,country,countryCode,timezone,query"

LOCALES = {"US": "en-US", "GB": "en-GB", "JP": "ja-JP", "KR": "ko-KR", "SG": "en-SG",
           "DE": "de-DE", "FR": "fr-FR", "IT": "it-IT", "TH": "th-TH", "RU": "ru-RU",
           "AU": "en-AU", "CA": "en-CA", "NL": "nl-NL", "ES": "es-ES", "TW": "zh-TW",
           "HK": "zh-HK", "CN": "zh-CN"}


class NetBlocked(Exception):
    pass


def _read_fanout_state():
    try:
        return json.loads(FANOUT_STATE.read_text())
    except Exception:
        r = subprocess.run(["sudo", "-n", "cat", str(FANOUT_STATE)],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return json.loads(r.stdout)
        raise NetBlocked("cannot read fanout state.json (tunnel table unavailable)")


def load_binding(provider):
    try:
        bindings = json.loads(BINDINGS_PATH.read_text())
    except Exception as e:
        raise NetBlocked(f"cannot read bindings: {e}")
    b = bindings.get(provider)
    if not b:
        raise NetBlocked(f"no binding configured for provider {provider}")
    return b


def slot_cred(slot):
    st = _read_fanout_state()
    for t in st.get("tunnels", []):
        if t.get("slot") == slot:
            return t["port"], t["socks_user"], t["socks_pass"]
    raise NetBlocked(f"slot {slot} not present in fanout state")


def geo_via(port, user=None, pw=None):
    if user:
        proxy = f"socks5h://{user}:{pw}@127.0.0.1:{port}"
    else:
        proxy = f"socks5h://127.0.0.1:{port}"
    r = subprocess.run(["curl", "-s", "-m", "15", "--proxy", proxy, GEO_URL],
                       capture_output=True, text=True, timeout=25)
    try:
        d = json.loads(r.stdout)
    except Exception:
        raise NetBlocked("exit geo lookup failed (tunnel down or no egress)")
    if d.get("status") != "success":
        raise NetBlocked(f"geo lookup failed: {d.get('message')}")
    return d  # query=exit ip, countryCode, timezone


class Relay:
    """本地无凭据 socks 中继（xray），上游为带凭据的 fanout socks。"""

    def __init__(self, up_port, user, pw):
        self.up_port, self.user, self.pw = up_port, user, pw
        self.port = None
        self.proc = None
        self.cfg_file = None

    def start(self):
        base = 19400 + (os.getpid() % 60) * 3
        for cand in [base + i for i in range(3)] + [19000 + random.randint(0, 900) for _ in range(3)]:
            cfg = {"log": {"loglevel": "none"},
                   "inbounds": [{"listen": "127.0.0.1", "port": cand, "protocol": "socks",
                                 "settings": {"udp": True}}],
                   "outbounds": [{"protocol": "socks",
                                  "settings": {"servers": [{"address": "127.0.0.1", "port": self.up_port,
                                                             "users": [{"user": self.user, "pass": self.pw}]}]}}]}
            fd, path = tempfile.mkstemp(prefix="brain-relay-", suffix=".json")
            with os.fdopen(fd, "w") as f:
                f.write(json.dumps(cfg))
            proc = subprocess.Popen([XRAY, "run", "-c", path],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.2)
            if proc.poll() is not None:
                os.unlink(path)
                continue
            try:
                geo_via(cand)  # 经中继实测一次，通了才算起好
                self.port, self.proc, self.cfg_file = cand, proc, path
                return
            except NetBlocked:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()
                os.unlink(path)
                continue
        raise NetBlocked("local relay failed to start on all candidate ports")

    def close(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
            self.proc = None
        if self.cfg_file:
            try:
                os.unlink(self.cfg_file)
            except Exception:
                pass
            self.cfg_file = None


def preflight(provider):
    """返回 {slot, exit_ip, country, timezone, locale, proxy, relay}；任何不符抛 NetBlocked。"""
    b = load_binding(provider)
    slot, expect = int(b["slot"]), b["expect_country"]
    port, user, pw = slot_cred(slot)
    d = geo_via(port, user, pw)
    if d["countryCode"] != expect:
        raise NetBlocked(
            f"geo mismatch: {provider} bound to slot {slot} expecting {expect}, "
            f"actual exit {d['query']} is {d['countryCode']} ({d['country']}) - refusing to run")
    relay = Relay(port, user, pw)
    try:
        relay.start()
        d2 = geo_via(relay.port)
        if d2["countryCode"] != expect or d2["query"] != d["query"]:
            raise NetBlocked("relay egress differs from direct tunnel egress - refusing to run")
    except Exception:
        relay.close()
        raise
    return {"slot": slot, "exit_ip": d["query"], "country": d["countryCode"],
            "timezone": d["timezone"], "locale": LOCALES.get(d["countryCode"], "en-US"),
            "proxy": {"server": f"socks5://127.0.0.1:{relay.port}"}, "relay": relay}


def recheck(net):
    """发送前复查：经中继再测一次地理；国家变了抛 NetBlocked，同国换 IP 只返回提示。"""
    d = geo_via(net["proxy"]["server"].rsplit(":", 1)[1])
    if d["countryCode"] != net["country"]:
        raise NetBlocked(
            f"exit country changed mid-run: {net['country']} -> {d['countryCode']} - aborting before send")
    if d["query"] != net["exit_ip"]:
        return f"note: exit ip changed within {net['country']}: {net['exit_ip']} -> {d['query']}"
    return None


if __name__ == "__main__":
    prov = sys.argv[1] if len(sys.argv) > 1 else "gemini"
    try:
        net = preflight(prov)
        print(json.dumps({k: v for k, v in net.items() if k not in ("relay", "proxy")},
                         ensure_ascii=False))
        net["relay"].close()
    except NetBlocked as e:
        print(json.dumps({"provider": prov, "status": "net_blocked", "answer": str(e)},
                         ensure_ascii=False))
        sys.exit(2)

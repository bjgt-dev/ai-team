#!/usr/bin/env python3
"""InstaBox 10-node strong-compute cluster: one-click formation / status / teardown.

Runs ON the hub (arm-agent). Uses the InstaBox exec API with the two account
keys in /home/ubuntu/ai-team/state/.instabox*-api-key.

Recipe (proven in P1-P3, 2026-10-22):
  wake all -> discover endpoints live (IPv6 on eth0 + STUN-mapped IPv4 for the
  WG listen port) -> hybrid WireGuard mesh via wireguard-go (cross-pool pairs
  over IPv6, same-pool pairs over discovered IPv4 endpoints) -> verify
  handshake matrix -> optionally bring up a Ray cluster whose head is the
  best-connected node.

Usage:
  python3 instabox_cluster.py up [--ray] [--job] [--dual-account --accept-tos-risk]
  python3 instabox_cluster.py status
  python3 instabox_cluster.py down
State: /tmp/instabox_cluster_state.json
History: /home/ubuntu/ai-team/state/instabox_cluster_history.jsonl

Default formation is SINGLE-account (a1, worker-1..5, 20 CPU), the
expert-reviewed default. InstaBox ToS 2(b)(iv) prohibits creating multiple
accounts to obtain additional free-plan resources; the dual-account 10-node
fleet carries that account risk (reeval report section 7). Dual-account mode
therefore requires BOTH --dual-account and --accept-tos-risk.
WireGuard keys are regenerated on every `up` and shredded on `down`.
"""
import json, urllib.request, urllib.error, threading, time, re, sys, os

KEY_FILES = {"a1": "/home/ubuntu/ai-team/state/.instabox-api-key",
             "a2": "/home/ubuntu/ai-team/state/.instabox2-api-key",
             "a3": "/home/ubuntu/ai-team/state/.instabox3-api-key",
             "a4": "/home/ubuntu/ai-team/state/.instabox4-api-key"}
KEYS = {a: open(p).read().strip() for a, p in KEY_FILES.items()}
MACHINES = [
    ("worker-1", "a1", "m_agqv3bvofwxkfb2m4axoqjbju4"),
    ("worker-2", "a1", "m_agqv3c5yhvcfa36clq23hu336m"),
    ("worker-3", "a1", "m_agqv3n5d3ou3lloeoyv3zg5czq"),
    ("worker-4", "a1", "m_agqv3n5estnryd66fhihzavtzm"),
    ("worker-5", "a1", "m_agqv3n5fcdqga4mzdd65hcol5e"),
    ("worker-6", "a2", "m_agqv3oeaqad7z75wlewoy47eg4"),
    ("worker-7", "a2", "m_agqv3oeazcswsturdalymnbkgm"),
    ("worker-8", "a2", "m_agqv3oebcdlb3z6x75rvowduhe"),
    ("worker-9", "a2", "m_agqv3omteos5xmoapdrzosfywi"),
    ("worker-10", "a2", "m_agqv3omtnp3tf4q7hc4gsylyiu"),
]
IDX = {n: i + 1 for i, (n, _, _) in enumerate(MACHINES)}
BYNAME = {n: (a, m) for n, a, m in MACHINES}
NAMES = [n for n, _, _ in MACHINES]
ACTIVE = list(NAMES)
STATE = "/tmp/instabox_cluster_state.json"
HISTORY = "/home/ubuntu/ai-team/state/instabox_cluster_history.jsonl"
# worker-1 is excluded from head candidacy pending the residual-state
# reproduction experiment (two historical anomalies on its intra-pool
# paths, 2026-10-22). It may still serve as a worker. See report sec. 7.
HEAD_BLACKLIST = {"worker-1"}
WG_NET = "10.99.0.%d"
LISTEN = 51820

def log(*a):
    print("[%s]" % time.strftime("%H:%M:%S"), *a, flush=True)

def ex(name, cmd, t=120, retries=1):
    acct, mid = BYNAME[name]
    body = json.dumps({"cmd": cmd}).encode()
    last = ""
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            "https://api.instabox.sh/v1/machines/{}/exec".format(mid), data=body,
            headers={"Authorization": "Bearer " + KEYS[acct], "Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=t) as r:
                d = json.loads(r.read())
                return (d.get("stdout") or "") + (d.get("stderr") or "")
        except urllib.error.HTTPError as e:
            try: last = "HTTP %d %s" % (e.code, e.read().decode("utf-8", "replace")[:200])
            except Exception: last = "HTTP %d" % e.code
        except Exception as e:
            last = "ERR %s" % e
        if attempt < retries:
            time.sleep(15)
    return last

def parallel(fn, names):
    sems = {"a1": threading.Semaphore(3), "a2": threading.Semaphore(3)}
    out = {}
    def go(n):
        with sems[BYNAME[n][0]]:
            out[n] = fn(n)
    ts = [threading.Thread(target=go, args=(n,)) for n in names]
    for t in ts: t.start()
    for t in ts: t.join()
    return out

STUN_PY = """import socket, struct, os, sys
def query(srv):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('', %d)); s.settimeout(5)
    tid = os.urandom(12)
    s.sendto(struct.pack('>HHI', 1, 0, 0x2112A442) + tid, srv)
    data, _ = s.recvfrom(2048)
    s.close()
    if len(data) < 20: raise ValueError('short resp')
    mlen = struct.unpack('>H', data[2:4])[0]
    total = min(len(data), 20 + mlen); off = 20
    while off + 4 <= total:
        typ, ln = struct.unpack('>HH', data[off:off+4]); val = data[off+4:off+4+ln]
        if typ == 0x0020 and len(val) >= 8:
            p = struct.unpack('>H', val[2:4])[0] ^ 0x2112
            ip = '.'.join(str(b ^ m2) for b, m2 in zip(val[4:8], [0x21,0x12,0xA4,0x42]))
            return '%%s:%%d' %% (ip, p)
        if typ == 0x0001 and len(val) >= 8:
            p = struct.unpack('>H', val[2:4])[0]
            ip = '.'.join(str(b) for b in val[4:8])
            return '%%s:%%d' %% (ip, p)
        off += 4 + ln + ((4 - ln %% 4) %% 4)
    raise ValueError('no mapped attr')
for srv in [('74.125.250.129', 19302), ('74.125.197.129', 19302)]:
    try:
        print('STUN=' + query(srv)); sys.exit(0)
    except Exception as e:
        print('TRYFAIL', srv, repr(e))
print('STUN_FAIL_ALL')
""" % LISTEN

PROBE = ("echo MID=$(cat /etc/machine-id 2>/dev/null); "
         "echo V6=$(ip -6 addr show eth0 | grep -oE 'inet6 2[0-9a-f:]+' | head -1 | cut -d' ' -f2); "
         "echo V4=$(curl -s -4 --max-time 8 ifconfig.me); "
         "for b in wg wireguard-go iperf3 pip3; do which $b > /dev/null && echo HAS_$b || echo NO_$b; done; "
         "python3 -c 'import ray' 2>/dev/null && echo HAS_ray || echo NO_ray; "
         "[ -f /tmp/wgk ] || (which wg > /dev/null && wg genkey > /tmp/wgk 2>/dev/null); "
         "chmod 600 /tmp/wgk 2>/dev/null; "
         "[ -f /tmp/wgk ] && echo PUB=$(wg pubkey < /tmp/wgk)")

INSTALL = ("sudo apt-get update -qq 2>&1 | tail -1; "
           "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wireguard-tools wireguard-go iperf3 python3-pip 2>&1 | tail -1; "
           "python3 -m pip install --break-system-packages -q ray 2>&1 | tail -1; echo INSTALL_DONE")

def parse_probe(out):
    d = {}
    for line in out.splitlines():
        if line.startswith("MID="): d["mid"] = line[4:].strip()
        elif line.startswith("V6="): d["v6"] = line[3:].strip()
        elif line.startswith("V4="): d["v4"] = line[3:].strip()
        elif line.startswith("PUB="): d["pub"] = line[4:].strip()
        elif line.startswith("HAS_"): d[line[4:].strip()] = True
        elif line.startswith("NO_"): d[line[3:].strip()] = False
    return d

def seg_of(v6):
    parts = (v6 or "").split(":")
    return parts[4] if len(parts) > 4 else "?"

def cmd_up(with_ray, with_job):
    t_start = time.time()
    names = ACTIVE
    log("phase 1/5 wake + probe (%d nodes)" % len(names))
    probes = parallel(lambda n: parse_probe(ex(n, PROBE, retries=2)), names)
    for n in names:
        p = probes.get(n, {})
        log(" ", n, "mid", (p.get("mid") or "?")[:8], "v6", p.get("v6") or "-", "v4", p.get("v4") or "-",
            "tools", all(p.get(k) for k in ("wg", "wireguard-go", "iperf3", "pip3", "ray")))
    need = [n for n in names if not all(probes.get(n, {}).get(k) for k in ("wg", "wireguard-go", "iperf3", "pip3", "ray"))]
    if need:
        log("phase 1b install missing tools on:", ", ".join(need))
        parallel(lambda n: ex(n, INSTALL, t=600), need)
        probes.update(parallel(lambda n: parse_probe(ex(n, PROBE, retries=1)), need))
    log("phase 1c rotate WireGuard keys (fresh keypair per formation)")
    def rotate(n):
        out = ex(n, "wg genkey > /tmp/wgk 2>/dev/null; chmod 600 /tmp/wgk; echo PUB=$(wg pubkey < /tmp/wgk)")
        for line in out.splitlines():
            if line.startswith("PUB="):
                probes[n]["pub"] = line[4:].strip()
        return out
    parallel(rotate, names)
    missing_pub = [n for n in names if not probes.get(n, {}).get("pub")]
    if missing_pub:
        log("FATAL: no WG pubkey for", ", ".join(missing_pub)); return 2
    log("phase 2/5 STUN discovery (sequential, WG stopped per node)")
    stun = {}
    for n in names:
        out = ex(n, "sudo pkill -x wireguard-go; sleep 2; sudo rm -f /var/run/wireguard/wg0.sock; "
                    "cat > /tmp/stunnum.py <<'SEOF'\n" + STUN_PY + "SEOF\n"
                    "python3 /tmp/stunnum.py")
        got = None
        for line in out.splitlines():
            if line.startswith("STUN="): got = line[5:].strip()
        stun[n] = got
        log(" ", n, "stun4", got)
    groups = {}
    for n in names: groups.setdefault(seg_of(probes[n].get("v6")), []).append(n)
    log("  pools(by v6 segment):", json.dumps(groups))
    log("phase 3/5 mesh config (cross-pool IPv6, same-pool IPv4)")
    def cfg(n):
        parts = ["sudo -b wireguard-go wg0; sleep 2; sudo wg set wg0 listen-port %d private-key /tmp/wgk" % LISTEN]
        for other in names:
            if other == n: continue
            d = probes[other]
            if seg_of(d.get("v6")) == seg_of(probes[n].get("v6")) and stun.get(other):
                ep = stun[other]
            elif d.get("v6"):
                ep = "[%s]:%d" % (d["v6"], LISTEN)
            elif stun.get(other):
                ep = stun[other]
            else:
                continue
            parts.append("peer %s allowed-ips %s/32 endpoint %s persistent-keepalive 25" % (d["pub"], WG_NET % IDX[other], ep))
        parts.append("; sudo ip addr add %s/24 dev wg0 2>/dev/null; sudo ip link set wg0 up; echo CFG_OK" % (WG_NET % IDX[n]))
        return ex(n, " ".join(parts))
    cfgs = parallel(cfg, names)
    for n in names: log(" ", n, "cfg_ok" if "CFG_OK" in (cfgs.get(n) or "") else "CFG_CHECK")
    log("phase 4/5 verify (wait 35s for handshakes)")
    time.sleep(35)
    matrix = verify(probes, names)
    total = sum(v["hs_count"] for v in matrix.values())
    denom = len(names) * (len(names) - 1)
    log("  mesh directed pairs: %d/%d" % (total, denom))
    log("  matrix (row -> peers 1..%d, 1=handshake 0=none -=self):" % len(names))
    for n in names:
        hs_set = set(o for o in names if o != n and o not in matrix[n]["missing"])
        row = "".join("-" if o == n else ("1" if o in hs_set else "0") for o in names)
        log("   ", n, row)
    log("phase 4b data-plane gate (in-tunnel ping sweep)")
    def ping_sweep(n):
        cmd = "; ".join("ping -c2 -W2 %s > /dev/null 2>&1 && echo PING_OK_%d || echo PING_NO_%d" % (WG_NET % IDX[o], IDX[o], IDX[o]) for o in names)
        return ex(n, cmd)
    pings_raw = parallel(ping_sweep, names)
    ping_ok = {}
    for n in names:
        ok = set(int(x) for x in re.findall(r"PING_OK_(\d+)", pings_raw.get(n) or ""))
        ping_ok[n] = ok
        log(" ", n, "data-plane reachable: %d/%d" % (len(ok), len(names)))
    prev_head = None
    try:
        with open(HISTORY) as f:
            for line in f:
                prev_head = json.loads(line).get("head") or prev_head
    except Exception:
        pass
    full = len(names) - 1
    def head_score(n):
        return (matrix[n]["hs_count"], len(ping_ok.get(n, ())) )
    eligible = [n for n in names if n not in HEAD_BLACKLIST]
    if prev_head in eligible and matrix[prev_head]["hs_count"] == full and len(ping_ok.get(prev_head, ())) == len(names):
        head, why = prev_head, "stability: previous head still fully connected (hs + data-plane)"
    else:
        head = max(eligible, key=lambda n: (head_score(n), -len(groups.get(seg_of(probes[n].get("v6")), [n])), -IDX[n]))
        why = "best (handshakes, data-plane reach), then smallest NAT pool, excluding blacklist %s" % sorted(HEAD_BLACKLIST)
    log("  ray head:", head, "hs=%d/%d (%s)" % (matrix[head]["hs_count"], full, why))
    try:
        with open(HISTORY, "a") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "head": head,
                                "pairs": total, "denom": denom,
                                "missing": {n: matrix[n]["missing"] for n in names if matrix[n]["missing"]},
                                "ping_reach": {n: len(ping_ok.get(n, ())) for n in names}}) + "\n")
    except Exception as e:
        log("  history append failed:", e)
    state = {"probes": probes, "stun": stun, "groups": groups, "matrix": matrix,
             "head": head, "formed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "mesh_wall_s": round(time.time() - t_start, 1), "ray": False}
    json.dump(state, open(STATE, "w"), indent=1)
    if with_ray:
        log("phase 5/5 ray cluster (head=%s)" % head)
        parallel(lambda n: ex(n, "ray stop --force > /dev/null 2>&1; echo STOPPED"), names)
        time.sleep(3)
        out = ex(head, "ray start --head --port=6379 --node-ip-address=%s --num-cpus=4 2>&1 | tail -2" % (WG_NET % IDX[head]), t=300)
        log("  head:", out.replace("\n", " | ")[-140:])
        time.sleep(5)
        def join(n):
            return ex(n, "ray start --address=%s:6379 --node-ip-address=%s --num-cpus=4 2>&1 | tail -1" % (WG_NET % IDX[head], WG_NET % IDX[n]), t=300)
        parallel(join, [n for n in names if n != head])
        check = ex(head, "python3 -c \"import ray; ray.init(address='auto'); "
                         "import time; time.sleep(5); "
                         "ns=[x for x in ray.nodes() if x['Alive']]; "
                         "print('RAY_NODES', len(ns), 'CPU', ray.cluster_resources().get('CPU'))\" 2>&1 | grep RAY_NODES", t=300)
        log(" ", check.strip())
        state["ray"] = True
        json.dump(state, open(STATE, "w"), indent=1)
        if with_job:
            job = ("import ray, time, hashlib, collections\n"
                   "ray.init(address='auto')\n"
                   "@ray.remote\n"
                   "def chain(n):\n"
                   "    x = b'x'\n"
                   "    for _ in range(n): x = hashlib.sha256(x).digest()\n"
                   "    return ray.get_runtime_context().get_node_id()\n"
                   "t0 = time.time()\n"
                   "res = ray.get([chain.remote(18000000) for _ in range(40)])\n"
                   "print('SMOKE40 wall=%.2fs per_node=%s' % (time.time() - t0, dict(collections.Counter(res))))\n")
            out = ex(head, "cat > /tmp/smoke.py <<'JEOF'\n" + job + "JEOF\npython3 /tmp/smoke.py 2>&1 | grep SMOKE40", t=600)
            log(" ", out.strip())
    log("UP_DONE wall=%.0fs head=%s ray=%s" % (time.time() - t_start, head, with_ray))
    return 0

VERIFY_CMD = "sudo wg show wg0 latest-handshakes"
def verify(probes, names):
    pub2name = {probes[n].get("pub"): n for n in names if probes.get(n, {}).get("pub")}
    res = parallel(lambda n: ex(n, VERIFY_CMD), names)
    matrix = {}
    for n in names:
        hs = set()
        for line in (res.get(n) or "").splitlines():
            p = line.split()
            if len(p) == 2 and p[1].isdigit() and int(p[1]) > 0 and p[0] in pub2name:
                hs.add(pub2name[p[0]])
        missing = [o for o in names if o != n and o not in hs]
        matrix[n] = {"hs_count": len(hs), "missing": missing}
        log(" ", n, "hs=%d/%d missing=%s" % (len(hs), len(names) - 1, ",".join(missing) or "-"))
    return matrix

def cmd_status():
    if not os.path.exists(STATE):
        log("no state file; cluster not formed by this script"); return 1
    state = json.load(open(STATE))
    log("formed_at", state.get("formed_at"), "head", state.get("head"), "ray", state.get("ray"))
    snames = sorted(state["probes"].keys(), key=lambda n: IDX.get(n, 99))
    matrix = verify(state["probes"], snames)
    log("mesh directed pairs: %d/%d" % (sum(v["hs_count"] for v in matrix.values()), len(snames) * (len(snames) - 1)))
    if state.get("ray") and state.get("head"):
        out = ex(state["head"], "python3 -c \"import ray; ray.init(address='auto'); "
                                "ns=[x for x in ray.nodes() if x['Alive']]; "
                                "print('RAY_NODES', len(ns), 'CPU', ray.cluster_resources().get('CPU'))\" 2>&1 | grep RAY_NODES", t=120)
        log(out.strip())
    return 0

def cmd_down():
    log("teardown: ray stop + wireguard-go kill + WG key shred on all nodes")
    parallel(lambda n: ex(n, "ray stop --force > /dev/null 2>&1; sudo pkill -x wireguard-go; pkill iperf3 2>/dev/null; shred -u /tmp/wgk 2>/dev/null || rm -f /tmp/wgk; echo DOWN"), NAMES)
    if os.path.exists(STATE): os.remove(STATE)
    log("DOWN_DONE")
    return 0

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "up":
        if "--dual-account" in sys.argv:
            if "--accept-tos-risk" not in sys.argv:
                print("REFUSED: dual-account formation uses 10 free machines across 2 accounts.")
                print("InstaBox ToS 2(b)(iv) prohibits creating multiple accounts to obtain")
                print("additional free-plan resources; both accounts may be suspended.")
                print("Re-run with --dual-account --accept-tos-risk to acknowledge,")
                print("or run plain `up` for the single-account 5-node default.")
                sys.exit(3)
            ACTIVE[:] = list(NAMES)
            log("DUAL-ACCOUNT MODE: 10 nodes; ToS 2(b)(iv) risk acknowledged by operator")
        else:
            ACTIVE[:] = [n for n in NAMES if BYNAME[n][0] == "a1"]
            log("single-account mode (default): %d nodes, account a1 only" % len(ACTIVE))
        sys.exit(cmd_up("--ray" in sys.argv, "--job" in sys.argv))
    elif cmd == "status":
        sys.exit(cmd_status())
    elif cmd == "down":
        sys.exit(cmd_down())
    else:
        print("unknown command:", cmd); sys.exit(1)

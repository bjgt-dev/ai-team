// gate.js — OpenCode gate plugin (cutover 2026-11-10, S6 branch 1)
// Enforces the hub gate-svc for the butler (OpenCode) seat:
//  - session start: call gate-svc /search, cache receipt ids for this session.
//  - tool.execute.before: implementation tools (bash/write/edit/patch) require
//    a valid kb receipt for this session, else throw (block, exit 2 semantics).
//  - experimental.session.compacting: log a flush marker so the extractor picks
//    up the session (raw_log) before compaction.
// Gate-svc: http://127.0.0.1:8093 (tailnet only).
// This is a readable enforcement hook; the real enforcement is server-side
// (gate-svc + DB triggers). If this plugin fails to load, the seat falls back
// to branch 3 (S3 credential revocation + S4 DB constraints).
const GATE_SVC = "http://127.0.0.1:8093";
const SEAT = "butler";
const IMPL_TOOLS = new Set(["bash", "write", "edit", "patch"]);

// Append-only protected files (root fix, 2026-11-11, 3rd arch-note destruction).
// These files change ONLY via the approved append scripts; direct write/edit/
// patch (or a bash redirection) targeting them is denied below, in code, at
// the same choke point that checks receipts. The rule no longer lives in a prompt.
const PROTECTED_FILES = [
  "/home/ubuntu/ai-team/ceo-seat/out/butler-arch-note.md",
  "/home/ubuntu/ai-team/ceo-seat/state/decision-queue.json",
  "/home/ubuntu/ai-team/state/decision-queue.json",
];
const PROTECTED_BASENAMES = new Set(PROTECTED_FILES.map((p) => p.split("/").pop()));
const APPEND_SCRIPTS = ["arch-note-append.sh", "ledger-append.sh"];

// Content fields are skipped when scanning tool params for paths, so a verdict
// whose text merely mentions a protected file is not falsely denied.
const CONTENT_FIELDS = new Set(["content", "newText", "new_text", "oldText", "old_text", "text", "body", "diff"]);

// Collect path-like strings from the tool input, shape-agnostic.
function collectPathStrings(value, out, key) {
  if (typeof value === "string") {
    if (!CONTENT_FIELDS.has(key)) out.push(value);
    return;
  }
  if (value && typeof value === "object") {
    for (const [k, v] of Object.entries(value)) collectPathStrings(v, out, k);
  }
}

function mentionsProtected(input, directory) {
  const strings = [];
  collectPathStrings(input, strings, null);
  for (const s of strings) {
    for (const base of PROTECTED_BASENAMES) {
      if (s.includes(base)) return true;
    }
    const t = s.trim().replace(/^["']|["']$/g, "");
    if (t.includes("/")) {
      const resolved = t.startsWith("/")
        ? t
        : (directory ? directory.replace(/\/$/, "") + "/" + t : null);
      if (resolved && PROTECTED_FILES.includes(resolved)) return true;
    }
  }
  return false;
}

function invokesAppendScript(input) {
  const strings = [];
  collectPathStrings(input, strings, null);
  return strings.some((s) => APPEND_SCRIPTS.some((a) => s.includes(a)));
}

// per-session receipt cache: sessionID -> {receiptIds, ts}
const sessionReceipts = new Map();

async function gateSearch(sessionID, query) {
  const res = await fetch(GATE_SVC + "/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ seat: SEAT, slug: sessionID, query }),
  });
  if (!res.ok) throw new Error("gate-svc /search failed: " + res.status);
  return await res.json();
}

async function gateVerify(sessionID, receiptIds) {
  const res = await fetch(GATE_SVC + "/verify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ slug: sessionID, receipt_ids: receiptIds }),
  });
  if (!res.ok) throw new Error("gate-svc /verify failed: " + res.status);
  return await res.json();
}

export default async ({ project, client, $, directory, worktree }) => {
  return {
    // Session start: get gate-svc receipts so implementation tools are allowed.
    "event": async ({ event }) => {
      if (event.type === "session.created") {
        const sessionID = event.properties?.sessionID;
        if (!sessionID) return;
        try {
          const data = await gateSearch(sessionID, "butler session start");
          const ids = (data.receipts || []).map((r) => r.receipt_id);
          if (ids.length > 0) sessionReceipts.set(sessionID, { receiptIds: ids, ts: Date.now() });
        } catch (e) {
          // Fail open to branch 3 (DB triggers still enforce); log the miss.
          console.error("[gate] /search failed at session start: " + e.message);
        }
      }
    },
    // Tool call interception: implementation tools need a valid kb receipt.
    "tool.execute.before": async (input, output) => {
      const tool = input.tool;
      if (!IMPL_TOOLS.has(tool)) return;
      // Append-only guard (root fix, 2026-11-11): protected files change only
      // via the approved append scripts. Deny even with a valid receipt.
      // NOTE (4th destruction, 2026-11-11 20:08): opencode passes the tool's real
      // parameters in the SECOND hook argument under `args`; the first argument
      // carries only tool/sessionID/callID. Scanning `input` alone never sees
      // the file path. Scan both.
      const hookArgs = { ...(input || {}), ...((output && output.args) || {}) };
      if (!(tool === "bash" && invokesAppendScript(hookArgs)) && mentionsProtected(hookArgs, directory)) {
        throw new Error(
          "[gate] write to a protected append-only file is denied: '" + tool + "'. " +
          "Write the content to a temp file first, then run: " +
          "bash /home/ubuntu/ai-team/scripts/arch-note-append.sh <temp-file> " +
          "(arch note) or bash /home/ubuntu/ai-team/scripts/ledger-append.sh " +
          "(queues/ledger). Direct write/edit/patch to these files is blocked by design."
        );
      }
      const sessionID = input.sessionID;
      const cached = sessionID ? sessionReceipts.get(sessionID) : null;
      if (!cached || !cached.receiptIds || cached.receiptIds.length === 0) {
        throw new Error(
          "[gate] implementation tool '" + tool + "' blocked: no gate-svc receipt for this session. " +
          "Run: hubctl search butler <task-slug> <query> first (evidence by hubctl, agent writes no evidence)."
        );
      }
      // Verify the cached receipts are still valid (<24h, sig ok).
      try {
        const v = await gateVerify(sessionID, cached.receiptIds);
        if (!v.ok) {
          sessionReceipts.delete(sessionID);
          throw new Error(
            "[gate] implementation tool '" + tool + "' blocked: receipts invalid/expired: " +
            (v.reasons || []).join("; ") + ". Re-run hubctl search."
          );
        }
      } catch (e) {
        if (String(e.message).startsWith("[gate]")) throw e;
        // gate-svc unreachable: fail closed for implementation tools.
        throw new Error("[gate] implementation tool '" + tool + "' blocked: gate-svc unreachable (" + e.message + ").");
      }
    },
    // Before compaction: record a flush marker (extractor picks it up via raw_log).
    "experimental.session.compacting": async (input, output) => {
      const sessionID = input.sessionID;
      if (!sessionID) return;
      try {
        await gateSearch(sessionID, "session compacting flush");
      } catch (e) {
        console.error("[gate] compact flush /search failed: " + e.message);
      }
    },
  };
};

// gate.js — OpenCode gate plugin (cutover 2026-11-10, S6 branch 1)
// Enforces the hub gate-svc for the butler (OpenCode) seat:
//  - session start: call gate-svc /search, cache receipt ids for this session.
//  - tool.execute.before: implementation tools (bash/write/edit/patch) require
//    a valid kb receipt for this session, else throw (block, exit 2 semantics).
//  - experimental.session.compacting: log a flush marker so the extractor picks
//    up the session (raw_log) before compaction.
// Gate-svc: $GATE_SVC_URL (default http://127.0.0.1:8093).
// This is a readable enforcement hook; the real enforcement is server-side
// (gate-svc + DB triggers). If this plugin fails to load, the seat falls back
// to branch 3 (S3 credential revocation + S4 DB constraints).
const GATE_SVC = process.env.GATE_SVC_URL || "http://127.0.0.1:8093";
const SEAT = "butler";
const IMPL_TOOLS = new Set(["bash", "write", "edit", "patch"]);

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

---
name: local-system-check
description: Daily systematic global check before reporting: worker pool, queue, credentials ledger, gateway health, deadlines, commercial lines. Use when reporting status, creating a central queue task, or checking InstaBox/Oracle workers. Prevents single-task thinking and stale-ledger misses.
---

# System Check (not single-task thinking)

Before reporting, run all six layers, not just one task:

1. **Workers**: InstaBox 12 machines, 4 accounts, keys in hub state/.instabox*-api-key. Wake on demand via API exec (auto-resumes suspended, free=true). Idle auto-pause 30 min, max run 480 min. Oracle 2 machines only (arm-agent, amd-proxy), no new instances. muse/muse-2 are disk-only + switching, never execution workers.
2. **Queue**: Central queue READY/DONE/QUARANTINED/leases. No task bound to a single offline person. If a task waits for an unwakeable session, it is not automation.
3. **Credentials ledger**: List the directory first, then check the registry. Script: hub scripts/ledger-count-check-20261104.py — key file count must match registry xN. New account/key/machine must sync registry + scripts the same day.
4. **Gateway health**: Available pool >=2, canary, health 3 questions, disk / and /data, decision pending 0.
5. **Deadlines/risks**: amd-proxy restart (2026-11-10/11-11/11-12), ASCS expiry 2026-11-29, OCI idle P95, Beszel weak password before public entry.
6. **Commercial**: Three lines progress, L3 pending approval, report at 18:00 only if no commercial action that day.

## Preflight before creating a queue task (hub state/queue-task-preflight-20261104.md)
- Not bound to one person?
- Not waiting for an unwakeable session?
- Executor has the required credentials/config, or reports the missing item once?
If any fails, do not create the task.

## InstaBox wake method (same for all 12)
POST https://api.instabox.sh/v1/machines/{id}/exec with API key from hub, cmd hostname. Exec auto-resumes suspended machines. Do not ask for approval for free-plan resume; keys were already given.

---
name: avoid-pitfalls
description: 团队反复踩过的坑（由 skill_forge 每日从 REJECT 记录自动沉淀）：写契约、产证据、交任务前先对照本清单。同类错误出现 3 次自动入册。
---

# Avoid Pitfalls（自动沉淀的教训册）

## 教训（2026-10-25 入册，累计 5 次）
- 错误签名：`receipt[<n>] missing fields: ['output_sha<n>']`
- 出现于：diary-a1-singing-draft-001, diary-a1-singing-draft-002, sd021-iteration1-dev-20261024-1639, sd021-iteration1b-ui-20261025-0009, sd021-iteration1b-ui-shell-20261025-0040
- 规避：动手前检查对应环节；契约必须带 created_at；每个产物文件必须有 write_file 回执（含 path 与 output_sha256）。

## 教训（2026-10-26 入册，累计 4 次）
- 错误签名：`acceptance requires inbox<path><hex><path><hex>.md but no write receipt found`
- 出现于：coldpull-fix-20261025, proxy-transplant-20261025, proxy-transplant-20261025-b, wake-worker-pull-shell-20261025
- 规避：动手前检查对应环节；契约必须带 created_at；每个产物文件必须有 write_file 回执（含 path 与 output_sha256）。

## 教训（2026-10-26 入册，累计 4 次）
- 错误签名：`content_verify error: name 'pathlib' is not defined`
- 出现于：canary-worker-guangtou-20261025, canary2-worker-guangtou-20261025, proxy-s2c-guangtou-20261025, proxy-s2c-noesis-20261025
- 规避：动手前检查对应环节；契约必须带 created_at；每个产物文件必须有 write_file 回执（含 path 与 output_sha256）。

## 教训（2026-10-26 入册，累计 3 次）
- 错误签名：`acceptance requires artifacts<path> but no write receipt found`
- 出现于：intel-daily-20261025, requeue-test-001
- 规避：动手前检查对应环节；契约必须带 created_at；每个产物文件必须有 write_file 回执（含 path 与 output_sha256）。

## 教训（2026-10-26 入册，累计 3 次）
- 错误签名：`{'type': 'file_exists', 'target': 'artifacts<path>'}`
- 出现于：intel-daily-20261025, requeue-test-001
- 规避：动手前检查对应环节；契约必须带 created_at；每个产物文件必须有 write_file 回执（含 path 与 output_sha256）。

## 教训（2026-10-29 入册，累计 3 次）
- 错误签名：`{'type': 'file_min_size', 'target': 'artifacts<path>', 'expected': <n>}`
- 出现于：intel-daily-20261025, intel-daily-20261028, scan-test-001
- 规避：动手前检查对应环节；契约必须带 created_at；每个产物文件必须有 write_file 回执（含 path 与 output_sha256）。

## 教训（2026-10-30 入册，累计 3 次）
- 错误签名：`acceptance requires <path><hex>.md but no write receipt found`
- 出现于：automation-close-canary-20261029, patrol-hash-receipt-20261028c, patrol-hash-receipt-20261028c-shell1
- 规避：动手前检查对应环节；契约必须带 created_at；每个产物文件必须有 write_file 回执（含 path 与 output_sha256）。

## 教训（2026-10-30 入册，累计 3 次）
- 错误签名：`{'type': 'file_exists', 'target': '<path><hex>.md'}`
- 出现于：automation-close-canary-20261029, patrol-hash-receipt-20261028c, patrol-hash-receipt-20261028c-shell1
- 规避：动手前检查对应环节；契约必须带 created_at；每个产物文件必须有 write_file 回执（含 path 与 output_sha256）。

## 教训（2026-11-01 入册，累计 11 次）
- 错误签名：`sensors: contract missing sha<n>`
- 出现于：diary-a1-singing-draft-001, diary-a1-singing-draft-002, p1-01-expert-draft-20261025, p1-01-r2-20261025, p1-02-r2-20261025, p1-02-review-trigger-20261025, p1-03-r2-20261025, p1-04-backup-20261025, p1-04-r2-20261025, p1-05-coldpull-stop-20261025, p1-05-r2-20261025
- 规避：动手前检查对应环节；契约必须带 created_at；每个产物文件必须有 write_file 回执（含 path 与 output_sha256）。

## 教训（2026-11-01 入册，累计 11 次）
- 错误签名：`sensors: contract missing criteria`
- 出现于：diary-a1-singing-draft-001, diary-a1-singing-draft-002, p1-01-expert-draft-20261025, p1-01-r2-20261025, p1-02-r2-20261025, p1-02-review-trigger-20261025, p1-03-r2-20261025, p1-04-backup-20261025, p1-04-r2-20261025, p1-05-coldpull-stop-20261025, p1-05-r2-20261025
- 规避：动手前检查对应环节；契约必须带 created_at；每个产物文件必须有 write_file 回执（含 path 与 output_sha256）。

## 教训（2026-11-02 入册，累计 5 次）
- 错误签名：`sensors: contract missing output_file<path>`
- 出现于：canary-kit-sync-003, canary-kit-sync-004, canary-kit-sync-005, intel-daily-20261025, intel-daily-20261028
- 规避：动手前检查对应环节；契约必须带 created_at；每个产物文件必须有 write_file 回执（含 path 与 output_sha256）。

## 教训（2026-11-02 入册，累计 3 次）
- 错误签名：`receipt[<n>] predates contract (forgery<path>): <n>-<n>-<n>t<n>:<n>:<n>.<n>+<n>:<n> < <n>-<n>-<n>t<n>:<n>:<n>+<n>:<n>`
- 出现于：canary-kit-sync-004
- 规避：动手前检查对应环节；契约必须带 created_at；每个产物文件必须有 write_file 回执（含 path 与 output_sha256）。

## 教训（2026-11-02 入册，累计 3 次）
- 错误签名：`criterion file_exists missing target`
- 出现于：canary-kit-sync-003, canary-kit-sync-004, canary-kit-sync-005
- 规避：动手前检查对应环节；契约必须带 created_at；每个产物文件必须有 write_file 回执（含 path 与 output_sha256）。

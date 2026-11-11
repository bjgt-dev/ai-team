---
name: e2b-sync
description: E2B 执行后产物必须回传 parts/pi/，不得只留在临时路径
---
# E2B Sync

启用 E2B 时使用 --e2b-sync 或 /e2b-download，
确保产物落回 /workspace/<task_id>/parts/pi/。
不得只把文件留在 E2B 沙箱临时路径而不回传。

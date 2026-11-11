#!/usr/bin/env bash
# gate-action-dbmigrate.sh — /act db-migrate 执行器（gatesvc 身份运行，执行令51 缺口收口 E1）
# 用法：gate-action-dbmigrate.sh <task-slug>
# slug -> 固定迁移文件映射（白名单，不接受任意路径）：
#   cutover51-up       -> state/migrations/004_cutover51_up.sql
#   cutover51-down     -> state/migrations/004_cutover51_down.sql
#   rollback-prep-up   -> state/migrations/006_cutover51_rollback_prep_up.sql
#   rollback-prep-down -> state/migrations/006_cutover51_rollback_prep_down.sql
#   e1-canary-up       -> state/migrations/e1_canary_up.sql
#   e1-canary-down     -> state/migrations/e1_canary_down.sql
#   business-failure-log-20261110      -> state/migrations/007_business_failure_log_up.sql
#   business-failure-log-20261110-down -> state/migrations/007_business_failure_log_down.sql
#   eval-os-mvp-phase2      -> state/migrations/009_eval_os_up.sql
#   eval-os-mvp-phase2-down -> state/migrations/009_eval_os_down.sql
#     （C4 失败账本，董事长 2026-11-10 执行令；基建冻结下唯一允许新增的结构）
# 执行方式：TCP + migrator 专用 DB 角色（凭证 /etc/noesis/db-migrator.env，
#   root:gatesvc 0440，仅 gatesvc 可读；0400 会使组读位为 0、gatesvc 读不到，
#   与 chairman.token 0400->0440 前例同理，故用 0440）。不调用 docker；
#   gatesvc 不入 docker 组，任何 agent 账户也不入。
# 注意：migrator 仅有 public schema USAGE+CREATE 与 gate_registry SELECT；
#   004 含属主级 GRANT 与 postgres 属主函数替换，在 migrator 身份下会 fail closed
#   （须走 rollback-all.sh 属主操作员路径）；006/e1 只操作 migrator 可拥有对象，可走通。
# psql --single-transaction：单文件原子执行，失败即全回滚；-q 不输出凭证相关内容。
set -u
ENV_FILE="/etc/noesis/db-migrator.env"
[ -r "$ENV_FILE" ] || { echo "gate-action-dbmigrate: credential file unreadable: $ENV_FILE" >&2; exit 1; }
SLUG="${1:-}"
case "$SLUG" in
  cutover51-up)       FILE="/home/ubuntu/ai-team/state/migrations/004_cutover51_up.sql" ;;
  cutover51-down)     FILE="/home/ubuntu/ai-team/state/migrations/004_cutover51_down.sql" ;;
  rollback-prep-up)   FILE="/home/ubuntu/ai-team/state/migrations/006_cutover51_rollback_prep_up.sql" ;;
  rollback-prep-down) FILE="/home/ubuntu/ai-team/state/migrations/006_cutover51_rollback_prep_down.sql" ;;
  e1-canary-up)       FILE="/home/ubuntu/ai-team/state/migrations/e1_canary_up.sql" ;;
  e1-canary-down)     FILE="/home/ubuntu/ai-team/state/migrations/e1_canary_down.sql" ;;
  business-failure-log-20261110)      FILE="/home/ubuntu/ai-team/state/migrations/007_business_failure_log_up.sql" ;;
  business-failure-log-20261110-down) FILE="/home/ubuntu/ai-team/state/migrations/007_business_failure_log_down.sql" ;;
  eval-os-mvp-phase2)      FILE="/home/ubuntu/ai-team/state/migrations/009_eval_os_up.sql" ;;
  eval-os-mvp-phase2-down) FILE="/home/ubuntu/ai-team/state/migrations/009_eval_os_down.sql" ;;
  *) echo "gate-action-dbmigrate: unknown migration slug: $SLUG" >&2; exit 1 ;;
esac
[ -f "$FILE" ] || { echo "gate-action-dbmigrate: migration file missing: $FILE" >&2; exit 1; }
set -a; . "$ENV_FILE"; set +a
psql --single-transaction -v ON_ERROR_STOP=1 -q -f "$FILE" \
  || { echo "gate-action-dbmigrate: psql failed (see above); fail closed" >&2; exit 1; }
echo "gate-action-dbmigrate: OK $SLUG"
exit 0

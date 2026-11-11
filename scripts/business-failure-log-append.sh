#!/usr/bin/env bash
# business-failure-log-append.sh — C4 失败账本入账脚本（董事长 2026-11-10 执行令）
# 每次业务失败当场入账：任务进入 QUARANTINED、重试耗尽、或 verifier 拒收时，
# 由当班处置席（twt/复核席）在同一处置动作内执行本脚本，不得事后补记无证据条目。
# 用法：
#   business-failure-log-append.sh --task-id <uuid> --business <业务> \
#     --failed-step <失败步骤> --direct-cause <直接原因一句话> \
#     --category <1|2|3|4> --evidence <日志行或收据 id> [--ts <ISO时间>] \
#     [--is-fixed --retest-result <修后复测结果>] [--dry-run]
# 类别：1 任务过大或模糊；2 模型能力不足；3 需求或验收标准不清；4 基建故障。
# 硬规：--is-fixed 必须同时给 --retest-result（不得写“已修”而无复测，库里 CHECK 约束也会拦）。
# 写入通道：docker exec -u postgres ops-pg psql（DB 属主操作员路径，与 004 迁移同口径）。
# 测试用：BFL_DB=central_queue_c4test ... --dry-run 只打印 SQL 不写库。
set -u
DB="${BFL_DB:-central_queue}"
DRY_RUN=0
TASK_ID=""; BUSINESS=""; FAILED_STEP=""; DIRECT_CAUSE=""; CATEGORY=""; EVIDENCE=""
TS=""; IS_FIXED="false"; RETEST_RESULT=""
usage() { echo "用法：$0 --task-id <uuid> --business <业务> --failed-step <失败步骤> --direct-cause <直接原因一句话> --category <1|2|3|4> --evidence <日志行或收据 id> [--ts <ISO时间>] [--is-fixed --retest-result <结果>] [--dry-run]" >&2; exit 2; }
[ $# -ge 12 ] || usage
while [ $# -gt 0 ]; do
  case "$1" in
    --task-id) TASK_ID="$2"; shift 2 ;;
    --business) BUSINESS="$2"; shift 2 ;;
    --failed-step) FAILED_STEP="$2"; shift 2 ;;
    --direct-cause) DIRECT_CAUSE="$2"; shift 2 ;;
    --category) CATEGORY="$2"; shift 2 ;;
    --evidence) EVIDENCE="$2"; shift 2 ;;
    --ts) TS="$2"; shift 2 ;;
    --is-fixed) IS_FIXED="true"; shift ;;
    --retest-result) RETEST_RESULT="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "未知参数：$1" >&2; usage ;;
  esac
done
# --- 校验（失败即 exit 2，不写库）---
for v in "$TASK_ID" "$BUSINESS" "$FAILED_STEP" "$DIRECT_CAUSE" "$EVIDENCE"; do
  [ -n "$v" ] || { echo "business-failure-log-append: 必填字段为空" >&2; exit 2; }
done
case "$TASK_ID" in
  ????????-????-????-????-????????????) ;;
  *) echo "business-failure-log-append: task-id 不是 uuid：$TASK_ID" >&2; exit 2 ;;
esac
case "$CATEGORY" in
  1|2|3|4) ;;
  *) echo "business-failure-log-append: category 只允许 1/2/3/4：$CATEGORY" >&2; exit 2 ;;
esac
case "$DIRECT_CAUSE" in
  *$'\n'*) echo "business-failure-log-append: 直接原因须一句话（单行）" >&2; exit 2 ;;
esac
if [ "$IS_FIXED" = "true" ] && [ -z "$RETEST_RESULT" ]; then
  echo "business-failure-log-append: 已修必须给 --retest-result（不得写已修而无复测）" >&2; exit 2
fi
# --- 组 SQL（单行值用 $$ 引用防单引号转义问题；字段已校验无换行）---
esc() { printf '%s' "$1" | sed "s/'/''/g"; }
if [ -n "$TS" ]; then TS_SQL="'$(esc "$TS")'::timestamptz"; else TS_SQL="now()"; fi
if [ -n "$RETEST_RESULT" ]; then RETEST_SQL="'$(esc "$RETEST_RESULT")'"; else RETEST_SQL="NULL"; fi
SQL="INSERT INTO business_failure_log (ts, task_id, business, failed_step, direct_cause, category, evidence, is_fixed, retest_result) VALUES ($TS_SQL, '$(esc "$TASK_ID")', '$(esc "$BUSINESS")', '$(esc "$FAILED_STEP")', '$(esc "$DIRECT_CAUSE")', $CATEGORY, '$(esc "$EVIDENCE")', $IS_FIXED, $RETEST_SQL) RETURNING failure_id;"
if [ "$DRY_RUN" = "1" ]; then
  printf '%s\n' "$SQL"
  exit 0
fi
docker exec -i -u postgres ops-pg psql -d "$DB" -v ON_ERROR_STOP=1 -t -A -c "$SQL" \
  || { echo "business-failure-log-append: 写入失败（库约束或连接）" >&2; exit 1; }

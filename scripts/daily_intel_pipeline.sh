#!/bin/bash
# daily_intel_pipeline.sh — 情报与内容供应链入口（cron 每日 07:30，日志 logs/intel-pipeline.log）
# 过载检查点（定版 P1 登记项，2026-10-24 Twt 加）：启动前查 1 分钟负载/CPU 数；
# >60% 时本轮不采集、整轮延后并记日志。说明：intel_pipeline.py 无采集/入库相位拆分，
# 定版「只入库不采集」需相位拆分后才能精确实现，已登记偏差，相位拆分列后续项。
# 阈值可用环境变量 INTEL_CPU_THRESHOLD 覆盖（默认 60）。
TH="${INTEL_CPU_THRESHOLD:-60}"
read -r l1 _ < /proc/loadavg
NCPU=$(nproc)
PCT=$(awk -v l="$l1" -v n="$NCPU" 'BEGIN{printf "%d", (l/n)*100}')
if [ "$PCT" -ge "$TH" ]; then
  echo "$(date '+%F %T') overload-guard: cpu=${PCT}% >= ${TH}%，跳过采集相位，仅入库（用当日缓存）"
  cd /home/ubuntu/ai-team && exec python3 scripts/intel_pipeline.py --skip-collect --overload-pct "$PCT" "$@"
fi
cd /home/ubuntu/ai-team && exec python3 scripts/intel_pipeline.py "$@"

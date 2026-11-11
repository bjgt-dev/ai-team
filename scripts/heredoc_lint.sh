#!/bin/bash
# E006 检测器：扫描 .sh 文件中未加引号的 heredoc 且内容块含 $ 展开的行。
# 用法：heredoc_lint.sh <file.sh> [file2.sh ...]
# 输出：每处风险一行 "文件:行号: 未加引号的 heredoc <<DELIM，内容块含 $ 展开"；无风险无输出；退出码：有风险=1，无=0。
found=0
for f in "$@"; do
  [ -f "$f" ] || { echo "$f: 文件不存在" >&2; continue; }
  awk -v fname="$f" '
    /<<-?[[:space:]]*[A-Za-z_][A-Za-z0-9_]*[[:space:]]*$/ || /<<-?[[:space:]]*[A-Za-z_][A-Za-z0-9_]*[[:space:]]/ {
      line = $0
      # 提取分隔符：<< 或 <<- 后的标识符
      s = line
      sub(/.*<<-?[[:space:]]*/, "", s)
      sub(/[^A-Za-z0-9_].*/, "", s)
      if (s != "") { delim = s; start = NR; inblk = 1; has_dollar = 0; next }
    }
    inblk {
      if ($0 == delim) {
        if (has_dollar) { printf "%s:%d: 未加引号的 heredoc <<%s，内容块含 $ 展开\n", fname, start, delim; found = 1 }
        inblk = 0; next
      }
      if (index($0, "$") > 0) has_dollar = 1
    }
    END { exit(found ? 1 : 0) }
  ' "$f" || found=1
done
exit $found

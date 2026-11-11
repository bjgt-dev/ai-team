#!/bin/bash
# 入口画面层检查：对 X 显示 :99 截图算平均亮度并数顶层窗口。
# 全黑（亮度<3）或无任何窗口时输出 ALERT，否则 OK。供 entrypoint-watch 调用或独立 cron。
export DISPLAY=:99
SHOT=/tmp/ep-visual-shot.png
if command -v scrot >/dev/null 2>&1; then
  scrot -o "$SHOT" 2>/dev/null
elif command -v import >/dev/null 2>&1; then
  import -window root "$SHOT" 2>/dev/null
else
  echo "ALERT no-screenshot-tool"
  exit 1
fi
[ -s "$SHOT" ] || { echo "ALERT screenshot-failed"; exit 1; }
BRIGHT=$(python3 - "$SHOT" <<'PYEOF'
import sys
try:
    from PIL import Image
    im = Image.open(sys.argv[1]).convert("L")
    px = list(im.resize((32, 32)).getdata())
    print(round(sum(px) / len(px), 2))
except Exception:
    print(-1)
PYEOF
)
WINS=$(xwininfo -root -children 2>/dev/null | awk '{for(i=1;i<=NF;i++){if($i ~ /^[0-9]+x[0-9]+\+/){split($i,a,"x"); if(a[1]+0>=100) c++}}} END{print c+0}')
if [ "$BRIGHT" = "-1" ]; then
  echo "ALERT brightness-unavailable windows=$WINS"
  exit 1
fi
awk -v b="$BRIGHT" 'BEGIN{exit !(b < 3)}' && BLK=1 || BLK=0
if [ "$BLK" = "1" ] || [ "$WINS" = "0" ]; then
  echo "ALERT brightness=$BRIGHT windows=$WINS"
  exit 1
fi
echo "OK brightness=$BRIGHT windows=$WINS"
exit 0

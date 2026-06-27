#!/bin/bash
# 双击此文件即可启动 STORM 网页前端，并自动打开浏览器。
# 关闭弹出的终端窗口即停止 STORM。
ROOT="$(cd "$(dirname "$0")" && pwd)"
STREAMLIT="$ROOT/.venv/bin/streamlit"
URL="http://127.0.0.1:8501"

# 已在运行 → 直接打开浏览器
if curl -s -o /dev/null --max-time 2 "$URL/healthz"; then
  echo "STORM 已在运行，打开浏览器…"
  open "$URL"
  exit 0
fi

echo "正在启动 STORM 前端…"
cd "$ROOT/frontend/demo_light" || exit 1
"$STREAMLIT" run storm.py --server.port 8501 --server.address 127.0.0.1 --server.headless true &

# 等就绪后开浏览器
for i in $(seq 1 20); do
  sleep 1
  curl -s -o /dev/null --max-time 2 "$URL/healthz" && break
done
open "$URL"
echo ""
echo "STORM 前端运行中： $URL"
echo "关闭此终端窗口即停止。"
wait

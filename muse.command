#!/bin/bash
# 双击此文件即可启动「论文构思者 · 互动圆桌」（Co-STORM × DeepSeek）。
# 在终端里跟专家圆桌对话：回车听下一位发言，输入文字插话，q 结束并出报告。
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || exit 1
exec "$ROOT/.venv/bin/python" examples/costorm_examples/run_costorm_deepseek.py "$@"

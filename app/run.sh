#!/usr/bin/env bash
# CareerSafari 一键启动（本地 demo）
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "首次运行：创建虚拟环境（Python 3.12）并安装依赖…"
  uv venv --python 3.12 .venv
  uv pip install --python .venv/bin/python -r requirements.txt
fi

# ADMIN_TOKEN 未配置时服务端会自动生成并写入 .env（fail-closed 依然生效）
exec .venv/bin/python -m uvicorn careersafari.main:app --host 127.0.0.1 --port 8765 --reload

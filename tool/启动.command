#!/bin/bash
cd -- "$(dirname -- "$0")" || exit 1
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/Applications/ChatGPT.app/Contents/Resources:$PATH"
if [ ! -x .venv/bin/python ]; then
  python3 setup.py || { read -r -p "安装未完成，按回车关闭…"; exit 1; }
fi
.venv/bin/python launcher.py

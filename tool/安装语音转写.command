#!/bin/bash
cd -- "$(dirname -- "$0")" || exit 1
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
python3 setup.py --transcription
read -r -p "按回车关闭…"

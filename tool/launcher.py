import json
import subprocess
import sys
import urllib.request
import webbrowser
from pathlib import Path

tool = Path(__file__).resolve().parent
url = "http://127.0.0.1:8765"
try:
    with urllib.request.urlopen(url + "/api/status", timeout=5) as response:
        state = json.load(response)
    if state.get("root") == str(tool.parent):
        print("Still 已经启动，正在打开学习空间…")
        webbrowser.open(url)
        sys.exit(0)
    print("8765 端口已被其他应用占用。请关闭该应用后再启动。")
    sys.exit(1)
except (OSError, ValueError):
    pass
subprocess.run([sys.executable, str(tool / "server.py")], cwd=tool)

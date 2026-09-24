"""Install project-local dependencies; never alter system Python packages."""
import shutil
import subprocess
import sys
from pathlib import Path

tool = Path(__file__).resolve().parent
python = tool / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
uv = shutil.which("uv") or (str(Path.home() / ".local/bin/uv") if (Path.home() / ".local/bin/uv").exists() else None)
try:
    print("正在准备 Still 的本地运行环境…", flush=True)
    if not python.exists():
        if uv:
            subprocess.run([uv, "venv", "--python", "3.12", str(tool / ".venv")], check=True)
        elif sys.version_info >= (3, 10):
            subprocess.run([sys.executable, "-m", "venv", str(tool / ".venv")], check=True)
        else:
            print("需要 Python 3.10 或更新版本，或先安装 uv：https://docs.astral.sh/uv/getting-started/installation/")
            sys.exit(1)
    install = [uv, "pip", "install", "--python", str(python)] if uv else [str(python), "-m", "pip", "install"]
    subprocess.run(install + ["-r", str(tool / "requirements.txt")], check=True)
    if "--transcription" in sys.argv:
        subprocess.run(install + ["faster-whisper>=1.1,<2"], check=True)
        print("语音转写组件已安装。首次识别视频时会下载 base.en 英语模型。")
    print("\n准备完成。双击「启动.command」就可以开始学习。")
except subprocess.CalledProcessError:
    print("\n安装未完成，请确认网络连接后重试。")
    sys.exit(1)

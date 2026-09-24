import copy
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from captions import read_cues, segment_cues
from packages import ROOT, STATE, atomic_json, read_package, package_folder, save_package
import ai

JOBS = {}
LOCK = threading.RLock()


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, kind, **details):
        self.id = uuid.uuid4().hex[:12]
        self.cancelled = threading.Event()
        self.process = None
        self.data = dict(id=self.id, kind=kind, status="queued", stage="waiting", progress=0, message="准备中…", created_at=time.time(), **details)

    def update(self, **values):
        with LOCK:
            self.data.update(values)
            atomic_json(STATE / "jobs" / f"{self.id}.json", self.data)

    def check(self):
        if self.cancelled.is_set():
            raise Cancelled()


def snapshots():
    with LOCK:
        active = {key: copy.deepcopy(job.data) for key,job in JOBS.items()}
    for path in (STATE / "jobs").glob("*.json"):
        if path.stem not in active:
            try:
                data = json.loads(path.read_text())
                if data["status"] in ("running", "queued"):
                    data.update(status="interrupted", message="上次运行已中断；可重试，已完成的文件和讲解会保留")
                active[path.stem] = data
            except (OSError, ValueError):
                continue
    return sorted(active.values(), key=lambda d: d.get("created_at", 0), reverse=True)[:30]


def launch(kind, details):
    with LOCK:
        if any(j.data["status"] in ("queued", "running") for j in JOBS.values()):
            raise ValueError("已有制作任务正在进行，请完成或取消后再开始")
        job = Job(kind, **details)
        JOBS[job.id] = job
        job.update()
    def target():
        try:
            job.update(status="running")
            if kind == "download":
                download(job)
            else:
                folder = package_folder(details["folder"])
                data = read_package(folder.name)
                if not data["segments"]:
                    transcribe(folder, data, job)
                ai.analyze(folder, data, job, run)
            job.check()
            job.update(status="complete", stage="done", progress=100, message="素材包已准备好，开始学习吧")
        except Cancelled:
            job.update(status="cancelled", message="已取消，已下载的视频和已完成的讲解会保留")
        except Exception as exc:
            job.update(status="error", message=friendly_error(str(exc)))
    threading.Thread(target=target, daemon=True).start()
    return job.data


def cancel(job_id):
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise ValueError("任务不存在")
        job.cancelled.set()
        if job.process and job.process.poll() is None:
            stop(job.process)


def stop(process):
    try:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        pass


def run(command, job, input_text=None, env=None, timeout=1800, logfile=None, quiet=False):
    job.check()
    logfile = logfile or STATE / "jobs" / f"{job.id}.log"
    logfile.parent.mkdir(parents=True, exist_ok=True)
    with logfile.open("w", encoding="utf8") as log:
        process = subprocess.Popen(command, stdin=subprocess.PIPE if input_text else subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, text=True, env=env, start_new_session=os.name != "nt")
        job.process = process
        if input_text:
            try:
                process.stdin.write(input_text)
                process.stdin.close()
            except BrokenPipeError:
                pass
        started = time.monotonic()
        timed_out = False
        try:
            while process.poll() is None:
                if job.cancelled.is_set() or time.monotonic() - started > timeout:
                    timed_out = not job.cancelled.is_set()
                    stop(process)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        if os.name != "nt":
                            os.killpg(process.pid, signal.SIGKILL)
                        else:
                            process.kill()
                    break
                if not quiet:
                    tail = logfile.read_text("utf8", errors="replace")[-1600:]
                    found = re.findall(r"\[download\]\s+(\d+(?:\.\d+)?)%", tail)
                    if found:
                        value = float(found[-1])
                        job.update(progress=10 + value * .4, message=f"正在下载视频 · {value:.0f}%")
                time.sleep(.5)
            process.wait()
        finally:
            job.process = None
        job.check()
        if timed_out:
            raise RuntimeError("处理超时，已完成的内容保留，请重试")
        if process.returncode:
            tail = logfile.read_text("utf8", errors="replace")[-3500:]
            raise RuntimeError(tail)


def friendly_error(message):
    if "429" in message or "rate limit" in message.lower() or "usage limit" in message.lower():
        return "下载服务或 AI 达到用量限制，请稍后重试。已完成的内容已保存。"
    if "Sign in to confirm" in message or "not a bot" in message:
        return "YouTube 要求登录验证。可在新建素材包的高级选项中选择已登录的浏览器，然后重试。"
    if "403" in message:
        return "视频服务拒绝了下载（403）。请检查网络，或选择已登录浏览器后重试。"
    if any(word in message.lower() for word in ("timed out", "unable to download", "name resolution", "certificate verify")):
        return "无法连接视频服务，请确认当前网络可访问该链接后重试。详情见 tool/.state/jobs 中的日志。"
    return message[-1200:] or "处理失败，请重试"


def ffmpeg_path():
    system = shutil.which("ffmpeg")
    if system:
        return system
    try:
        import imageio_ffmpeg
        binary = imageio_ffmpeg.get_ffmpeg_exe()
        folder = STATE / "bin"
        folder.mkdir(exist_ok=True)
        link = folder / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if not link.exists():
            try:
                link.symlink_to(binary)
            except OSError:
                shutil.copy2(binary, link)
        return str(link)
    except ImportError:
        raise RuntimeError("缺少视频处理组件，请双击 tool/安装依赖.command")


def normalize_url(url):
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError("请输入有效的视频链接")
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or parsed.username or parsed.port not in (None, 80, 443):
        raise ValueError("请输入 YouTube 或 Bilibili 的网页链接")
    if host not in ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv"):
        raise ValueError("当前支持 YouTube 和 Bilibili 视频链接")
    if "youtube.com" in host or host == "youtu.be":
        video_id = parsed.path.strip("/") if host == "youtu.be" else parse_qs(parsed.query).get("v", [""])[0]
        if not video_id and parsed.path.startswith(("/shorts/", "/live/")):
            video_id = parsed.path.split("/")[2]
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError("请粘贴单个 YouTube 视频的链接")
        return f"https://www.youtube.com/watch?v={video_id}"
    if host != "b23.tv" and not re.search(r"/video/(BV[A-Za-z0-9]+|av\d+)", parsed.path):
        raise ValueError("请粘贴单个 Bilibili 视频的链接")
    return url.strip()


def ytdlp_command(browser=""):
    if not importlib.util.find_spec("yt_dlp"):
        raise RuntimeError("缺少下载组件，请双击 tool/安装依赖.command")
    cmd = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist", "--no-colors", "--newline", "--socket-timeout", "25", "--retries", "3", "--ffmpeg-location", ffmpeg_path()]
    node = shutil.which("node")
    if node:
        cmd += ["--js-runtimes", f"node:{node}"]
    if browser:
        if browser not in ("chrome", "safari", "firefox", "edge"):
            raise ValueError("不支持的浏览器")
        cmd += ["--cookies-from-browser", browser]
    return cmd


def choose_subtitle(info):
    for key in ("subtitles", "automatic_captions"):
        available = info.get(key, {})
        choices = [lang for lang in available if re.fullmatch(r"(?:en(?:-[A-Za-z]+)*|ai-en)", lang)]
        if choices:
            chosen = sorted(choices, key=lambda k: (k not in ("en", "en-orig"), k))[0]
            return chosen, "人工英文字幕" if key == "subtitles" else "自动英文字幕（可能有识别误差）"
    return None, "未找到英文字幕"


def download(job):
    url = normalize_url(job.data["url"])
    browser = job.data.get("browser", "")
    base = ytdlp_command(browser)
    # Stable staging path lets yt-dlp resume partially downloaded files on retry.
    import hashlib
    work = STATE / "downloads" / hashlib.sha256(url.encode()).hexdigest()[:16]
    work.mkdir(parents=True, exist_ok=True)
    template = str(work / "source.%(ext)s")
    job.update(stage="download", message="正在读取视频信息…", progress=4)
    run(base + ["--skip-download", "--write-info-json", "-o", template, url], job)
    info = json.loads((work / "source.info.json").read_text("utf8"))
    if info.get("is_live") or info.get("live_status") == "is_upcoming":
        raise ValueError("请使用已发布的普通视频，暂不支持直播")
    if info.get("duration", 0) > 4 * 3600:
        raise ValueError("单个素材包暂限 4 小时以内的视频")
    title = info.get("title") or info["id"]
    slug = re.sub(r"[^\w\- ]", "", title, flags=re.UNICODE).strip().replace(" ", "-")[:65] or "video"
    identifier = re.sub(r"[^A-Za-z0-9_-]", "", str(info["id"]))
    folder_name = f"{slug}--{identifier}"
    folder = package_folder(folder_name)
    if (folder / "manifest.json").exists():
        data = read_package(folder_name)
        job.update(folder=folder_name)
        if job.data.get("analyze", True):
            if not data["segments"]:
                transcribe(folder, data, job)
            ai.analyze(folder, data, job, run)
        return
    job.update(title=title, message="正在下载完整视频…", progress=8)
    fmt = "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/b[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b"
    run(base + ["--format", fmt, "--merge-output-format", "mp4", "-o", template, url], job)
    videos = [p for p in work.glob("source.*") if p.suffix in (".mp4", ".mkv", ".webm", ".mov") and not re.search(r"\.f\d+\.", p.name)]
    if not videos:
        raise RuntimeError("下载完成但找不到视频文件")
    source = videos[0]
    folder.mkdir(exist_ok=True)
    job.update(stage="process", folder=folder_name, message="正在准备本地视频…", progress=52)
    destination = folder / "video.mp4"
    if not destination.exists():
        # Ensure MP4 uses browser-friendly codecs, even when only VP9/AV1 was available.
        probe = subprocess.run([ffmpeg_path(), "-hide_banner", "-i", str(source)], capture_output=True, text=True)
        compatible = "Video: h264" in probe.stderr and ("Audio:" not in probe.stderr or "Audio: aac" in probe.stderr)
        codec = ["-c", "copy"] if compatible else ["-c:v", "libx264", "-preset", "fast", "-crf", "21", "-c:a", "aac"]
        temp = folder / "preparing.mp4"
        run([ffmpeg_path(), "-y", "-i", str(source), *codec, "-movflags", "+faststart", str(temp)], job, quiet=True)
        temp.replace(destination)
    lang, caption_source = choose_subtitle(info)
    segments = []
    notice = ""
    if lang:
        job.update(message="正在整理英文字幕和语句时间轴…", progress=58)
        try:
            run(base + ["--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", lang, "--sub-format", "json3/vtt/srt/best", "-o", template, url], job, quiet=True)
            files = [p for p in work.glob(f"source.{lang}.*") if p.suffix in (".json3", ".vtt", ".srt", ".json")]
            if files:
                caption = files[0]
                shutil.copy2(caption, folder / f"original{caption.suffix}")
                segments = segment_cues(read_cues(caption))
        except Cancelled:
            raise
        except Exception:
            notice = "字幕下载失败，视频已保存。可导入英文 SRT/VTT 字幕，或安装本地转写组件后重试。"
    data = {"schema_version": 1, "id": identifier, "title": title, "creator": info.get("uploader", ""), "source_url": url,
            "duration": info.get("duration", 0), "video": "video.mp4", "poster": "", "segments": segments,
            "created_at": datetime.now(timezone.utc).isoformat(), "status": "captions_ready" if segments else "needs_captions", "caption_source": caption_source, "notice": notice}
    try:
        run([ffmpeg_path(), "-y", "-ss", str(min(35, data["duration"] / 3)), "-i", str(destination), "-frames:v", "1", "-vf", "scale=960:-2", str(folder / "poster.jpg")], job, quiet=True, timeout=60)
        data["poster"] = "poster.jpg"
    except Cancelled:
        raise
    except Exception:
        pass
    save_package(folder, data)
    # The package is now self-contained; retain only small source metadata in staging.
    for p in videos:
        p.unlink(missing_ok=True)
    if not segments:
        transcribe(folder, data, job)
    if job.data.get("analyze", True):
        ai.analyze(folder, data, job, run)


def transcribe(folder, data, job):
    if not importlib.util.find_spec("faster_whisper"):
        raise RuntimeError("视频已保存，但没有可用的英文字幕。请在学习页导入英文 SRT/VTT 文件，或双击 tool/安装语音转写.command 后点击「补全 AI 讲解」。")
    job.update(stage="transcribe", message="正在本地识别英文语音；首次运行需下载语音模型…", progress=60)
    output = STATE / "jobs" / f"{job.id}-transcript.json"
    run([sys.executable, str(Path(__file__).parent / "transcribe.py"), str(folder / data["video"]), str(output)], job, quiet=True, timeout=7200)
    data["segments"] = segment_cues(json.loads(output.read_text("utf8")))
    if not data["segments"]:
        raise RuntimeError("没有识别到英文语音，请导入英文字幕")
    data.update(caption_source="本地语音识别（可能有误差）", status="captions_ready", notice="")
    save_package(folder, data)

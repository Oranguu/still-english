"""Stream local video copies into private staging, then build portable packages."""
import hashlib
import json
import math
import re
import secrets
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

import packages

MAX_BYTES = 32 * 1024 ** 3
EXTENSIONS = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi"}
LOCK = threading.RLock()
UPLOADING = set()


def import_directory(identifier, state):
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{24}", identifier):
        raise ValueError("本地视频导入编号无效")
    return packages.safe_path(packages.safe_path(state, "local-imports"), identifier)


def load_import(identifier, state):
    folder = import_directory(identifier, state)
    metadata = packages.safe_path(folder, "import.json")
    if not metadata.is_file() or metadata.stat().st_size > 16384:
        raise ValueError("本地视频导入已失效，请重新选择文件")
    data = json.loads(metadata.read_text("utf8"))
    return folder, data


def start_import(data, state):
    name, size = data.get("name"), data.get("size")
    if not isinstance(name, str) or not name or len(name) > 240 or Path(name).name != name or "\\" in name or name.startswith(".") or any(ord(c) < 32 for c in name):
        raise ValueError("请选择一个本地视频文件")
    if Path(name).suffix.lower() not in EXTENSIONS:
        raise ValueError("支持 MP4、MOV、M4V、MKV、WebM 和 AVI 视频")
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_BYTES:
        raise ValueError("视频为空或大于 32 GB")
    analyze = data.get("analyze", True)
    if not isinstance(analyze, bool):
        raise ValueError("请选择是否生成简单讲解")
    identifier = secrets.token_hex(12)
    folder = import_directory(identifier, state)
    folder.mkdir(parents=True, mode=0o700)
    packages.atomic_json(folder / "import.json", {"name": name, "size": size, "analyze": analyze, "uploaded": False})
    return {"id": identifier}


def upload(identifier, stream, length, state):
    folder, data = load_import(identifier, state)
    if length != data["size"] or not 0 < length <= MAX_BYTES:
        raise ValueError("视频大小与选择的文件不一致，请重新选择")
    temp = packages.safe_path(folder, "video.part")
    with LOCK:
        _, data = load_import(identifier, state)
        if identifier in UPLOADING or data.get("uploaded"):
            raise ValueError("这个视频正在复制或已复制完成，请继续制作")
        UPLOADING.add(identifier)
    try:
        digest = hashlib.sha256()
        with temp.open("wb") as output:
            remaining = length
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError("复制被中断，请重新选择视频；原文件未改动")
                output.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
        temp.replace(packages.safe_path(folder, "video" + Path(data["name"]).suffix.lower()))
        data.update(uploaded=True, sha256=digest.hexdigest())
        packages.atomic_json(folder / "import.json", data)
    finally:
        temp.unlink(missing_ok=True)
        with LOCK:
            UPLOADING.discard(identifier)
    return {"ok": True}


def probe(source, ffmpeg):
    result = subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-i", str(source)], capture_output=True, text=True, timeout=30)
    text = result.stderr
    duration = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", text)
    video = re.search(r"Stream #0:(\d+)[^\n]*: Video: ([^\s,]+)([^\n]*)", text)
    audio = re.findall(r"Stream #0:(\d+)(?:\(([^)]+)\))?[^\n]*: Audio: ([^\s,]+)", text)
    if not duration or not video or not audio:
        raise ValueError("没有找到可读取的视频和音轨，请选择有英文原声的视频")
    seconds = sum(float(n) * m for n, m in zip(duration.groups(), (3600, 60, 1)))
    if not math.isfinite(seconds) or not 0 < seconds <= 4 * 3600:
        raise ValueError("本地视频需要在 4 小时以内")
    selected = next((row for row in audio if row[1].lower() in {"en", "eng", "english"}), audio[0])
    return {"duration": seconds, "video_index": int(video[1]), "video_codec": video[2], "audio_index": int(selected[0]),
            "browser_video": video[2] == "h264" and bool(re.search(r"\byuv420p\b", video[3])),
            "audio_codec": selected[2], "audio_language": selected[1], "audio_tracks": len(audio)}


def prepare(identifier, state, job, run, ffmpeg):
    work, info = load_import(identifier, state)
    if not info.get("uploaded"):
        raise ValueError("视频尚未复制完整，请重新选择")
    source = packages.safe_path(work, "video" + Path(info["name"]).suffix.lower())
    package_id = "local-" + info["sha256"][:24]
    # A second upload of the same content restores its package and learning IDs.
    existing = next((row for row in packages.list_packages() if row["id"] == package_id), None)
    if existing:
        folder = packages.package_folder(existing["folder"])
        data = packages.read_package(folder.name)
        if data.get("source_sha256") != info["sha256"]:
            raise ValueError("素材编号冲突，未覆盖已有素材")
        job.update(folder=folder.name, title=data["title"])
        source.unlink(missing_ok=True)
        return folder, data
    if not source.is_file() or source.stat().st_size != info["size"]:
        raise ValueError("本地视频副本不完整，请重新选择原视频")
    job.check()
    job.update(stage="process", progress=12, message="正在检查视频和英文音轨…")
    details = probe(source, ffmpeg)
    title = Path(info["name"]).stem.replace("_", " ").strip()
    slug = re.sub(r"[^\w\- ]", "", title).strip().replace(" ", "-")[:55] or "Local-video"
    folder = packages.package_folder(slug + "--" + package_id)
    folder.mkdir(exist_ok=True)
    destination = folder / "video.mp4"
    compatible = source.suffix == ".mp4" and details["browser_video"] and details["audio_codec"] == "aac" and details["audio_tracks"] == 1
    if not destination.exists():
        job.update(message="正在准备可播放的本地视频…", progress=18)
        if compatible:
            temp = folder / "preparing.mp4"
            shutil.copy2(source, temp)
            job.check()
            temp.replace(destination)
        else:
            temp = folder / "preparing.mp4"
            codec = ["-c:v", "copy"] if details["browser_video"] else ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "21"]
            run([ffmpeg, "-nostdin", "-y", "-i", str(source), "-map", f"0:{details['video_index']}", "-map", f"0:{details['audio_index']}",
                 *codec, "-c:a", "aac", "-movflags", "+faststart", str(temp)], job, quiet=True, timeout=6 * 3600)
            temp.replace(destination)
    if not compatible:
        shutil.copy2(source, folder / ("original" + source.suffix))
    data = {"schema_version": 1, "id": package_id, "title": title, "creator": "本地英文视频", "source_kind": "local", "source_sha256": info["sha256"],
            "original_filename": info["name"], "duration": details["duration"], "video": "video.mp4", "poster": "", "segments": [],
            "created_at": datetime.now(timezone.utc).isoformat(), "status": "needs_captions", "caption_source": "等待从原音重新识别", "notice": "未读取或导入随附字幕。"}
    # Publish the playable package before ASR, retaining it if inference is interrupted.
    packages.save_package(folder, data)
    source.unlink(missing_ok=True)
    job.update(folder=folder.name, title=title)
    try:
        run([ffmpeg, "-nostdin", "-y", "-ss", str(min(35, details["duration"] / 3)), "-i", str(destination), "-frames:v", "1", "-vf", "scale=960:-2", str(folder / "poster.jpg")], job, quiet=True, timeout=60)
        data["poster"] = "poster.jpg"
        packages.save_package(folder, data)
    except Exception:
        job.check()
    return folder, data

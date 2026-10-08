import json
import math
import os
import re
import tempfile
import threading
from pathlib import Path
from captions import vtt

TOOL = Path(__file__).resolve().parent
ROOT = TOOL.parent
STATE = TOOL / ".state"
STATE.mkdir(exist_ok=True)
_LIBRARY_LOCK = threading.RLock()


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def safe_path(base, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("文件路径无效")
    rel = Path(relative)
    if rel.is_absolute() or any(part in ("..", ".") or part.startswith(".") for part in rel.parts):
        raise ValueError("素材包只能使用包内的相对路径")
    path = base / rel
    if base.is_symlink() or any((base / Path(*rel.parts[:i])).is_symlink() for i in range(1, len(rel.parts) + 1)):
        raise ValueError("素材包不能包含符号链接")
    if not path.resolve().is_relative_to(base.resolve()):
        raise ValueError("路径超出素材包")
    return path


def validate(data, folder=None):
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("不支持的素材包格式，需要 schema_version: 1")
    for key in ("id", "title", "video"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"素材包缺少 {key}")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", data["id"]):
        raise ValueError("素材包 id 格式无效")
    if folder:
        video = safe_path(folder, data["video"])
        if video.suffix.lower() not in (".mp4", ".webm", ".m4v", ".mov") or not video.is_file():
            raise ValueError("素材包缺少可播放的视频文件")
        if data.get("poster"):
            poster = safe_path(folder, data["poster"])
            if poster.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp") or not poster.is_file():
                raise ValueError("封面路径无效")
    items = data.get("segments")
    if not isinstance(items, list) or len(items) > 20000:
        raise ValueError("语句列表格式无效")
    ids, last = set(), -1
    for s in items:
        if not isinstance(s, dict) or not isinstance(s.get("id"), str) or s["id"] in ids:
            raise ValueError("语句编号缺失或重复")
        ids.add(s["id"])
        start, end = s.get("start"), s.get("end")
        if not all(isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t) for t in (start, end)) or start < 0 or end <= start or start < last:
            raise ValueError("语句时间轴无效或重叠")
        if not isinstance(s.get("en"), str) or not s["en"].strip() or not isinstance(s.get("zh", ""), str):
            raise ValueError("语句内容无效")
        if s.get("analysis") is not None and not isinstance(s["analysis"], dict):
            raise ValueError("讲解格式无效")
        last = end
    return data


def save_package(folder, data):
    validate(data, folder)
    atomic_json(folder / "manifest.json", data)
    (folder / "english.vtt").write_text(vtt(data["segments"]), encoding="utf8")
    (folder / "bilingual.vtt").write_text(vtt(data["segments"], True), encoding="utf8")


def library_root():
    """Return the private media library without changing the project root."""
    folder = safe_path(ROOT, "source")
    if folder.exists() and not folder.is_dir():
        raise ValueError("source 已存在且不是文件夹，请先处理后再启动")
    return folder


def _read_manifest(folder):
    path = safe_path(folder, "manifest.json")
    if path.stat().st_size > 30 * 1024 * 1024:
        raise ValueError("素材包目录文件过大")
    return validate(json.loads(path.read_text("utf8")), folder)


def migrate_library():
    """Move validated legacy packages once; preflight every move before writing."""
    with _LIBRARY_LOCK:
        destination = library_root()
        moves = []
        for folder in sorted(ROOT.iterdir()):
            if folder.name.startswith(".") or folder.name in {"tool", "source", "review"} or folder.is_symlink() or not folder.is_dir():
                continue
            try:
                safe_path(ROOT, folder.name)
                _read_manifest(folder)
            except (OSError, ValueError, TypeError, KeyError):
                # Unrelated folders and incomplete downloads are never moved.
                continue
            if any(path.is_symlink() for path in folder.rglob("*")):
                raise ValueError(f"素材包 {folder.name} 含有符号链接，未迁移任何素材包")
            target = destination / folder.name
            if target.exists() or target.is_symlink():
                raise ValueError(f"source 中已存在同名素材包 {folder.name}，未迁移任何素材包；请先保留两份数据并解决重名")
            moves.append((folder, target))
        destination.mkdir(exist_ok=True)
        for folder, target in moves:
            if target.exists() or target.is_symlink():
                raise ValueError(f"source 中已出现同名素材包 {folder.name}，迁移已停止，未覆盖文件")
            folder.rename(target)
        return [target.name for _, target in moves]


def list_packages():
    result = []
    library = library_root()
    if not library.exists():
        return result
    for folder in sorted(library.iterdir()):
        if not folder.is_dir() or folder.name.startswith(".") or folder.name == "tool" or folder.is_symlink():
            continue
        try:
            data = read_package(folder.name)
            result.append({k: data.get(k) for k in ("id", "title", "creator", "duration", "poster", "status", "created_at", "source_url", "caption_source", "notice") } | {"folder": folder.name, "count": len(data["segments"]), "analyzed": sum(bool(s.get("analysis")) for s in data["segments"]), "detailed": sum(isinstance(s.get("analysis_version"), (int, float)) and not isinstance(s.get("analysis_version"), bool) and s["analysis_version"] >= 2 for s in data["segments"])})
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return result


def package_folder(name):
    if not isinstance(name, str) or Path(name).name != name or name.startswith(".") or name == "tool":
        raise ValueError("素材包名称无效")
    return safe_path(library_root(), name)


def read_package(name):
    return _read_manifest(package_folder(name))

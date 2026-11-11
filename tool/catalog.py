"""Local library organization and cached subtitle search, without AI calls."""
import hashlib
import json
import re
import threading
import unicodedata
from collections import Counter, OrderedDict
from pathlib import Path
from urllib.parse import urlparse

import packages

LOCK = threading.RLock()
CACHE = OrderedDict()
PRESETS = ["Vlog", "电影", "剧集", "访谈 / 播客", "演讲", "课程", "长视频", "未分类"]
CATEGORY_RULES = [
    ("课程", r"\b(tutorial|lesson|course|lecture|masterclass)\b|教程|课程|教学"),
    ("访谈 / 播客", r"\b(interview|podcast)\b|访谈|播客"),
    ("演讲", r"\b(tedx?|speech|keynote)\b|演讲"),
    ("剧集", r"\bs\d{1,2}e\d{1,3}\b|\b(episode|sitcom)\b|剧集|第.{1,4}集"),
    ("电影", r"\b(movie|feature film|short film)\b|电影"),
    ("Vlog", r"\bvlogs?\b|\bday in (my|the) life\b|生活记录|日常记录"),
]
TOPIC_RULES = [
    ("成长与心态", r"\b(mindset|balance|bored|confidence|motivation|anxiety)\b|giving up|self.care|心态|成长|焦虑"),
    ("日常生活", r"\b(routine|daily|morning|everyday)\b|日常|生活"),
    ("旅行", r"\b(travel|trip|vacation|journey)\b|旅行|旅游"),
    ("学习", r"\b(study|studying|learning|school|university)\b|学习|学校"),
    ("工作", r"\b(work|career|office|job)\b|工作|职场"),
    ("美食", r"\b(food|cooking|recipe|baking)\b|美食|烹饪"),
    ("运动", r"\b(workout|fitness|gym|running|yoga)\b|健身|运动"),
    ("影像与科技", r"\b(camera|filmmaking|photography|technology|software)\b|相机|摄影|科技"),
]


def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", str(text or "")).casefold().replace("’", "'").split())


def label(value):
    if not isinstance(value, str) or any(unicodedata.category(c).startswith("C") for c in value):
        raise ValueError("分类和标签须为普通文字")
    value = " ".join(unicodedata.normalize("NFKC", value).split())
    if not value or len(value) > 32:
        raise ValueError("分类和每个标签须为 1–32 个字")
    return value


def tags_list(values):
    if not isinstance(values, list) or len(values) > 16:
        raise ValueError("每份素材最多添加 16 个标签")
    result, seen = [], set()
    for value in values:
        value = label(value)
        key = normalize(value)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def defaults(data):
    title = normalize(data.get("title"))
    category = next((name for name, pattern in CATEGORY_RULES if re.search(pattern, title)), None)
    duration = data.get("duration") or 0
    duration = duration if isinstance(duration, (int, float)) and not isinstance(duration, bool) else 0
    category = category or ("长视频" if duration >= 3600 else "未分类")
    try:
        host = (urlparse(str(data.get("source_url") or "")).hostname or "").lower()
    except ValueError:
        host = ""
    tags = []
    if data.get("source_kind") == "local":
        tags.append("本地视频")
    elif host in ("youtu.be", "youtube.com", "www.youtube.com", "m.youtube.com"):
        tags.append("YouTube")
    elif host in ("b23.tv", "bilibili.com", "www.bilibili.com", "m.bilibili.com"):
        tags.append("Bilibili")
    if duration > 0:
        tags.append("10 分钟内" if duration <= 600 else "10–30 分钟" if duration <= 1800 else "30–60 分钟" if duration < 3600 else "1 小时以上")
    tags.extend(name for name, pattern in TOPIC_RULES if re.search(pattern, title))
    return {"category": category, "tags": tags, "automatic": True}


def preference_path(state, identifier):
    key = hashlib.sha256(identifier.encode("utf8")).hexdigest()
    return packages.safe_path(Path(state), f"catalog/{key}.json")


def preferences(state, identifier):
    path = preference_path(state, identifier)
    if not path.exists():
        return None
    try:
        if path.stat().st_size > 16_000:
            raise ValueError()
        data = json.loads(path.read_text("utf8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1 or data.get("package_id") != identifier:
            raise ValueError()
        if data.get("automatic") is True:
            return None
        return {"category": label(data["category"]), "tags": tags_list(data["tags"]), "automatic": False}
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise ValueError("分类标签文件无法读取，原文件已保留；请先备份并检查 tool/.state/catalog") from exc


def organize(request, state):
    with LOCK:
        data = packages.read_package(request.get("folder"))
        if request.get("package_id") != data["id"]:
            raise ValueError("素材已经变化，请刷新后再整理")
        preferences(state, data["id"])  # Never overwrite an unreadable record.
        if "reset" in request and not isinstance(request["reset"], bool):
            raise ValueError("自动整理选项无效")
        values = defaults(data) if request.get("reset") else {"category": label(request.get("category")), "tags": tags_list(request.get("tags")), "automatic": False}
        packages.atomic_json(preference_path(state, data["id"]), {"schema_version": 1, "package_id": data["id"], **values})
        return {"organization": values}


def indexed(folder):
    manifest = packages.safe_path(folder, "manifest.json")
    stat = manifest.stat()
    key = str(folder.resolve())
    signature = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
    entry = CACHE.get(key)
    if entry and entry[0] == signature:
        CACHE.move_to_end(key)
        row, segments = entry[1:]
        if not packages.safe_path(folder, row["video"]).is_file() or (row.get("poster") and not packages.safe_path(folder, row["poster"]).is_file()):
            raise ValueError("素材文件不存在")
        return row.copy(), segments
    data = packages.read_package(folder.name)
    fields = ("id", "title", "creator", "duration", "video", "poster", "status", "created_at", "source_url", "source_kind", "caption_source", "notice")
    row = {k: data.get(k) for k in fields}
    row.update(folder=folder.name, count=len(data["segments"]), caption_count=len(data.get("subtitle_segments", data["segments"])), analyzed=sum(bool(s.get("analysis")) for s in data["segments"]), suggested=defaults(data))
    segments = [{"id": s["id"], "start": s["start"], "en": s["en"], "zh": s.get("zh", ""), "search": normalize(s["en"] + " " + s.get("zh", ""))} for s in data["segments"]]
    CACHE[key] = (signature, row, segments)
    while len(CACHE) > 128:
        CACHE.popitem(last=False)
    return row.copy(), segments


def library(state, query="", category="", tags=(), sort="recent"):
    if not isinstance(query, str) or len(query) > 200 or len(query.split()) > 16:
        raise ValueError("请使用 200 字以内、最多 16 个关键词搜索")
    if sort not in ("recent", "title", "short", "long", "relevance"):
        raise ValueError("排序选项无效")
    selected_category = normalize(label(category)) if category else ""
    selected_tags = {normalize(tag) for tag in tags_list(list(tags))}
    terms = normalize(query).split()
    result, categories, category_names, tag_counts, tag_names = [], Counter(), {}, Counter(), {}
    total = 0
    root = packages.library_root()
    with LOCK:
        folders = sorted(root.iterdir()) if root.exists() else []
        current_paths = {str(f.resolve()) for f in folders if f.is_dir() and not f.is_symlink() and not f.name.startswith(".")}
        for key in list(CACHE):
            if key not in current_paths:
                del CACHE[key]
        for folder in folders:
            if folder.is_symlink() or not folder.is_dir() or folder.name.startswith("."):
                continue
            try:
                row, segments = indexed(folder)
            except (ValueError, OSError, TypeError, KeyError):
                continue
            total += 1
            try:
                organization = preferences(state, row["id"]) or row["suggested"]
            except ValueError as exc:
                organization = row["suggested"]
                row["catalog_error"] = str(exc)
            row.update(organization)
            category_key = normalize(row["category"])
            category_names.setdefault(category_key, row["category"])
            categories[category_key] += 1
            for tag in row["tags"]:
                key = normalize(tag)
                tag_names.setdefault(key, tag)
                tag_counts[key] += 1
            if selected_category and normalize(row["category"]) != selected_category:
                continue
            if not selected_tags.issubset({normalize(tag) for tag in row["tags"]}):
                continue
            metadata = normalize(" ".join(str(row.get(k) or "") for k in ("title", "creator", "category")) + " " + " ".join(row["tags"]))
            missing = {term for term in terms if term not in metadata}
            score, best, best_hits, match_count = sum(term in metadata for term in terms) * 5, None, 0, 0
            for segment in segments if terms else ():
                hits = sum(term in segment["search"] for term in terms)
                missing.difference_update(term for term in tuple(missing) if term in segment["search"])
                if hits:
                    match_count += 1
                    if hits > best_hits:
                        best, best_hits = segment, hits
            if missing:
                continue
            row["match"] = {k: best[k] for k in ("id", "start", "en", "zh")} if best else None
            row["match_count"] = match_count
            row["relevance"] = score + best_hits
            result.append(row)
    def title_key(row):
        return (normalize(row["title"]), row["id"], row["folder"])
    def duration(row):
        value = row.get("duration")
        return value if isinstance(value, (float, int)) else 0
    if sort == "title":
        result.sort(key=title_key)
    elif sort in ("short", "long"):
        result.sort(key=lambda r: (duration(r) * (-1 if sort == "long" else 1), title_key(r)))
    else:
        result.sort(key=lambda r: (r["relevance"] if sort == "relevance" else 0, str(r.get("created_at") or ""), title_key(r)), reverse=True)
    return {"packages": result, "total": total, "matched": len(result), "categories": [{"name": category_names[name], "count": count} for name, count in sorted(categories.items(), key=lambda item: (item[0] == "未分类", item[0]))], "tags": [{"name": tag_names[key], "count": count} for key, count in sorted(tag_counts.items(), key=lambda item: (-item[1], item[0]))], "presets": PRESETS}

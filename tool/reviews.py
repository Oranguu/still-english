"""Private, durable review cards with validated quotations and source snapshots."""
import copy
import hashlib
import json
import logging
import math
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import packages

_LOCK = threading.RLock()
_WARNED = set()
_ID = re.compile(r"[a-f0-9]{32}")
_FIELD = re.compile(r"analysis(?:\.(?:[A-Za-z_][A-Za-z0-9_]*|0|[1-9][0-9]*)){1,12}")
MAX_NOTE = 10000
MAX_CONTEXT = 20000
MAX_QUOTE = 12000
MAX_FILE = 1024 * 1024
_CATEGORIES = {"word", "phrase", "sentence"}


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _items_dir():
    root = packages.safe_path(packages.ROOT, "review")
    items = packages.safe_path(root, "items")
    root.mkdir(mode=0o700, exist_ok=True)
    items.mkdir(mode=0o700, exist_ok=True)
    return items


def _item_path(item_id):
    if not isinstance(item_id, str) or not _ID.fullmatch(item_id):
        raise ValueError("复习卡片编号无效")
    return packages.safe_path(_items_dir(), item_id + ".json")


def _note(value):
    if not isinstance(value, str) or len(value) > MAX_NOTE:
        raise ValueError("笔记格式无效或超过 10000 字")
    return value


def _selection(value, context=None):
    if not isinstance(value, dict):
        raise ValueError("划线内容格式无效")
    field = value.get("field")
    if not isinstance(field, str) or len(field) > 240 or not _FIELD.fullmatch(field):
        raise ValueError("只能保存精讲中的文字")
    start, end, quote = value.get("start"), value.get("end"), value.get("quote")
    if context is None:
        context = value.get("context")
    if not isinstance(context, str) or len(context) > MAX_CONTEXT:
        raise ValueError("精讲原文格式无效或内容过长")
    if not all(isinstance(n, int) and not isinstance(n, bool) for n in (start, end)) or not 0 <= start < end <= len(context):
        raise ValueError("划线范围无效，请重新选择文字")
    if not isinstance(quote, str) or not quote.strip() or context[start:end] != quote:
        raise ValueError("精讲已更新或划线文字不匹配，请刷新并重新选择")
    return {"field": field, "start": start, "end": end, "quote": quote, "context": context}


def _field_context(segment, field):
    if not isinstance(field, str) or len(field) > 240 or not _FIELD.fullmatch(field):
        raise ValueError("只能保存精讲中的文字")
    value = segment
    for part in field.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", part) and len(part) < 8 and int(part) < len(value):
            value = value[int(part)]
        else:
            raise ValueError("精讲内容已变更，请刷新后重新划线")
    if not isinstance(value, str):
        raise ValueError("请选择精讲中的具体文字")
    return value


def _selection_key(selection):
    return (selection["field"], selection["start"], selection["end"], selection["quote"], selection["context"])


def _infer_category(item, analysis=None):
    selections = item["selections"]
    fields = [s["field"].split(".") for s in selections]
    if not fields or any(len(parts) < 4 for parts in fields):
        return "sentence"
    entries = {tuple(parts[:3]) for parts in fields}
    if len(entries) != 1:
        return "sentence"
    if all(parts[3] in {"example", "example_zh"} for parts in fields):
        return "sentence"
    if fields[0][1] == "phrases":
        return "phrase"
    if fields[0][1] != "vocabulary":
        return "sentence"
    terms = [s["context"] for s in selections if s["field"].endswith(".term")]
    if not terms and isinstance(analysis, dict):
        vocabulary = analysis.get("vocabulary")
        index = fields[0][2]
        if isinstance(vocabulary, list) and index.isdigit() and int(index) < len(vocabulary) and isinstance(vocabulary[int(index)], dict):
            term = vocabulary[int(index)].get("term")
            if isinstance(term, str):
                terms.append(term)
    return "phrase" if any(len(re.findall(r"[A-Za-z]+(?:['’-][A-Za-z]+)*", term)) > 1 for term in terms) else "word"


def _matching_analysis(item):
    """Legacy summaries may use a source only when every saved context still agrees."""
    source = item["source"]
    try:
        package = packages.read_package(source["folder"])
        if package["id"] != source["package_id"]:
            return None
        segment = next((s for s in package["segments"] if s["id"] == source["segment_id"]), None)
        if segment and all(_field_context(segment, s["field"]) == s["context"] for s in item["selections"]):
            return segment.get("analysis")
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


def _english_prefix(text):
    if not isinstance(text, str):
        return ""
    text = re.split(r"[\u3400-\u9fff]", text, maxsplit=1)[0].strip().rstrip(" /|—–-（(:：")
    return text if re.search(r"[A-Za-z]", text) else ""


def _infer_english(item, analysis=None):
    if item["kind"] == "favorite":
        return item["source"]["en"]
    selections = item["selections"]
    fields = [s["field"].split(".") for s in selections]
    same_entry = all(len(parts) >= 4 for parts in fields) and len({tuple(parts[:3]) for parts in fields}) == 1
    if same_entry and fields[0][1] in {"vocabulary", "phrases", "sentence_parts"}:
        example_only = all(parts[3] in {"example", "example_zh"} for parts in fields)
        key = "example" if example_only else "chunk" if fields[0][1] == "sentence_parts" else "term"
        candidates = [s["context"] for s in selections if s["field"].endswith("." + key)]
        entries = analysis.get(fields[0][1]) if isinstance(analysis, dict) else None
        index = fields[0][2]
        if isinstance(entries, list) and index.isdigit() and int(index) < len(entries) and isinstance(entries[int(index)], dict):
            candidates.insert(0, entries[int(index)].get(key))
        for candidate in candidates:
            if english := _english_prefix(candidate):
                return english
    selected = [_english_prefix(s["quote"]) for s in selections]
    return "\n".join(text for text in selected if text) or item["source"]["en"]


def _validated_item(item, item_id):
    if not isinstance(item, dict) or item.get("id") != item_id:
        raise ValueError("复习卡片编号与文件不一致")
    _note(item.get("note"))
    source = item.get("source")
    if not isinstance(source, dict) or any(not isinstance(source.get(k), str) for k in ("folder", "package_id", "package_title", "segment_id", "en", "zh")):
        raise ValueError("复习卡片缺少来源快照")
    if not all(isinstance(source.get(k), (int, float)) and not isinstance(source[k], bool) and math.isfinite(source[k]) for k in ("start", "end")) or source["start"] < 0 or source["end"] <= source["start"]:
        raise ValueError("复习卡片来源时间无效")
    selections = item.get("selections")
    item.setdefault("kind", "excerpt")
    item.setdefault("active", True)
    if item["kind"] not in {"excerpt", "favorite"} or not isinstance(item["active"], bool):
        raise ValueError("复习卡片类型或状态无效")
    if item["kind"] == "favorite":
        if selections != []:
            raise ValueError("收藏语句不能包含划线片段")
        quote = source["en"]
    else:
        if not isinstance(selections, list) or not 1 <= len(selections) <= 30:
            raise ValueError("复习卡片缺少划线内容")
        normalized = [_selection(s) for s in selections]
        quote = "\n".join(s["quote"] for s in normalized)
    if item.get("quote") != quote or len(quote) > MAX_QUOTE:
        raise ValueError("复习卡片划线内容不一致")
    analysis = _matching_analysis(item) if item["kind"] == "excerpt" and ("category" not in item or "english" not in item) else None
    if "category" not in item:
        item["category"] = "sentence" if item["kind"] == "favorite" else _infer_category(item, analysis)
    if not isinstance(item["category"], str) or item["category"] not in _CATEGORIES or item["kind"] == "favorite" and item["category"] != "sentence":
        raise ValueError("复习卡片分类无效")
    if "english" not in item:
        item["english"] = _infer_english(item, analysis)
    if not isinstance(item["english"], str) or len(item["english"]) > MAX_CONTEXT:
        raise ValueError("复习卡片英文摘要无效")
    count = item.get("review_count")
    if not isinstance(item.get("mastered"), bool) or not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise ValueError("复习卡片学习状态无效")
    for name in ("created_at", "updated_at", "last_reviewed_at"):
        value = item.get(name)
        if name == "last_reviewed_at" and value is None:
            continue
        if not isinstance(value, str) or len(value) > 40:
            raise ValueError("复习卡片日期无效")
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError()
        except ValueError:
            raise ValueError("复习卡片日期无效") from None
    return item


def _read_item(item_id):
    path = _item_path(item_id)
    if not path.is_file():
        raise ValueError("找不到这张复习卡片")
    if path.stat().st_size > MAX_FILE:
        raise ValueError("复习卡片文件过大")
    return _validated_item(json.loads(path.read_text("utf8")), item_id)


def _save(item, new=False):
    path = _item_path(item["id"])
    _validated_item(item, item["id"])
    if len(json.dumps(item, ensure_ascii=False, indent=2, allow_nan=False).encode("utf8")) > MAX_FILE:
        raise ValueError("划线内容过多，请分成多张卡片保存")
    if new and path.exists():
        raise ValueError("复习卡片编号冲突，请重试")
    packages.atomic_json(path, item)


def _all_reviews():
    """A damaged card remains on disk; all other cards remain available."""
    with _LOCK:
        result = []
        for path in sorted(_items_dir().glob("*.json")):
            if not _ID.fullmatch(path.stem):
                continue
            try:
                result.append(_read_item(path.stem))
            except (OSError, ValueError, TypeError, KeyError, RecursionError):
                try:
                    modified = path.lstat().st_mtime_ns
                except OSError:
                    modified = 0
                marker = (str(path), modified)
                if marker not in _WARNED:
                    logging.warning("复习卡片 %s 无法读取，已跳过并保留原文件，请检查此文件或从备份恢复。", path.name)
                    _WARNED.add(marker)
        return sorted(result, key=lambda item: (item["created_at"], item["id"]), reverse=True)


def _progress_path(package_id, state_dir=None):
    state = Path(state_dir) if state_dir is not None else packages.ROOT / "tool" / ".state"
    try:
        relative = state.relative_to(packages.ROOT)
    except ValueError:
        raise ValueError("学习记录目录必须位于当前工程内") from None
    if packages.ROOT.is_symlink() or any(part in {"..", "."} for part in relative.parts):
        raise ValueError("学习记录路径无效")
    current = packages.ROOT
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError("学习记录不能使用符号链接")
    progress = packages.safe_path(state, "progress")
    name = hashlib.sha256(package_id.encode()).hexdigest()[:24] + ".json"
    return packages.safe_path(progress, name)


def progress_path(folder, state_dir=None):
    return _progress_path(packages.read_package(folder)["id"], state_dir)


def _validate_progress(progress):
    if not isinstance(progress, dict):
        raise ValueError("学习记录格式无效")
    favorites = progress.get("favorites")
    if "favorites" in progress and (not isinstance(favorites, list) or len(favorites) > 20000 or any(not isinstance(s, str) or not s or len(s) > 200 for s in favorites)):
        raise ValueError("收藏语句列表格式无效")
    return progress


def _read_progress(package_id, state_dir=None):
    path = _progress_path(package_id, state_dir)
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("学习记录文件格式无效或过大")
    return _validate_progress(json.loads(path.read_text("utf8")))


def read_progress(folder, state_dir=None):
    with _LOCK:
        package = packages.read_package(folder)
        return _read_progress(package["id"], state_dir) or {}


def _source(folder, package, segment):
    return {"folder": folder, "package_id": package["id"], "package_title": package["title"], "segment_id": segment["id"],
            "start": segment["start"], "end": segment["end"], "en": segment["en"], "zh": segment.get("zh", "")}


def _sync_package_favorites(folder, package, progress, items):
    if "favorites" not in progress:
        return
    favorites = set(progress["favorites"])
    existing = {item["source"]["segment_id"]: item for item in items if item["kind"] == "favorite" and item["source"]["package_id"] == package["id"]}
    for segment_id, item in existing.items():
        active = segment_id in favorites
        if item["active"] != active:
            item.update(active=active, updated_at=_now())
            _save(item)
    notes = progress.get("notes", {})
    for segment in package["segments"]:
        if segment["id"] not in favorites or segment["id"] in existing:
            continue
        note = notes.get(segment["id"], "") if isinstance(notes, dict) else ""
        now = _now()
        item = {"id": uuid.uuid4().hex, "kind": "favorite", "category": "sentence", "active": True,
                "source": _source(folder, package, segment), "selections": [], "quote": segment["en"], "english": segment["en"],
                "note": note[:MAX_NOTE] if isinstance(note, str) else "", "created_at": now, "updated_at": now,
                "last_reviewed_at": None, "review_count": 0, "mastered": False}
        _save(item, new=True)
        items.append(item)


def sync_favorites(state_dir=None):
    """Backfill only readable, available sources; missing data never removes cards."""
    with _LOCK:
        items = _all_reviews()
        for summary in packages.list_packages():
            try:
                package = packages.read_package(summary["folder"])
                progress = _read_progress(package["id"], state_dir)
                if progress is not None:
                    _sync_package_favorites(summary["folder"], package, progress, items)
            except (OSError, ValueError, TypeError, KeyError, RecursionError):
                marker = ("favorites", summary["id"])
                if marker not in _WARNED:
                    logging.warning("一份素材的收藏记录暂时无法同步；现有复习卡片和学习记录均已保留，请检查本地文件。")
                    _WARNED.add(marker)


def list_reviews(state_dir=None):
    with _LOCK:
        sync_favorites(state_dir)
        return [item for item in _all_reviews() if item["active"]]


def save_progress(folder, progress, state_dir=None):
    with _LOCK:
        _validate_progress(progress)
        package = packages.read_package(folder)
        saved = _read_progress(package["id"], state_dir)
        updated = copy.deepcopy(progress)
        if "favorites" not in updated and saved is not None and "favorites" in saved:
            updated["favorites"] = saved["favorites"]
        items = _all_reviews()
        packages.atomic_json(_progress_path(package["id"], state_dir), updated)
        _sync_package_favorites(folder, package, updated, items)
        return {"ok": True}


def add_review(data):
    with _LOCK:
        if not isinstance(data, dict):
            raise ValueError("复习卡片请求格式无效")
        folder, segment_id = data.get("folder"), data.get("segment_id")
        if not isinstance(segment_id, str) or not segment_id or len(segment_id) > 200:
            raise ValueError("语句编号无效")
        package = packages.read_package(folder)
        segment = next((s for s in package["segments"] if s["id"] == segment_id), None)
        if segment is None:
            raise ValueError("找不到原语句，请刷新后再保存")
        raw = data.get("selections")
        if not isinstance(raw, list) or not 1 <= len(raw) <= 30:
            raise ValueError("每次请选择 1 至 30 处精讲文字")
        selections = []
        seen = set()
        for value in raw:
            if not isinstance(value, dict):
                raise ValueError("划线内容格式无效")
            selection = _selection(value, _field_context(segment, value.get("field")))
            key = _selection_key(selection)
            if key not in seen:
                seen.add(key)
                selections.append(selection)
        quote = "\n".join(s["quote"] for s in selections)
        if len(quote) > MAX_QUOTE:
            raise ValueError("每张复习卡片最多保存 12000 字，请分开保存")
        note = _note(data.get("note", ""))
        for item in _all_reviews():
            source = item["source"]
            if item["kind"] == "excerpt" and source["package_id"] == package["id"] and source["segment_id"] == segment_id and sorted(_selection_key(s) for s in item["selections"]) == sorted(seen):
                return {"item": item, "existing": True}
        now = _now()
        item = {
            "id": uuid.uuid4().hex, "kind": "excerpt", "active": True,
            "source": _source(folder, package, segment),
            "selections": selections, "quote": quote, "note": note,
            "created_at": now, "updated_at": now, "last_reviewed_at": None, "review_count": 0, "mastered": False,
        }
        item["category"] = _infer_category(item, segment.get("analysis"))
        item["english"] = _infer_english(item, segment.get("analysis"))
        _save(item, new=True)
        return {"item": copy.deepcopy(item), "existing": False}


def update_review(data):
    with _LOCK:
        if not isinstance(data, dict):
            raise ValueError("复习卡片请求格式无效")
        item = _read_item(data.get("id"))
        if not any(k in data for k in ("note", "mastered", "action", "category")):
            raise ValueError("请选择要更新的笔记或复习状态")
        if "note" in data:
            item["note"] = _note(data["note"])
        if "mastered" in data:
            if not isinstance(data["mastered"], bool):
                raise ValueError("掌握状态无效")
            item["mastered"] = data["mastered"]
        if "category" in data:
            if not isinstance(data["category"], str) or data["category"] not in _CATEGORIES or item["kind"] == "favorite" and data["category"] != "sentence":
                raise ValueError("请选择单词、短语或句子；收藏语句固定归为句子")
            item["category"] = data["category"]
        now = _now()
        if "action" in data:
            if data["action"] != "reviewed":
                raise ValueError("复习操作无效")
            item["review_count"] += 1
            item["last_reviewed_at"] = now
        item["updated_at"] = now
        _save(item)
        return {"item": item}


def delete_review(data, state_dir=None):
    with _LOCK:
        if not isinstance(data, dict):
            raise ValueError("复习卡片请求格式无效")
        path = _item_path(data.get("id"))
        if not path.exists():
            return {"ok": True}
        item = _read_item(data["id"])
        if item["kind"] == "favorite":
            source = item["source"]
            progress = _read_progress(source["package_id"], state_dir)
            if progress is not None and "favorites" in progress:
                progress["favorites"] = [segment_id for segment_id in progress["favorites"] if segment_id != source["segment_id"]]
                packages.atomic_json(_progress_path(source["package_id"], state_dir), progress)
            item.update(active=False, updated_at=_now())
            _save(item)
        else:
            path.unlink()
        return {"ok": True}

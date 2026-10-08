"""Private, durable review cards with validated quotations and source snapshots."""
import copy
import json
import logging
import math
import re
import threading
import uuid
from datetime import datetime, timezone

import packages

_LOCK = threading.RLock()
_WARNED = set()
_ID = re.compile(r"[a-f0-9]{32}")
_FIELD = re.compile(r"analysis(?:\.(?:[A-Za-z_][A-Za-z0-9_]*|0|[1-9][0-9]*)){1,12}")
MAX_NOTE = 10000
MAX_CONTEXT = 20000
MAX_QUOTE = 12000
MAX_FILE = 1024 * 1024


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
    if not isinstance(selections, list) or not 1 <= len(selections) <= 30:
        raise ValueError("复习卡片缺少划线内容")
    normalized = [_selection(s) for s in selections]
    quote = "\n".join(s["quote"] for s in normalized)
    if item.get("quote") != quote or len(quote) > MAX_QUOTE:
        raise ValueError("复习卡片划线内容不一致")
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


def list_reviews():
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
        for item in list_reviews():
            source = item["source"]
            if source["package_id"] == package["id"] and source["segment_id"] == segment_id and sorted(_selection_key(s) for s in item["selections"]) == sorted(seen):
                return {"item": item, "existing": True}
        now = _now()
        item = {
            "id": uuid.uuid4().hex,
            "source": {"folder": folder, "package_id": package["id"], "package_title": package["title"], "segment_id": segment_id,
                       "start": segment["start"], "end": segment["end"], "en": segment["en"], "zh": segment.get("zh", "")},
            "selections": selections, "quote": quote, "note": note,
            "created_at": now, "updated_at": now, "last_reviewed_at": None, "review_count": 0, "mastered": False,
        }
        _save(item, new=True)
        return {"item": copy.deepcopy(item), "existing": False}


def update_review(data):
    with _LOCK:
        if not isinstance(data, dict):
            raise ValueError("复习卡片请求格式无效")
        item = _read_item(data.get("id"))
        if not any(k in data for k in ("note", "mastered", "action")):
            raise ValueError("请选择要更新的笔记或复习状态")
        if "note" in data:
            item["note"] = _note(data["note"])
        if "mastered" in data:
            if not isinstance(data["mastered"], bool):
                raise ValueError("掌握状态无效")
            item["mastered"] = data["mastered"]
        now = _now()
        if "action" in data:
            if data["action"] != "reviewed":
                raise ValueError("复习操作无效")
            item["review_count"] += 1
            item["last_reviewed_at"] = now
        item["updated_at"] = now
        _save(item)
        return {"item": item}


def delete_review(data):
    with _LOCK:
        if not isinstance(data, dict):
            raise ValueError("复习卡片请求格式无效")
        _item_path(data.get("id")).unlink(missing_ok=True)
        return {"ok": True}

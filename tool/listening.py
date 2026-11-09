"""Sentence-level listening practice, separate from text-review mastery."""
import copy
import hashlib
import json
import logging
import math
import re
import uuid
from datetime import datetime, timedelta, timezone

import packages
import reviews

_LOCK = reviews._LOCK
_ID = re.compile(r"[a-f0-9]{32}")
_REASONS = {"meaning", "sound", "speed"}
_RESULTS = {"understood", "hinted", "missed"}
_MAX_FILE = 256 * 1024
_WARNED = set()


def _now():
    return datetime.now(timezone.utc)


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _identifier(package_id, segment_id):
    key = json.dumps([package_id, segment_id], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(key.encode("utf8")).hexdigest()[:32]


def _directory():
    root = packages.safe_path(packages.ROOT, "review")
    return packages.safe_path(root, "listening")


def _path(item_id):
    if not isinstance(item_id, str) or not _ID.fullmatch(item_id):
        raise ValueError("听力记录编号无效")
    return packages.safe_path(_directory(), item_id + ".json")


def _attempt_id(value):
    if not isinstance(value, str) or len(value) not in (32, 36):
        raise ValueError("本次练习编号无效，请重新开始")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise ValueError("本次练习编号无效，请重新开始") from None
    if parsed.int == 0 or value.lower() not in (parsed.hex, str(parsed)):
        raise ValueError("本次练习编号无效，请重新开始")
    return parsed.hex


def _new_record(source):
    return {"schema_version": 1, "id": _identifier(source["package_id"], source["segment_id"]), "source": copy.deepcopy(source),
            "difficulty": None, "listening_count": 0, "independent_streak": 0, "last_result": None,
            "last_reviewed_at": None, "due_at": None, "interval_days": 0, "attempts": []}


def _validated(record, item_id):
    if not isinstance(record, dict) or record.get("id") != item_id or record.get("schema_version", 1) != 1:
        raise ValueError("听力记录格式无效")
    source = record.get("source")
    if not isinstance(source, dict) or any(not isinstance(source.get(key), str) for key in ("folder", "package_id", "package_title", "segment_id", "en", "zh")):
        raise ValueError("听力记录缺少原句快照")
    if not source["package_id"] or not source["segment_id"] or not source["en"].strip() or item_id != _identifier(source["package_id"], source["segment_id"]):
        raise ValueError("听力记录与来源语句不一致")
    if not all(isinstance(source.get(key), (int, float)) and not isinstance(source[key], bool) and math.isfinite(source[key]) for key in ("start", "end")) or source["start"] < 0 or source["end"] <= source["start"]:
        raise ValueError("听力记录原句时间无效")
    defaults = _new_record(source)
    for key, value in defaults.items():
        record.setdefault(key, value)
    if record["difficulty"] is not None and (not isinstance(record["difficulty"], str) or record["difficulty"] not in _REASONS):
        raise ValueError("听力困难类型无效")
    if record["last_result"] is not None and (not isinstance(record["last_result"], str) or record["last_result"] not in _RESULTS):
        raise ValueError("听力练习结果无效")
    for key in ("listening_count", "independent_streak", "interval_days"):
        if not isinstance(record[key], int) or isinstance(record[key], bool) or record[key] < 0:
            raise ValueError("听力记录次数或间隔无效")
    if record["independent_streak"] > record["listening_count"] or record["interval_days"] > 30:
        raise ValueError("听力记录次数或间隔无效")
    for key in ("last_reviewed_at", "due_at"):
        value = record[key]
        if value is not None:
            if not isinstance(value, str) or len(value) > 40:
                raise ValueError("听力记录日期无效")
            try:
                if datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
                    raise ValueError()
            except ValueError:
                raise ValueError("听力记录日期无效") from None
    attempts = record["attempts"]
    if not isinstance(attempts, list) or len(attempts) > 100:
        raise ValueError("听力练习历史格式无效")
    seen = set()
    for attempt in attempts:
        if not isinstance(attempt, dict):
            raise ValueError("听力练习历史格式无效")
        attempt_id = _attempt_id(attempt.get("id"))
        if attempt_id in seen or not isinstance(attempt.get("result"), str) or attempt["result"] not in _RESULTS or not isinstance(attempt.get("assisted"), bool) or attempt["result"] == "understood" and attempt["assisted"]:
            raise ValueError("听力练习历史格式无效")
        seen.add(attempt_id)
    return record


def _read_record(item_id):
    path = _path(item_id)
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > _MAX_FILE:
        raise ValueError("听力记录文件无效或过大")
    return _validated(json.loads(path.read_text("utf8")), item_id)


def _save(record):
    path = _path(record["id"])
    _validated(record, record["id"])
    if len(json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False).encode("utf8")) > _MAX_FILE:
        raise ValueError("听力记录内容过长")
    root = packages.safe_path(packages.ROOT, "review")
    root.mkdir(mode=0o700, exist_ok=True)
    _directory().mkdir(mode=0o700, exist_ok=True)
    packages.atomic_json(path, record)


def _stored_records():
    records, damaged = {}, set()
    folder = _directory()
    if not folder.exists():
        return records, damaged
    if not folder.is_dir():
        raise ValueError("听力记录目录无效")
    for path in sorted(folder.glob("*.json")):
        if not _ID.fullmatch(path.stem):
            continue
        try:
            record = _read_record(path.stem)
            if record is not None:
                records[path.stem] = record
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            damaged.add(path.stem)
            marker = str(path)
            if marker not in _WARNED:
                logging.warning("听力记录 %s 无法读取，已跳过并保留原文件。", path.name)
                _WARNED.add(marker)
    return records, damaged


def _library():
    result = {}
    for summary in packages.list_packages():
        try:
            package = packages.read_package(summary["folder"])
            result.setdefault(package["id"], []).append((summary["folder"], package))
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return result


def _view(record, library, active=True):
    item = {key: copy.deepcopy(value) for key, value in record.items() if key not in {"attempts", "schema_version"}}
    item["active"] = active
    source = record["source"]
    audio = {"available": False, "folder": None, "video": None, "start": source["start"], "end": source["end"],
             "reason": "原素材暂时不在素材库，重新导入后可以继续听音。"}
    candidates = sorted(library.get(source["package_id"], []), key=lambda row: row[0] != source["folder"])
    if candidates:
        audio["reason"] = "原语句字幕已变化，暂时不能确认对应原音；保存的学习记录仍保留。"
    for folder, package in candidates:
        segment = next((s for s in package["segments"] if s["id"] == source["segment_id"] and s["en"] == source["en"]), None)
        if segment is None:
            continue
        audio = {"available": True, "folder": folder, "video": package["video"], "start": segment["start"], "end": segment["end"]}
        analysis = segment.get("analysis")
        if isinstance(analysis, dict) and isinstance(analysis.get("meaning"), str):
            item["meaning"] = analysis["meaning"]
        break
    item["audio"] = audio
    return item


def _queue(state_dir=None, reconcile=True):
    sources = {}
    if reconcile:
        cards = reviews.list_reviews(state_dir)
    else:
        items = packages.safe_path(packages.safe_path(packages.ROOT, "review"), "items")
        cards = [card for card in reviews._all_reviews() if card["active"]] if items.is_dir() else []
    for card in cards:
        source = card["source"]
        sources.setdefault(_identifier(source["package_id"], source["segment_id"]), source)
    stored, damaged = _stored_records()
    active = set(sources) | {item_id for item_id, record in stored.items() if record["difficulty"] is not None}
    records = {item_id: stored.get(item_id) or _new_record(sources[item_id]) for item_id in active if item_id not in damaged}
    return records, stored, damaged


def listening_state(state_dir=None):
    with _LOCK:
        records, _, _ = _queue(state_dir)
        library = _library()
        items = [_view(record, library) for record in records.values()]
        items.sort(key=lambda item: (item["due_at"] is not None, item["due_at"] or "", item["id"]))
        return {"items": items, "now": _iso(_now())}


def mark_difficulty(data, state_dir=None):
    if not isinstance(data, dict) or "reason" not in data:
        raise ValueError("请选择听力困难类型")
    reason = data["reason"]
    if reason is not None and (not isinstance(reason, str) or reason not in _REASONS):
        raise ValueError("请选择看不懂、听不出或跟不上")
    segment_id, folder = data.get("segment_id"), data.get("folder")
    if not isinstance(segment_id, str) or not segment_id or len(segment_id) > 200:
        raise ValueError("语句编号无效")
    packages.package_folder(folder)
    with _LOCK:
        package = packages.read_package(folder)
        segment = next((s for s in package["segments"] if s["id"] == segment_id), None)
        if segment is None:
            raise ValueError("原语句不存在，请刷新页面")
        source = reviews._source(folder, package, segment)
        item_id = _identifier(package["id"], segment_id)
        stored = _read_record(item_id)
        record = stored or _new_record(source)
        if reason is not None and record["source"]["en"] != source["en"]:
            raise ValueError("原语句字幕已变化，旧听力记录仍保留，请先核对素材")
        if reason is not None:
            record.update(difficulty=reason, due_at=_iso(_now()), interval_days=0, independent_streak=0)
            _save(record)
        elif record["difficulty"] is not None:
            record["difficulty"] = None
            _save(record)
        records, _, _ = _queue(state_dir, reconcile=False)
        return {"item": _view(record, _library(), item_id in records), "existing": stored is not None}


def grade_attempt(data, state_dir=None):
    if not isinstance(data, dict):
        raise ValueError("听力练习请求格式无效")
    item_id = data.get("id")
    if not isinstance(item_id, str) or not _ID.fullmatch(item_id):
        raise ValueError("听力记录编号无效")
    result, assisted = data.get("result"), data.get("assisted")
    if not isinstance(result, str) or result not in _RESULTS or not isinstance(assisted, bool):
        raise ValueError("请选择本次听音结果")
    if result == "understood" and assisted:
        raise ValueError("本次已使用提示，请选择提示后听懂或还没听懂")
    attempt_id = _attempt_id(data.get("attempt_id"))
    with _LOCK:
        record = _read_record(item_id)
        records, _, damaged = _queue(state_dir, reconcile=False)
        if item_id in damaged:
            raise ValueError("听力记录暂时无法读取，请先检查原文件")
        library = _library()
        if record is not None:
            previous = next((attempt for attempt in record["attempts"] if _attempt_id(attempt["id"]) == attempt_id), None)
            if previous is not None:
                if previous["result"] != result or previous["assisted"] != assisted:
                    raise ValueError("同一次练习已经记录了其他结果，请重新开始练习")
                return {"item": _view(record, library, item_id in records), "duplicate": True}
        if item_id not in records:
            raise ValueError("这句话已不在听力复习中，请刷新列表")
        record = record or records[item_id]
        item = _view(record, library)
        if not item["audio"]["available"]:
            raise ValueError("原音暂时不可用，不能记录新的听音结果")
        now = _now()
        if result == "understood":
            record["independent_streak"] += 1
            interval = (3, 7, 14, 30)[min(record["independent_streak"] - 1, 3)]
            delay = timedelta(days=interval)
        else:
            record["independent_streak"] = 0
            interval = 1 if result == "hinted" else 0
            delay = timedelta(days=1) if result == "hinted" else timedelta(minutes=10)
        record.update(listening_count=record["listening_count"] + 1, last_result=result, last_reviewed_at=_iso(now), due_at=_iso(now + delay), interval_days=interval)
        record["attempts"] = (record["attempts"] + [{"id": attempt_id, "result": result, "assisted": assisted}])[-100:]
        _save(record)
        return {"item": _view(record, library), "duplicate": False}

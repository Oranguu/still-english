"""Rebuild listening units while reusing existing translations and lessons.

This is a pure transformation. Callers must back up their package and reconcile
learning records before saving a different segmentation of an existing package.
"""
import copy
import hashlib
import json

from captions import SEGMENTATION_VERSION, group_learning_segments, is_low_information


def _unique(rows):
    result, seen = [], set()
    for row in rows:
        key = json.dumps(row, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            result.append(copy.deepcopy(row))
    return result


def _combined_lesson(parts):
    # Reuse exact existing explanations. Do not pretend concatenation is a new
    # AI explanation of the dialogue or let repeated "yes" dominate a lesson.
    useful = [part for part in parts if not is_low_information(part["en"])] or parts
    if not all(isinstance(part.get("analysis"), dict) and part["analysis"].get("meaning") for part in useful):
        return None
    def paragraphs(field):
        return "\n\n".join(f'{part["en"]}\n{part["analysis"][field]}' for part in useful if part["analysis"].get(field))
    result = {field: paragraphs(field) for field in ("meaning", "paraphrase", "practice")}
    for field in ("vocabulary", "phrases", "sentence_parts", "grammar", "speech"):
        rows = [row for part in useful for row in part["analysis"].get(field, [])]
        result[field] = _unique(rows)
    return result


def regroup_package(original):
    """Return (updated package, old-to-new ID map, summary), with no I/O.

    The same policy version is a no-op. Unchanged units retain their IDs; merged
    units receive content-derived IDs that cannot impersonate an old sentence.
    Raw subtitles keep the original text, translation, and precise timings.
    """
    data = copy.deepcopy(original)
    old = original["segments"]
    if original.get("segmentation_version", 0) >= SEGMENTATION_VERSION:
        return data, {s["id"]: s["id"] for s in old}, {"before": len(old), "after": len(old), "merged_groups": 0, "skipped": 0, "changed": False}
    plan = group_learning_segments(old)
    mapping = {s["id"]: None for s in old}
    if "subtitle_segments" not in data:
        data["subtitle_segments"] = [{key: s.get(key, "") for key in ("id", "start", "end", "en", "zh")} for s in old]
    result = []
    occupied = {s["id"] for s in old}
    for members in plan["groups"]:
        parts = [old[index] for index in members]
        source_ids = [identifier for part in parts for identifier in part.get("source_ids", [part["id"]])]
        if len(parts) == 1:
            row = copy.deepcopy(parts[0])
        else:
            identity = [[p["id"], p["start"], p["end"], p["en"]] for p in parts]
            digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
            identifier = "g-" + digest[:24]
            if identifier in occupied:
                raise ValueError("合并语句编号冲突，未修改素材")
            occupied.add(identifier)
            row = {"id": identifier, "start": parts[0]["start"], "end": parts[-1]["end"],
                   "en": " ".join(p["en"] for p in parts),
                   "zh": " ".join(p["zh"] for p in parts) if all(p.get("zh") for p in parts) else "",
                   "analysis": _combined_lesson(parts), "analysis_version": 1,
                   "analysis_origin": "merged_existing"}
        row["source_ids"] = source_ids
        for part in parts:
            mapping[part["id"]] = row["id"]
        result.append(row)
    summary = {"before": len(old), "after": len(result), "merged_groups": sum(len(group) > 1 for group in plan["groups"]), "skipped": len(plan["skipped"]), "changed": True}
    data.update(segments=result, segmentation_version=SEGMENTATION_VERSION,
                segmentation_summary=summary,
                status="ready" if all(s.get("zh") and s.get("analysis") for s in result) else "partial")
    return data, mapping, summary

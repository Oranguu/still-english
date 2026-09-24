"""Normalize caption formats into non-overlapping, replayable speech segments."""
import html
import json
import re
from pathlib import Path


def clean(text):
    text = re.sub(r"♪[^♪]*♪", "", text)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    text = re.sub(r"\[(?:music|applause|laughter|silence|音楽|音乐)[^\]]*\]", "", text, flags=re.I)
    text = re.sub(r"\([^)]*(?:music|chirping|laughing|laughter|sighs|gasps|cheering|applause|wind blowing|door closes)[^)]*\)", "", text, flags=re.I)
    text = re.sub(r"^\s*-\s+", "", text)
    text = re.sub(r"^\[[A-Za-z ]+\]\s*", "", text)
    return re.sub(r"\s+", " ", text.replace("♪", "")).strip()


def seconds(value):
    parts = value.replace(",", ".").split(":")
    return sum(float(p) * 60 ** i for i, p in enumerate(reversed(parts)))


def read_cues(path):
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    cues = []
    if path.suffix == ".json3":
        for event in json.loads(text).get("events", []):
            parts = event.get("segs", [])
            body = clean("".join(p.get("utf8", "") for p in parts))
            if not body or "tStartMs" not in event:
                continue
            start = event["tStartMs"] / 1000
            end = start + event.get("dDurationMs", 1000) / 1000
            # Auto captions carry word offsets. Preserve these for sentence boundaries.
            if any("tOffsetMs" in p for p in parts):
                for i, part in enumerate(parts):
                    word = clean(part.get("utf8", ""))
                    if not word:
                        continue
                    a = start + part.get("tOffsetMs", 0) / 1000
                    b = start + parts[i + 1].get("tOffsetMs", event.get("dDurationMs", 1000)) / 1000 if i + 1 < len(parts) else end
                    cues.append({"start": a, "end": max(a + .08, min(b, a + max(.4, len(word.split()) * .65))), "text": word})
            else:
                cues.append({"start": start, "end": end, "text": body})
    elif path.suffix == ".json":
        data = json.loads(text)
        for cue in data.get("body", []):  # Bilibili subtitle JSON
            cues.append({"start": cue["from"], "end": cue["to"], "text": clean(cue["content"])})
    else:
        for block in re.split(r"\n\s*\n", text.replace("\r", "")):
            match = re.search(r"(\d{1,2}:\d{2}(?::\d{2})?[.,]\d+)\s*-->\s*(\d{1,2}:\d{2}(?::\d{2})?[.,]\d+)[^\n]*\n(.*)", block, re.S)
            if match:
                cues.append({"start": seconds(match[1]), "end": seconds(match[2]), "text": clean(match[3])})
    return [c for c in cues if c["text"] and c["end"] > c["start"]]


def segment_cues(cues):
    """Use source timings; merge short cues at punctuation, pauses, or ~12 seconds."""
    normalized = []
    for cue in sorted(cues, key=lambda c: c["start"]):
        cue = dict(cue)
        if not re.search(r"[A-Za-z0-9]", cue["text"]):
            continue
        if normalized and cue["start"] < normalized[-1]["end"] - .03:
            previous = normalized[-1]
            # Remove only overlapping rolling captions, never later repeated speech.
            old, new = previous["text"].split(), cue["text"].split()
            overlap = next((n for n in range(min(len(old), len(new)), 0, -1) if [w.lower() for w in old[-n:]] == [w.lower() for w in new[:n]]), 0)
            if overlap:
                cue["text"] = " ".join(new[overlap:])
                if not cue["text"]:
                    previous["end"] = max(previous["end"], cue["end"])
                    continue
            previous["end"] = min(previous["end"], cue["start"])
        normalized.append(cue)
    groups = []
    current = None
    for cue in normalized:
        if current and (cue["start"] - current["end"] > .7 or cue["end"] - current["start"] > 13 or len(current["en"].split()) >= 38):
            groups.append(current)
            current = None
        if current is None:
            current = {"start": cue["start"], "end": cue["end"], "en": cue["text"]}
        else:
            current["en"] += " " + cue["text"]
            current["end"] = cue["end"]
        if re.search(r'[.!?]["\u201d\u2019]?$' , current["en"]):
            groups.append(current)
            current = None
    if current:
        groups.append(current)
    result = []
    for i, group in enumerate(groups):
        if group["end"] <= group["start"]:
            continue
        group.update(id=f"s{len(result) + 1:04}", zh="", analysis=None)
        group["start"] = round(group["start"], 3)
        group["end"] = round(group["end"], 3)
        result.append(group)
    return result


def vtt(segments, bilingual=False):
    def stamp(t):
        ms = round(t * 1000)
        return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02}.{ms % 1000:03}"
    lines = ["WEBVTT", ""]
    for s in segments:
        lines.extend([s["id"], f'{stamp(s["start"])} --> {stamp(s["end"])}', s["en"]])
        if bilingual and s.get("zh"):
            lines.append(s["zh"])
        lines.append("")
    return "\n".join(lines)

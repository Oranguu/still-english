"""Normalize caption formats into non-overlapping, replayable speech segments."""
import html
import json
import re
from pathlib import Path

SEGMENTATION_VERSION = 2
SHORT_WORD_LIMIT = 4
MERGE_MAX_GAP = 1.5
MERGE_MAX_SECONDS = 13
MERGE_MAX_WORDS = 40

# These expressions can stay in the full subtitles without each becoming a
# separate listening exercise, even when several responses exceed four words.
_FILLER_WORDS = frozenset({
    "yes", "yeah", "yep", "yup", "no", "nope", "nah", "okay", "ok", "alright",
    "right", "well", "uh", "um", "erm", "er", "ah", "oh", "hmm", "hm", "mm",
    "mmm", "mhm", "wow", "hey", "hi", "hello", "bye", "goodbye", "thanks",
    "sorry", "sure", "indeed", "oops", "whoa", "huh", "hurray", "hooray",
})
_FILLER_PHRASES = tuple(tuple(phrase.split()) for phrase in (
    "thank you", "thank you very much", "thank you so much", "thanks a lot",
    "thanks very much", "thanks so much", "thank you all", "thank you both",
    "thank you guys", "thanks guys", "thank you sir", "thank you ma'am",
    "excuse me", "so sorry", "very sorry", "i'm sorry", "i am sorry",
    "i'm so sorry", "i am so sorry", "i'm really sorry", "i am really sorry",
    "you're welcome", "you are welcome", "all right", "no problem", "not a problem",
    "of course", "uh huh", "mm hmm", "hi there",
    "good morning", "good afternoon", "good evening", "good night", "hello there",
    "see you", "see ya", "see you later", "oh my god", "oh god", "my god",
))


def _words(text):
    return re.findall(r"[a-z]+(?:'[a-z]+)?|\d+(?:[.,]\d+)*", text.lower().replace("’", "'"))


def is_low_information(text):
    """True only when the complete text consists of known short responses."""
    words = _words(text)
    if not words:
        return False
    reachable = {0}
    for position in range(len(words)):
        if position not in reachable:
            continue
        if words[position] in _FILLER_WORDS:
            reachable.add(position + 1)
        for phrase in _FILLER_PHRASES:
            if tuple(words[position:position + len(phrase)]) == phrase:
                reachable.add(position + len(phrase))
    return len(words) in reachable


def _short(group):
    return len(_words(group["en"])) <= SHORT_WORD_LIMIT or is_low_information(group["en"])


def _fits(left, right):
    return (0 <= right["start"] - left["end"] <= MERGE_MAX_GAP
            and right["end"] - left["start"] <= MERGE_MAX_SECONDS
            and len(_words(left["en"])) + len(_words(right["en"])) <= MERGE_MAX_WORDS)


def _join(left, right):
    return {"members": left["members"] + right["members"], "start": left["start"],
            "end": right["end"], "en": left["en"] + " " + right["en"]}


def group_learning_segments(segments):
    """Return original-index groups and skipped indices without changing input.

    Input must be chronological, non-overlapping source segments. Short speech
    preferentially joins the following nearby speech, then the preceding group
    when no forward merge fits. Groups of four words or fewer, and groups made
    entirely of known filler expressions, remain only in the full subtitles if
    they cannot join useful speech. Bounds describe timing, not speaker or scene
    recognition; a short question, name or number is never joined across a long
    pause just to meet the exercise length threshold.
    """
    current = [{"members": [i], "start": s["start"], "end": s["end"], "en": s["en"]}
               for i, s in enumerate(segments)]
    skipped = []
    # Removing filler-only groups can expose another safe neighbor. Finish that
    # work now so applying the same rule again cannot keep changing boundaries.
    while current:
        result = []
        index = 0
        while index < len(current):
            group = current[index]
            index += 1
            if _short(group):
                while index < len(current) and _short(group) and _fits(group, current[index]):
                    group = _join(group, current[index])
                    index += 1
                if _short(group) and result and _fits(result[-1], group):
                    group = _join(result.pop(), group)
            if _short(group):
                skipped.extend(group["members"])
            else:
                result.append(group)
        if len(result) == len(current):
            current = result
            break
        current = result
    return {"groups": [group["members"] for group in current], "skipped": sorted(skipped)}


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


def segment_cues(cues, *, compact=True):
    """Normalize timings, then group short speech into useful listening units.

    compact=False retains the original subtitle units, including small replies,
    so callers can keep complete, accurately timed full-playback subtitles.
    """
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
    if not compact:
        return result
    grouped = []
    for members in group_learning_segments(result)["groups"]:
        parts = [result[i] for i in members]
        grouped.append({"id": f"s{len(grouped) + 1:04}", "start": parts[0]["start"],
                        "end": parts[-1]["end"], "en": " ".join(s["en"] for s in parts),
                        "zh": "", "analysis": None, "source_ids": [s["id"] for s in parts]})
    return grouped


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

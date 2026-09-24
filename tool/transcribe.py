"""Optional local transcription process; isolated so cancellation releases the model."""
import json
import sys
from pathlib import Path
from faster_whisper import WhisperModel

model = WhisperModel("base.en", device="cpu", compute_type="int8", download_root=str(Path(__file__).resolve().parent / ".state" / "models"))
segments, info = model.transcribe(sys.argv[1], language="en", vad_filter=True, word_timestamps=True)
rows = []
for segment in segments:
    if segment.no_speech_prob > .7:
        continue
    if segment.words:
        rows.extend({"start": w.start, "end": w.end, "text": w.word.strip()} for w in segment.words if w.end > w.start)
    else:
        rows.append({"start": segment.start, "end": segment.end, "text": segment.text.strip()})
with open(sys.argv[2], "w", encoding="utf8") as file:
    json.dump(rows, file, ensure_ascii=False)

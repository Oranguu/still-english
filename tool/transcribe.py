"""Local English ASR with bounded audio chunks and atomic retry checkpoints."""
import argparse
import hashlib
import importlib.util
import json
import math
import os
import platform
import subprocess
import wave
from pathlib import Path

from packages import STATE, atomic_json

CHUNK_SECONDS = 300
OVERLAP_SECONDS = 5
SAMPLE_RATE = 16000
MLX_MODEL = "mlx-community/whisper-large-v3-turbo"
CPU_MODEL = "small.en"
PIPELINE_VERSION = 3


def backend():
    if platform.system() == "Darwin" and platform.machine() == "arm64" and importlib.util.find_spec("mlx_whisper"):
        return "mlx"
    if importlib.util.find_spec("faster_whisper"):
        return "cpu"
    return None


def windows(duration):
    for start in range(0, math.ceil(duration), CHUNK_SECONDS):
        end = min(start + CHUNK_SECONDS, duration)
        yield start, end, max(0, start - OVERLAP_SECONDS), min(duration, end + OVERLAP_SECONDS)


def word_rows(segments, offset, start, end, duration):
    """Keep only the central window, so overlapping chunks cannot duplicate words."""
    rows = []
    for segment in segments:
        if segment.get("no_speech_prob", 0) > .6 and segment.get("avg_logprob", 0) < -1:
            continue
        for word in segment.get("words") or [{"start": segment["start"], "end": segment["end"], "word": segment["text"]}]:
            a, b = float(word["start"]) + offset, float(word["end"]) + offset
            text = word.get("word", "").strip()
            if not text or not all(math.isfinite(t) for t in (a, b)) or not start <= (a + b) / 2 < end:
                continue
            # Ownership is decided by midpoint; retain the whole spoken word.
            a, b = max(0, a), min(duration, b)
            if b > a:
                rows.append({"start": round(a, 3), "end": round(b, 3), "text": text})
    return rows


def context_clips(spans, duration):
    """Remove only long non-dialogue gaps, keeping quiet lines around clear speech."""
    merged = []
    for span in spans:
        start, end = max(0, span["start"] / SAMPLE_RATE - 30), min(duration, span["end"] / SAMPLE_RATE + 30)
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    # A detector can miss an entirely quiet scene; let ASR inspect it itself.
    return [value for pair in merged for value in pair] or [0, duration]


def speech_clips(audio):
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    spans = get_speech_timestamps(audio, VadOptions(threshold=.2, min_silence_duration_ms=2000, speech_pad_ms=500))
    return context_clips(spans, len(audio) / SAMPLE_RATE)


def transcribe(video, output):
    import numpy as np
    import imageio_ffmpeg
    engine = backend()
    if engine is None:
        raise RuntimeError("请先安装本地语音转写组件")
    model_name = MLX_MODEL if engine == "mlx" else CPU_MODEL
    os.environ["HF_HOME"] = str(STATE / "models" / "huggingface")
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    signature = hashlib.sha256(json.dumps([video.stat().st_size, video.stat().st_mtime_ns, engine, model_name, PIPELINE_VERSION]).encode()).hexdigest()
    work = output.with_suffix(".chunks") / signature[:24]
    work.mkdir(parents=True, exist_ok=True)
    audio_path = work / "audio.wav"
    if not audio_path.exists():
        print("STILL_ASR " + json.dumps({"progress": 0, "message": "正在提取本地英文音轨…"}), flush=True)
        temp = work / "audio.part.wav"
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-y", "-i", str(video), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(temp)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        temp.replace(audio_path)
    with wave.open(str(audio_path), "rb") as wav:
        duration = wav.getnframes() / SAMPLE_RATE
        chunks = list(windows(duration))
        all_rows = []
        model = None
        for index, (start, end, left, right) in enumerate(chunks):
            checkpoint = work / f"{index:04}.json"
            print("STILL_ASR " + json.dumps({"progress": index / len(chunks), "message": f"正在本地识别英文 · 第 {index + 1}/{len(chunks)} 段（已完成的段落可续做）"}, ensure_ascii=False), flush=True)
            if checkpoint.exists():
                rows = json.loads(checkpoint.read_text("utf8"))
            else:
                wav.setpos(round(left * SAMPLE_RATE))
                audio = np.frombuffer(wav.readframes(round((right - left) * SAMPLE_RATE)), dtype=np.int16).astype(np.float32) / 32768
                if engine == "mlx":
                    import mlx_whisper
                    clips = speech_clips(audio) if importlib.util.find_spec("faster_whisper") else None
                    result = mlx_whisper.transcribe(
                        audio, path_or_hf_repo=model_name, language="en", task="transcribe", verbose=None,
                        word_timestamps=True, condition_on_previous_text=False, temperature=(0.0, .2, .4),
                        hallucination_silence_threshold=2, **({"clip_timestamps": clips} if clips else {}))
                    segments = result["segments"]
                else:
                    from faster_whisper import WhisperModel
                    if model is None:
                        model = WhisperModel(model_name, device="cpu", compute_type="int8", download_root=str(STATE / "models"))
                    result, _ = model.transcribe(audio, language="en", vad_filter=False, word_timestamps=True, condition_on_previous_text=False, hallucination_silence_threshold=2)
                    segments = [dict(start=s.start, end=s.end, text=s.text, no_speech_prob=s.no_speech_prob, avg_logprob=s.avg_logprob,
                                     words=[dict(start=w.start, end=w.end, word=w.word) for w in s.words or []]) for s in result]
                rows = word_rows(segments, left, start, end, duration)
                atomic_json(checkpoint, rows)
            all_rows.extend(rows)
    atomic_json(output, all_rows)
    atomic_json(output.with_suffix(".metadata.json"), {"engine": engine, "model": model_name, "language": "en", "duration": duration, "chunks": len(chunks), "pipeline_version": PIPELINE_VERSION})
    print("STILL_ASR " + json.dumps({"progress": 1, "message": "英文识别已完成，正在整理语句…"}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    transcribe(args.video, args.output)

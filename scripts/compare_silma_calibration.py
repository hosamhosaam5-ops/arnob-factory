#!/usr/bin/env python3
"""Compare a one-chunk SILMA recalibration against the immutable Run #6 WAV."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
import subprocess
import wave
from pathlib import Path
from typing import Any


def audio_metrics(path: Path) -> dict[str, Any]:
    with wave.open(str(path), "rb") as audio:
        channels = audio.getnchannels()
        sample_width = audio.getsampwidth()
        sample_rate = audio.getframerate()
        frames = audio.getnframes()
        pcm = audio.readframes(frames)
    if channels != 1 or sample_width != 2 or sample_rate != 24000 or frames == 0:
        raise ValueError(f"Expected nonempty mono 24kHz PCM16 WAV: {path}")
    values = [sample / 32768.0 for (sample,) in struct.iter_unpack("<h", pcm)]
    rms = math.sqrt(sum(value * value for value in values) / len(values))
    peak = max(abs(value) for value in values)
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "samples": frames,
        "sampleRate": sample_rate,
        "channels": channels,
        "durationSeconds": frames / sample_rate,
        "rmsAmplitude": rms,
        "rmsDbfs": 20 * math.log10(rms) if rms else float("-inf"),
        "peakAmplitude": peak,
        "peakDbfs": 20 * math.log10(peak) if peak else float("-inf"),
    }


def silence_intervals(path: Path, duration: float) -> list[dict[str, float]]:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af",
         "silencedetect=noise=-50dB:d=0.02", "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"silencedetect failed for {path}: {result.stderr.strip()}")
    starts = [float(value) for value in re.findall(r"silence_start:\s*([0-9]+(?:\.[0-9]+)?)", result.stderr)]
    ends = [
        (float(end), float(gap))
        for end, gap in re.findall(
            r"silence_end:\s*([0-9]+(?:\.[0-9]+)?)\s*\|\s*silence_duration:\s*([0-9]+(?:\.[0-9]+)?)",
            result.stderr,
        )
    ]
    intervals = []
    for start, (end, gap) in zip(starts, ends):
        if start > 0.02 and end < duration - 0.02:
            intervals.append({"startSeconds": start, "endSeconds": end, "durationSeconds": gap})
    return intervals


def compare(original: Path, recalibrated: Path) -> dict[str, Any]:
    baseline = audio_metrics(original)
    candidate = audio_metrics(recalibrated)
    baseline_silences = silence_intervals(original, baseline["durationSeconds"])
    candidate_silences = silence_intervals(recalibrated, candidate["durationSeconds"])
    baseline_longest = max(baseline_silences, key=lambda item: item["durationSeconds"], default=None)
    candidate_longest = max(candidate_silences, key=lambda item: item["durationSeconds"], default=None)
    duration_delta = abs(candidate["durationSeconds"] - baseline["durationSeconds"])
    rms_delta_db = abs(candidate["rmsDbfs"] - baseline["rmsDbfs"])
    return {
        "status": "pass" if duration_delta < 0.15 and rms_delta_db < 1.0 else "fail",
        "original": {**baseline, "internalSilences": baseline_silences, "longestInternalSilence": baseline_longest},
        "recalibrated": {**candidate, "internalSilences": candidate_silences, "longestInternalSilence": candidate_longest},
        "durationDeltaSeconds": duration_delta,
        "rmsDeltaDb": rms_delta_db,
        "limits": {"durationDeltaSecondsExclusive": 0.15, "rmsDeltaDbExclusive": 1.0},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--original", required=True)
    parser.add_argument("--recalibrated", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = compare(Path(args.original), Path(args.recalibrated))
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

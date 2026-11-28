#!/usr/bin/env python3
"""Measure internal MP3 silence, duration and signal integrity for one SILMA seed."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any


def parse_internal_silences(log: str, duration: float) -> list[dict[str, float]]:
    starts = [
        float(value)
        for value in re.findall(
            r"silence_start:\s*([0-9]+(?:\.[0-9]+)?)",
            log,
        )
    ]
    ends = [
        (float(end), float(gap))
        for end, gap in re.findall(
            r"silence_end:\s*([0-9]+(?:\.[0-9]+)?)\s*\|\s*silence_duration:\s*([0-9]+(?:\.[0-9]+)?)",
            log,
        )
    ]
    intervals = []
    for start, (end, gap) in zip(starts, ends):
        if start > 0.02 and end < duration - 0.02:
            intervals.append(
                {
                    "startSeconds": start,
                    "endSeconds": end,
                    "durationSeconds": gap,
                }
            )
    return intervals


def analyze_candidate(
    mp3_path: Path,
    wav_path: Path,
    manifest_path: Path,
    chunk_id: str,
    expected_duration: float = 2.54,
    duration_tolerance: float = 0.30,
    maximum_internal_silence: float = 0.45,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    matches = [
        item
        for item in manifest.get("completedChunks", [])
        if item.get("chunkId") == chunk_id
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one candidate chunk {chunk_id!r}")
    item = matches[0]

    for key, path in (("mp3", mp3_path), ("wav", wav_path)):
        metadata = item.get(key, {})
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Missing candidate {key.upper()}: {path}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != metadata.get("sha256"):
            raise ValueError(f"Candidate {key.upper()} SHA-256 mismatch")

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(mp3_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    duration = float(probe.stdout.strip())
    detected = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(mp3_path),
            "-af",
            "silencedetect=noise=-50dB:d=0.02",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if detected.returncode != 0:
        raise RuntimeError(f"silencedetect failed: {detected.stderr.strip()}")
    intervals = parse_internal_silences(detected.stderr, duration)
    longest = max(
        (item["durationSeconds"] for item in intervals),
        default=0.0,
    )
    wav_metadata = item["wav"]
    peak = float(wav_metadata.get("peakAmplitude", 0.0))
    rms = float(wav_metadata.get("rmsAmplitude", 0.0))
    passes_duration = abs(duration - expected_duration) <= duration_tolerance
    passes_silence = longest < maximum_internal_silence
    passes_signal = 0.01 < peak < 0.999 and rms > 0.01
    return {
        "status": "pass" if passes_duration and passes_silence and passes_signal else "fail",
        "chunkId": chunk_id,
        "seed": item.get("settings", {}).get("seed"),
        "durationSeconds": round(duration, 6),
        "expectedDurationSeconds": expected_duration,
        "durationToleranceSeconds": duration_tolerance,
        "longestInternalSilenceSeconds": round(longest, 6),
        "internalSilences": intervals,
        "peakAmplitude": peak,
        "rmsAmplitude": rms,
        "passesDuration": passes_duration,
        "passesInternalSilence": passes_silence,
        "passesSignalIntegrityProxy": passes_signal,
        "pass": passes_duration and passes_silence and passes_signal,
        "pronunciationReview": "Not independently assessed by ASR or human listening",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mp3", required=True)
    parser.add_argument("--wav", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--chunk-id", required=True)
    parser.add_argument("--expected-duration", type=float, default=2.54)
    parser.add_argument("--duration-tolerance", type=float, default=0.30)
    parser.add_argument("--maximum-internal-silence", type=float, default=0.45)
    args = parser.parse_args()
    result = analyze_candidate(
        Path(args.mp3),
        Path(args.wav),
        Path(args.manifest),
        args.chunk_id,
        args.expected_duration,
        args.duration_tolerance,
        args.maximum_internal_silence,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

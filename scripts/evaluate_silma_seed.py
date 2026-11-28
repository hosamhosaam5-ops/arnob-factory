#!/usr/bin/env python3
"""Measure internal silence, duration and signal integrity for one SILMA seed."""

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
        for value in re.findall(r"silence_start:\s*([0-9]+(?:\.[0-9]+)?)", log)
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
                {"startSeconds": start, "endSeconds": end, "durationSeconds": gap}
            )
    return intervals


def duration_window(
    source_text: str,
    successful_durations: list[float] | None,
    tolerance: float,
) -> tuple[float, float, float | None]:
    """Use the successful same-text mean, or a bounded Arabic-text length estimate."""
    successful = [
        float(value) for value in (successful_durations or [])
        if isinstance(value, (int, float)) and value > 0
    ]
    if successful:
        mean = sum(successful) / len(successful)
        return max(0.1, mean - tolerance), mean + tolerance, mean

    letter_count = len(re.findall(r"[\u0600-\u06ffA-Za-z]", source_text))
    if letter_count < 1:
        raise ValueError("source text must contain at least one letter")
    # Arabic TTS duration estimate: 0.04–0.12 seconds per written letter.
    # Broad bounds accommodate pauses and speaking-rate variation while rejecting
    # clearly truncated or abnormally elongated candidates.
    return max(0.8, letter_count * 0.04), max(1.0, letter_count * 0.12), None


def analyze_candidate(
    mp3_path: Path,
    wav_path: Path,
    manifest_path: Path,
    chunk_id: str,
    expected_duration: float | None = 2.54,
    duration_tolerance: float = 0.30,
    maximum_internal_silence: float = 0.45,
    source_text: str = "",
    successful_durations: list[float] | None = None,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    matches = [
        item for item in manifest.get("completedChunks", [])
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
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(mp3_path),
        ],
        capture_output=True, text=True, check=True,
    )
    duration = float(probe.stdout.strip())
    detected = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(mp3_path),
            "-af", "silencedetect=noise=-50dB:d=0.02", "-f", "null", "-",
        ],
        capture_output=True, text=True, check=False,
    )
    if detected.returncode != 0:
        raise RuntimeError(f"silencedetect failed: {detected.stderr.strip()}")
    intervals = parse_internal_silences(detected.stderr, duration)
    longest = max((item["durationSeconds"] for item in intervals), default=0.0)
    wav_metadata = item["wav"]
    peak = float(wav_metadata.get("peakAmplitude", 0.0))
    rms = float(wav_metadata.get("rmsAmplitude", 0.0))

    if successful_durations:
        mean = sum(successful_durations) / len(successful_durations)
        minimum_duration = max(0.1, mean - duration_tolerance)
        maximum_duration = mean + duration_tolerance
        expected_value = mean
        duration_policy = "mean-of-successful-same-text-attempts"
    elif expected_duration is not None:
        minimum_duration = max(0.1, expected_duration - duration_tolerance)
        maximum_duration = expected_duration + duration_tolerance
        expected_value = expected_duration
        duration_policy = "configured-expected-duration"
    else:
        minimum_duration, maximum_duration, expected_value = duration_window(
            source_text, None, duration_tolerance
        )
        duration_policy = "source-character-count-bounds"

    passes_duration = minimum_duration <= duration <= maximum_duration
    passes_silence = longest < maximum_internal_silence
    passes_peak = 0.01 < peak < 0.95
    passes_rms = rms > 0.01
    passes_signal = passes_peak and passes_rms
    passed = passes_duration and passes_silence and passes_signal
    return {
        "status": "pass" if passed else "fail",
        "pass": passed,
        "chunkId": chunk_id,
        "seed": item.get("settings", {}).get("seed"),
        "durationSeconds": round(duration, 6),
        "expectedDurationSeconds": expected_value,
        "durationMinimumSeconds": round(minimum_duration, 6),
        "durationMaximumSeconds": round(maximum_duration, 6),
        "durationPolicy": duration_policy,
        "durationToleranceSeconds": duration_tolerance,
        "longestInternalSilenceSeconds": round(longest, 6),
        "internalSilences": intervals,
        "peakAmplitude": peak,
        "peakLimitExclusive": 0.95,
        "rmsAmplitude": rms,
        "rmsMinimumExclusive": 0.01,
        "passesDuration": passes_duration,
        "passesInternalSilence": passes_silence,
        "passesPeakAmplitude": passes_peak,
        "passesRms": passes_rms,
        "passesSignalIntegrityProxy": passes_signal,
        "pronunciationReview": "Not independently assessed by ASR or human listening",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mp3", required=True)
    parser.add_argument("--wav", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--chunk-id", required=True)
    parser.add_argument("--expected-duration", type=float)
    parser.add_argument("--duration-tolerance", type=float, default=0.30)
    parser.add_argument("--maximum-internal-silence", type=float, default=0.45)
    parser.add_argument("--source-text", default="")
    parser.add_argument("--successful-duration", type=float, action="append", default=[])
    args = parser.parse_args()
    result = analyze_candidate(
        Path(args.mp3),
        Path(args.wav),
        Path(args.manifest),
        args.chunk_id,
        args.expected_duration,
        args.duration_tolerance,
        args.maximum_internal_silence,
        args.source_text,
        args.successful_duration,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

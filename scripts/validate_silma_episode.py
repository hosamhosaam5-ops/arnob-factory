#!/usr/bin/env python3
"""Validate an eight-chunk Method A SILMA run and every source fingerprint."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

EXPECTED_WEIGHT_SHA256 = {
    "silma": "f43256d0b78b8803c638aed0875da5a4b372b4a784690a0156e5baff14f7336c",
    "vocos": "97ec976ad1fd67a33ab2682d29c0ac7df85234fae875aefcc5fb215681a91b2a",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def probe_media(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size:stream=codec_name,sample_rate,channels,bit_rate",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    if not streams:
        raise SystemExit(f"ffprobe returned no streams for {path}")
    stream = streams[0]
    return {
        "codec": stream.get("codec_name"),
        "sampleRate": int(stream.get("sample_rate", 0)),
        "channels": int(stream.get("channels", 0)),
        "bitRate": int(stream.get("bit_rate") or 0),
        "durationSeconds": float(payload["format"]["duration"]),
    }


def fail(message: str) -> None:
    raise SystemExit(message)


def main() -> int:
    if len(sys.argv) != 3:
        fail("usage: validate_silma_episode.py OUTPUT_DIR EPISODE_MANIFEST")

    output_dir = Path(sys.argv[1]).resolve()
    script_path = Path(sys.argv[2]).resolve(strict=True)
    report_path = output_dir / "manifest.json"
    if not report_path.is_file() or report_path.stat().st_size == 0:
        fail("Missing or empty SILMA manifest.json")

    report = json.loads(report_path.read_text(encoding="utf-8"))
    script = json.loads(script_path.read_text(encoding="utf-8"))
    if report.get("episodeId") != "letter-alif" or script.get("episodeId") != "letter-alif":
        fail("Unexpected episode identifier")
    if report.get("status") != "complete":
        fail("SILMA generation did not complete")
    if report.get("dryRun") is not False or report.get("modelInferenceInvoked") is not True:
        fail("Expected real SILMA inference")
    if report.get("method") != "A" or report.get("nfeStep") != 16:
        fail("Expected Method A and nfe_step=16")

    expected_chunks = script.get("chunks")
    completed_chunks = report.get("completedChunks", [])
    if not isinstance(expected_chunks, list) or len(expected_chunks) != 8:
        fail("Input script must contain exactly eight chunks")
    if len(completed_chunks) != 8 or report.get("chunksPlanned") != 8:
        fail("Expected exactly eight completed chunks")

    script_sha = sha256_file(script_path)
    if report.get("episodeScriptSha256") != script_sha:
        fail("Input script SHA-256 differs from the generation manifest")

    for expected, actual in zip(expected_chunks, completed_chunks):
        for key in ("chunkId", "speakerId", "text"):
            if actual.get(key) != expected.get(key):
                fail(f"Exact source script mismatch: {expected.get('chunkId')}")

        speaker = actual["speakerId"]
        if speaker == "pistachio":
            expected_voice = "ar+f3"
            expected_speed, expected_pitch = 160, 68
        elif speaker == "hazelnut":
            expected_voice = "ar+m3"
            expected_speed, expected_pitch = 140, 43
        else:
            fail(f"Unknown speaker: {speaker}")

        settings = actual.get("settings", {})
        if settings.get("method") != "A":
            fail(f"Method mismatch: {actual['chunkId']}")
        if settings.get("voice") != expected_voice or settings.get("nfeStep") != 16:
            fail(f"Voice or nfe_step mismatch: {actual['chunkId']}")
        if settings.get("referenceSpeed") != expected_speed:
            fail(f"Reference speed mismatch: {actual['chunkId']}")
        if settings.get("referencePitch") != expected_pitch:
            fail(f"Reference pitch mismatch: {actual['chunkId']}")

        timing = actual.get("timing", {})
        for key in ("cpuSeconds", "wallSeconds", "audioDurationSeconds", "rtf"):
            value = timing.get(key)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                fail(f"Invalid {key} measurement: {actual['chunkId']}")
        expected_rtf = timing["wallSeconds"] / timing["audioDurationSeconds"]
        if abs(timing["rtf"] - expected_rtf) > 0.02:
            fail(f"RTF mismatch: {actual['chunkId']}")

        for media_key, expected_codec in (("wav", "pcm_s16le"), ("mp3", "mp3")):
            metadata = actual.get(media_key, {})
            relative_path = Path(metadata.get("path", ""))
            if relative_path.is_absolute() or ".." in relative_path.parts:
                fail(f"Unsafe {media_key.upper()} path: {relative_path}")
            media_path = output_dir / relative_path
            if not media_path.is_file() or media_path.stat().st_size == 0:
                fail(f"Missing {media_key.upper()}: {actual['chunkId']}")
            if sha256_file(media_path) != metadata.get("sha256"):
                fail(f"{media_key.upper()} SHA-256 mismatch: {actual['chunkId']}")

            details = probe_media(media_path)
            if details["codec"] != expected_codec:
                fail(f"Invalid {media_key.upper()} codec: {actual['chunkId']}")
            if details["sampleRate"] != 24000 or details["channels"] != 1:
                fail(f"Invalid {media_key.upper()} sample format: {actual['chunkId']}")
            if media_key == "mp3" and not 120000 <= details["bitRate"] <= 136000:
                fail(f"MP3 bitrate outside 128 kbps tolerance: {actual['chunkId']}")

    for name, expected_sha in EXPECTED_WEIGHT_SHA256.items():
        weight = report.get("weights", {}).get(name, {})
        if weight.get("sha256") != expected_sha:
            fail(f"{name} SHA-256 differs from the reviewed digest")
        if weight.get("expectedSha256") != expected_sha:
            fail(f"{name} expected SHA-256 field is incorrect")
        if weight.get("verifiedBeforeInference") is not True:
            fail(f"{name} was not verified before inference")

    reference_hashes: dict[str, str] = {}
    for persona in ("pistachio", "hazelnut"):
        reference = report.get("references", {}).get(persona, {})
        relative_path = Path(reference.get("path", ""))
        if relative_path.is_absolute() or ".." in relative_path.parts:
            fail(f"Unsafe generated reference path: {persona}")
        reference_path = output_dir / "references" / relative_path
        actual_hash = sha256_file(reference_path) if reference_path.is_file() else None
        if actual_hash != reference.get("sha256"):
            fail(f"Generated eSpeak reference SHA-256 mismatch: {persona}")
        reference_hashes[persona] = actual_hash

    measurement = report.get("measurementSummary", {})
    total_wall = measurement.get("totalInferenceWallSeconds")
    total_cpu = measurement.get("totalInferenceCpuSeconds")
    if not isinstance(total_wall, (int, float)) or total_wall <= 0:
        fail("Missing measured total SILMA wall time")
    if not isinstance(total_cpu, (int, float)) or total_cpu <= 0:
        fail("Missing measured total SILMA CPU time")

    print(
        json.dumps(
            {
                "status": "PASS",
                "chunksValidated": len(completed_chunks),
                "episodeScriptSha256": script_sha,
                "weights": {
                    key: report["weights"][key]["sha256"]
                    for key in EXPECTED_WEIGHT_SHA256
                },
                "references": reference_hashes,
                "totalInferenceWallSeconds": total_wall,
                "totalInferenceCpuSeconds": total_cpu,
                "mp3Files": [item["mp3"]["path"] for item in completed_chunks],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

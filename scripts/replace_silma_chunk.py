#!/usr/bin/env python3
"""Replace one completed SILMA chunk while preserving all unrelated media and hashes."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import time
from copy import deepcopy
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_media_path(root: Path, relative: str) -> Path:
    value = Path(relative)
    if value.is_absolute() or ".." in value.parts:
        raise ValueError(f"Unsafe media path: {relative}")
    return root / value


def verify_media(root: Path, metadata: dict[str, Any], label: str) -> Path:
    path = safe_media_path(root, str(metadata.get("path", "")))
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"Missing {label}: {path}")
    if sha256_file(path) != metadata.get("sha256"):
        raise ValueError(f"{label} SHA-256 mismatch: {path}")
    return path


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
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    stream = payload.get("streams", [None])[0]
    if stream is None:
        raise ValueError(f"ffprobe found no stream in {path}")
    return {
        "codec": stream.get("codec_name"),
        "sampleRate": int(stream.get("sample_rate", 0)),
        "channels": int(stream.get("channels", 0)),
        "bitRate": int(stream.get("bit_rate") or 0),
        "durationSeconds": float(payload["format"]["duration"]),
        "sizeBytes": int(payload["format"]["size"]),
    }


def make_episode_mix(root: Path, completed: list[dict[str, Any]]) -> dict[str, Any]:
    concat = root / "concat-list.txt"
    lines = []
    for item in completed:
        wav = safe_media_path(root, item["wav"]["path"]).resolve()
        lines.append("file '" + wav.as_posix().replace("'", "'\\''") + "'")
    concat.write_text("\n".join(lines) + "\n", encoding="utf-8")

    episode_wav = root / "letter-alif.wav"
    pending_wav = root / "letter-alif.pending.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat),
            "-c:a",
            "pcm_s16le",
            str(pending_wav),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    wav_info = probe_media(pending_wav)
    pending_wav.replace(episode_wav)
    wav_info["sha256"] = sha256_file(episode_wav)
    wav_info["path"] = episode_wav.name

    episode_mp3 = root / "letter-alif.mp3"
    pending_mp3 = root / "letter-alif.pending.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(episode_wav),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(pending_mp3),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    mp3_info = probe_media(pending_mp3)
    if (
        mp3_info["codec"] != "mp3"
        or mp3_info["sampleRate"] != 24000
        or mp3_info["channels"] != 1
        or not 120000 <= mp3_info["bitRate"] <= 136000
    ):
        raise ValueError(f"Rebuilt episode MP3 failed format validation: {mp3_info}")
    pending_mp3.replace(episode_mp3)
    mp3_info["sha256"] = sha256_file(episode_mp3)
    mp3_info["path"] = episode_mp3.name
    return {"wav": wav_info, "mp3": mp3_info}


def write_measurements(path: Path, completed: list[dict[str, Any]]) -> None:
    columns = [
        "chunkId",
        "speakerId",
        "speakerArabic",
        "wordCount",
        "cpuSeconds",
        "wallSeconds",
        "audioDurationSeconds",
        "rtf",
        "cpuRtf",
        "encodeWallSeconds",
        "wavSha256",
        "mp3Sha256",
    ]
    temporary = path.with_name(path.name + ".pending")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for item in completed:
            timing = item["timing"]
            writer.writerow(
                {
                    "chunkId": item["chunkId"],
                    "speakerId": item["speakerId"],
                    "speakerArabic": item.get("speakerArabic", ""),
                    "wordCount": item.get("wordCount", ""),
                    "cpuSeconds": timing.get("cpuSeconds"),
                    "wallSeconds": timing.get("wallSeconds"),
                    "audioDurationSeconds": timing.get("audioDurationSeconds"),
                    "rtf": timing.get("rtf"),
                    "cpuRtf": timing.get("cpuRtf"),
                    "encodeWallSeconds": timing.get("encodeWallSeconds"),
                    "wavSha256": item["wav"]["sha256"],
                    "mp3Sha256": item["mp3"]["sha256"],
                }
            )
    temporary.replace(path)


def replace_chunk(
    base_dir: Path,
    candidate_dir: Path,
    chunk_id: str,
    reference_approval_manifest: Path | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    base_path = base_dir / "manifest.json"
    candidate_path = candidate_dir / "manifest.json"
    if not base_path.is_file() or not candidate_path.is_file():
        raise ValueError("Both base and candidate manifest.json files are required")

    base = json.loads(base_path.read_text(encoding="utf-8"))
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    base_items = base.get("completedChunks")
    candidate_items = candidate.get("completedChunks")
    if (
        base.get("status") != "complete"
        or not isinstance(base_items, list)
        or len(base_items) != 8
        or base.get("chunksPlanned") != 8
    ):
        raise ValueError("Base generation must contain exactly eight completed chunks")
    if (
        candidate.get("status") != "complete"
        or not isinstance(candidate_items, list)
        or len(candidate_items) != 1
        or candidate.get("chunksPlanned") != 1
    ):
        raise ValueError("Candidate generation must contain exactly one completed chunk")
    if (
        candidate.get("episodeId") != base.get("episodeId")
        or candidate.get("episodeScriptSha256") != base.get("episodeScriptSha256")
    ):
        raise ValueError("Candidate must use the exact same approved episode script")

    base_matches = [item for item in base_items if item.get("chunkId") == chunk_id]
    candidate_item = candidate_items[0]
    if len(base_matches) != 1 or candidate_item.get("chunkId") != chunk_id:
        raise ValueError(f"Expected one matching chunk {chunk_id!r} in both manifests")
    old_item = base_matches[0]
    if (
        candidate_item.get("speakerId") != old_item.get("speakerId")
        or candidate_item.get("text") != old_item.get("text")
    ):
        raise ValueError("Replacement speaker or exact source text differs from approved chunk")

    for key in (
        "method",
        "voice",
        "referenceSpeed",
        "referencePitch",
        "synthesisSpeed",
        "nfeStep",
    ):
        if candidate_item.get("settings", {}).get(key) != old_item.get("settings", {}).get(key):
            raise ValueError(f"Replacement setting mismatch: {key}")
    old_reference = old_item.get("reference", {})
    candidate_reference = candidate_item.get("reference", {})
    reference_sha_unchanged = candidate_reference.get("sha256") == old_reference.get("sha256")
    approved_reference_change = False
    if not reference_sha_unchanged:
        if reference_approval_manifest is None or chunk_id != "chunk-03" or old_item.get("speakerId") != "pistachio":
            raise ValueError("Replacement does not use the identical reference audio SHA-256")
        approval = json.loads(reference_approval_manifest.read_text(encoding="utf-8"))
        approved_reference_change = (
            approval.get("approvalId") == candidate_reference.get("approvalId")
            and approval.get("name") == candidate_reference.get("approvalName")
            and approval.get("sha256") == candidate_reference.get("canonicalSha256")
            and approval.get("pcmSha256") == candidate_reference.get("pcmSha256")
            and approval.get("persona") == "pistachio"
            and approval.get("voice") == candidate_item.get("settings", {}).get("voice")
            and approval.get("supersedes", {}).get("sha256") == candidate_reference.get("legacyReferenceSha256")
            and approval.get("supersedes", {}).get("reproducible") is False
        )
        if not approved_reference_change:
            raise ValueError("Replacement reference differs and does not match the declared approved Pistachio reference")
    seed = candidate_item.get("settings", {}).get("seed")
    if not isinstance(seed, int) or not 0 <= seed <= 2**31 - 1:
        raise ValueError("Candidate manifest does not contain a valid explicit seed")

    target_index = base_items.index(old_item)
    old_target_hashes = {
        "wavSha256": old_item["wav"]["sha256"],
        "mp3Sha256": old_item["mp3"]["sha256"],
    }
    old_episode = deepcopy(base.get("episode", {}))
    preserved_items = {
        item["chunkId"]: deepcopy(item)
        for item in base_items
        if item["chunkId"] != chunk_id
    }
    preserved_media: dict[str, str] = {}
    for item in base_items:
        for key in ("wav", "mp3"):
            media = verify_media(base_dir, item[key], f"Base {key.upper()}")
            if item["chunkId"] != chunk_id:
                preserved_media[f'{item["chunkId"]}:{key}'] = sha256_file(media)

    new_item = deepcopy(candidate_item)
    for key in ("wav", "mp3"):
        candidate_media = verify_media(
            candidate_dir, candidate_item[key], f"Candidate {key.upper()}"
        )
        destination = verify_media(base_dir, old_item[key], f"Previous {key.upper()}")
        shutil.copy2(candidate_media, destination)
        metadata = deepcopy(candidate_item[key])
        metadata["path"] = old_item[key]["path"]
        metadata["sha256"] = sha256_file(destination)
        new_item[key] = metadata

    # Keep the replacement reference record so the reference transition is auditable.
    new_item["reference"] = deepcopy(candidate_reference)
    base_items[target_index] = new_item

    for preserved_id, expected in preserved_items.items():
        found = [item for item in base_items if item["chunkId"] == preserved_id]
        if len(found) != 1 or found[0] != expected:
            raise RuntimeError(f"Unrelated manifest entry changed: {preserved_id}")
    for key, expected_sha in preserved_media.items():
        chunk_id_value, media_key = key.split(":")
        item = next(item for item in base_items if item["chunkId"] == chunk_id_value)
        path = safe_media_path(base_dir, item[media_key]["path"])
        if sha256_file(path) != expected_sha:
            raise RuntimeError(f"Unrelated media SHA changed: {key}")

    history = base.setdefault("replacementHistory", [])
    history.append(
        {
            "chunkId": chunk_id,
            "seed": seed,
            "previous": old_target_hashes,
            "replacement": {
                "wavSha256": new_item["wav"]["sha256"],
                "mp3Sha256": new_item["mp3"]["sha256"],
            },
            "referenceSha256": new_item["reference"]["sha256"],
            "referenceTransition": ({
                "fromSha256": old_reference.get("sha256"),
                "toSha256": new_item["reference"].get("sha256"),
                "approvalId": new_item["reference"].get("approvalId"),
                "pcmSha256": new_item["reference"].get("pcmSha256"),
                "note": new_item["reference"].get("approvalNote"),
            } if approved_reference_change else None),
            "episodeScriptSha256": base["episodeScriptSha256"],
        }
    )

    total_cpu = sum(float(item["timing"]["cpuSeconds"]) for item in base_items)
    total_wall = sum(float(item["timing"]["wallSeconds"]) for item in base_items)
    total_audio = sum(
        float(item["timing"]["audioDurationSeconds"]) for item in base_items
    )
    base["measurementSummary"] = {
        "totalInferenceCpuSeconds": round(total_cpu, 6),
        "totalInferenceWallSeconds": round(total_wall, 6),
        "totalSpeechAudioSeconds": round(total_audio, 6),
        "aggregateWallRtf": round(total_wall / total_audio, 6),
        "aggregateCpuRtf": round(total_cpu / total_audio, 6),
    }
    base["episode"] = make_episode_mix(base_dir, base_items)
    base["singleChunkReplacement"] = {
        "chunkId": chunk_id,
        "seed": seed,
        "requiresFullRenderAndQA": True,
        "replacementElapsedSeconds": round(time.perf_counter() - started, 6),
    }

    temporary = base_path.with_name(base_path.name + ".pending")
    temporary.write_text(
        json.dumps(base, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(base_path)
    write_measurements(base_dir / "measurement-table.csv", base_items)

    return {
        "status": "replaced",
        "chunkId": chunk_id,
        "seed": seed,
        "preservedChunkCount": len(preserved_items),
        "preservedMediaCount": len(preserved_media),
        "preservedMediaSha256": preserved_media,
        "replacement": history[-1]["replacement"],
        "episodeRebuilt": base["episode"],
        "replacementElapsedSeconds": base["singleChunkReplacement"][
            "replacementElapsedSeconds"
        ],
        "previousEpisode": old_episode,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", required=True)
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--chunk-id", required=True)
    parser.add_argument("--reference-approval-manifest")
    args = parser.parse_args()
    result = replace_chunk(
        Path(args.base_dir),
        Path(args.candidate_dir),
        args.chunk_id,
        Path(args.reference_approval_manifest) if args.reference_approval_manifest else None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

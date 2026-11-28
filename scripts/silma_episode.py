#!/usr/bin/env python3
"""Single-job, CPU-only SILMA episode pilot with atomic per-chunk checkpoints."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import time
import traceback
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MODEL_REPO = "silma-ai/silma-tts"
VOCODER_REPO = "charactr/vocos-mel-24khz"
EXPECTED_MODEL_SHA256 = "f43256d0b78b8803c638aed0875da5a4b372b4a784690a0156e5baff14f7336c"
EXPECTED_VOCODER_SHA256 = "97ec976ad1fd67a33ab2682d29c0ac7df85234fae875aefcc5fb215681a91b2a"
EXPECTED_SILMA_TTS = "1.0.5"
EXPECTED_TORCH = "2.11.0"
NFE_STEP = 16
ARABIC_REFERENCE_TEXT = "مَرْحَبًا، هَذَا صَوْتٌ تَجْرِيبِيٌّ لِشَخْصِيَّةٍ كَرْتُونِيَّةٍ."

PERSONAS = {
    "pistachio": {
        "labelArabic": "فستق", "voice": "ar+f3", "speed": 160, "pitch": 68,
        "reference": "pistachio-reference.wav",
    },
    "hazelnut": {
        "labelArabic": "بندق", "voice": "ar+m3", "speed": 140, "pitch": 43,
        "reference": "hazelnut-reference.wav",
    },
}

CHUNKS: list[dict[str, str]] = []
EPISODE_ID = "letter-alif"
SCRIPT_MANIFEST_SHA256 = ""
AUTO_SEED_ATTEMPTS = 5
AUTO_SEED_MAX_CHUNKS_PER_RUN = 16


def select_chunks(
    chunks: list[dict[str, str]],
    chunk_id: str | None,
) -> list[dict[str, str]]:
    if chunk_id is None:
        return list(chunks)
    matches = [item for item in chunks if item.get("chunkId") == chunk_id]
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one chunk with id {chunk_id!r}; found {len(matches)}"
        )
    return matches


def resolve_seed(index: int, override: int | None) -> int:
    if index < 1:
        raise ValueError("Chunk index must be positive")
    if override is not None:
        if not 0 <= override <= 2**31 - 1:
            raise ValueError("Seed must be between 0 and 2147483647")
        return override
    return 4242 + index


def authorize_run_event(
    event: str,
    attempt: str,
    selected_chunk_id: str | None,
    seed_override: int | None,
) -> dict[str, Any]:
    if attempt != "1":
        raise RuntimeError(
            f"Run guard rejected attempt: expected 1, received {attempt!r}"
        )
    if event == "workflow_dispatch":
        return {
            "mode": "full-episode" if selected_chunk_id is None else "single-chunk",
            "automaticRetry": False,
        }
    if event != "push" or selected_chunk_id is None or seed_override is None:
        raise RuntimeError(
            "Run guard rejected event: push is restricted to a seeded single-chunk request"
        )
    request_name = os.environ.get("ARNOB_RUN_REQUEST_PATH", "")
    request_path = Path(request_name)
    if not request_name or not request_path.is_file():
        raise RuntimeError("Run guard rejected push event: missing run request file")
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if request.get("schemaVersion") != 1 or request.get("episodeId") != EPISODE_ID:
        raise RuntimeError(
            "Run guard rejected push event: invalid request schema or episode"
        )
    if request.get("chunkId") != selected_chunk_id:
        raise RuntimeError(
            "Run guard rejected push event: chunk id does not match request"
        )
    allowed_seeds = request.get("seeds")
    if not isinstance(allowed_seeds, list):
        allowed_seeds = [request.get("seed")]
    if seed_override not in allowed_seeds:
        raise RuntimeError(
            "Run guard rejected push event: seed is not authorized by request"
        )
    purpose = request.get("purpose")
    if purpose not in {"seed-trials", "one-shot-replacement"}:
        raise RuntimeError(
            "Run guard rejected push event: unsupported request purpose"
        )
    return {
        "mode": purpose,
        "chunkId": selected_chunk_id,
        "seed": seed_override,
        "requestPath": request_name,
        "automaticRetry": False,
    }




def auto_seed_candidates(index: int) -> list[int]:
    """Return the five explicitly approved candidate seeds for a one-based chunk index."""
    base = resolve_seed(index, None)
    return [base + offset for offset in range(AUTO_SEED_ATTEMPTS)]


def pcm_sha256(path: Path) -> tuple[str, int, int, int, int]:
    """Hash uncompressed PCM frame bytes only; RIFF metadata is intentionally excluded."""
    with wave.open(str(path), "rb") as stream:
        channels = stream.getnchannels()
        sample_rate = stream.getframerate()
        sample_width = stream.getsampwidth()
        samples = stream.getnframes()
        pcm = stream.readframes(samples)
    return hashlib.sha256(pcm).hexdigest(), channels, sample_rate, sample_width, samples


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".pending")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def run_checked(args: list[str]) -> str:
    completed = subprocess.run(args, check=True, capture_output=True, text=True)
    return (completed.stdout or completed.stderr).strip()


def probe_audio(path: Path) -> dict[str, Any]:
    raw = run_checked([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,size:stream=codec_name,sample_rate,channels,bit_rate",
        "-of", "json", str(path),
    ])
    payload = json.loads(raw)
    streams = payload.get("streams", [])
    if not streams:
        raise RuntimeError(f"ffprobe found no media stream in {path}")
    stream = streams[0]
    return {
        "codec": stream.get("codec_name"),
        "sampleRate": int(stream.get("sample_rate", 0)),
        "channels": int(stream.get("channels", 0)),
        "bitRate": int(stream.get("bit_rate") or 0),
        "durationSeconds": float(payload["format"]["duration"]),
        "sizeBytes": int(payload["format"]["size"]),
    }


def validate_wav(path: Path, sf: Any) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f"Missing or empty WAV output: {path}")
    audio, rate = sf.read(path, dtype="float32", always_2d=True)
    if (
        rate != 24000 or audio.shape[1] != 1 or audio.size == 0
        or not all(math.isfinite(float(value)) for value in (audio.min(), audio.max()))
    ):
        raise RuntimeError(f"Invalid WAV output (expected finite mono 24kHz): {path}")
    peak = float(abs(audio).max())
    rms = float((audio.astype("float64") ** 2).mean() ** 0.5)
    if peak <= 0 or rms <= 0:
        raise RuntimeError(f"Silent WAV output: {path}")
    duration = len(audio) / float(rate)
    if duration <= 0:
        raise RuntimeError(f"Invalid WAV duration: {path}")
    return {
        "codec": "pcm_s16le",
        "sampleRate": int(rate),
        "channels": int(audio.shape[1]),
        "durationSeconds": round(duration, 6),
        "peakAmplitude": round(peak, 8),
        "rmsAmplitude": round(rms, 8),
        "sizeBytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def write_csv_atomic(path: Path, completed: list[dict[str, Any]]) -> None:
    columns = [
        "chunkId", "speakerId", "speakerArabic", "wordCount",
        "cpuSeconds", "wallSeconds", "audioDurationSeconds",
        "rtf", "cpuRtf", "encodeWallSeconds", "wavSha256", "mp3Sha256",
    ]
    temp = path.with_name(path.name + ".pending")
    with temp.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for item in completed:
            timing = item["timing"]
            writer.writerow({
                "chunkId": item["chunkId"],
                "speakerId": item["speakerId"],
                "speakerArabic": item["speakerArabic"],
                "wordCount": item["wordCount"],
                "cpuSeconds": timing.get("cpuSeconds"),
                "wallSeconds": timing.get("wallSeconds"),
                "audioDurationSeconds": timing.get("audioDurationSeconds"),
                "rtf": timing.get("rtf"),
                "cpuRtf": timing.get("cpuRtf"),
                "encodeWallSeconds": timing.get("encodeWallSeconds"),
                "wavSha256": item["wav"]["sha256"],
                "mp3Sha256": item["mp3"]["sha256"],
            })
    os.replace(temp, path)


def save_progress(root: Path, manifest: dict[str, Any]) -> None:
    write_json_atomic(root / "manifest.json", manifest)
    write_csv_atomic(root / "measurement-table.csv", manifest.get("completedChunks", []))


def make_mp3(wav_path: Path, mp3_path: Path, root: Path) -> tuple[dict[str, Any], float]:
    pending = mp3_path.with_name(mp3_path.stem + ".pending.mp3")
    started = time.perf_counter()
    run_checked([
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-i", str(wav_path), "-map", "0:a:0", "-vn",
        "-ac", "1", "-ar", "24000", "-codec:a", "libmp3lame", "-b:a", "128k",
        str(pending),
    ])
    elapsed = time.perf_counter() - started
    meta = probe_audio(pending)
    if (
        meta["codec"] != "mp3" or meta["sampleRate"] != 24000
        or meta["channels"] != 1 or not 120000 <= meta["bitRate"] <= 136000
    ):
        raise RuntimeError(f"MP3 validation failed: {pending}; metadata={meta}")
    os.replace(pending, mp3_path)
    meta["sha256"] = sha256_file(mp3_path)
    meta["path"] = str(mp3_path.relative_to(root))
    return meta, elapsed


def make_episode_mix(root: Path, completed: list[dict[str, Any]]) -> dict[str, Any]:
    concat_file = root / "concat-list.txt"
    lines = []
    for item in completed:
        wav_path = (root / item["wav"]["path"]).resolve()
        safe_path = str(wav_path).replace("'", "'\\''")
        lines.append(f"file '{safe_path}'")
    concat_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    episode_wav = root / "letter-alif.wav"
    pending_wav = root / "letter-alif.pending.wav"
    run_checked([
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-c:a", "pcm_s16le", str(pending_wav),
    ])
    wav_meta = probe_audio(pending_wav)
    if wav_meta["codec"] != "pcm_s16le" or wav_meta["sampleRate"] != 24000 or wav_meta["channels"] != 1:
        raise RuntimeError(f"Concatenated episode WAV validation failed: {wav_meta}")
    os.replace(pending_wav, episode_wav)
    wav_meta["sha256"] = sha256_file(episode_wav)
    wav_meta["path"] = episode_wav.name

    episode_mp3 = root / "letter-alif.mp3"
    mp3_meta, encode_wall = make_mp3(episode_wav, episode_mp3, root)
    mp3_meta["path"] = episode_mp3.name
    return {"wav": wav_meta, "mp3": mp3_meta, "encodeWallSeconds": round(encode_wall, 6)}


def write_tone(path: Path, frequency: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 24000
    frames = bytearray()
    frame_count = int(rate * 0.65)
    import struct
    for index in range(frame_count):
        envelope = min(1.0, index / 240.0, (frame_count - index) / 240.0)
        sample = int(4200 * envelope * math.sin(2 * math.pi * frequency * index / rate))
        frames.extend(struct.pack("<h", sample))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(frames)


def run_dry(root: Path, started: float) -> int:
    manifest: dict[str, Any] = {
        "schemaVersion": 1,
        "episodeId": EPISODE_ID,
        "status": "running",
        "dryRun": True,
        "method": "A",
        "nfeStep": NFE_STEP,
        "modelInferenceInvoked": False,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "runPolicy": {"branch": "dry-run", "attempt": 1, "automaticRetry": False},
        "scriptedWords": sum(len(item["text"].split()) for item in CHUNKS),
        "episodeScriptSha256": SCRIPT_MANIFEST_SHA256,
        "targetWords": sum(len(item["text"].split()) for item in CHUNKS),
        "chunksPlanned": len(CHUNKS),
        "completedChunks": [],
        "weights": {"silma": {"status": "not_downloaded"}, "vocos": {"status": "not_downloaded"}},
    }
    save_progress(root, manifest)
    try:
        for index, chunk in enumerate(CHUNKS, start=1):
            speaker = chunk["speakerId"]
            base = f"{index:02d}-{speaker}"
            wav_path = root / "chunks" / f"{base}.wav"
            mp3_path = root / "chunks" / f"{base}.mp3"
            write_tone(wav_path, 380.0 + index * 21.0)
            wav_meta = probe_audio(wav_path)
            if wav_meta["codec"] != "pcm_s16le" or wav_meta["sampleRate"] != 24000 or wav_meta["channels"] != 1:
                raise RuntimeError(f"Dry-run WAV invalid: {wav_path}")
            wav_meta["sha256"] = sha256_file(wav_path)
            wav_meta["path"] = str(wav_path.relative_to(root))
            mp3_meta, encode_wall = make_mp3(wav_path, mp3_path, root)
            entry = {
                "chunkId": chunk["chunkId"], "speakerId": speaker,
                "speakerArabic": PERSONAS[speaker]["labelArabic"],
                "text": chunk["text"], "wordCount": len(chunk["text"].split()),
                "reference": {"sha256": "dry-run"},
                "settings": {"method": "A", "voice": PERSONAS[speaker]["voice"], "nfeStep": NFE_STEP},
                "timing": {
                    "cpuSeconds": None, "wallSeconds": None,
                    "audioDurationSeconds": wav_meta["durationSeconds"],
                    "rtf": None, "cpuRtf": None,
                    "encodeWallSeconds": round(encode_wall, 6),
                    "synthetic": True,
                },
                "wav": wav_meta, "mp3": mp3_meta, "synthetic": True,
            }
            manifest["completedChunks"].append(entry)
            save_progress(root, manifest)
        manifest["episode"] = make_episode_mix(root, manifest["completedChunks"])
        manifest["status"] = "complete"
        manifest["modelInferenceInvoked"] = False
        manifest["jobElapsedSeconds"] = round(time.perf_counter() - started, 6)
        save_progress(root, manifest)
        print(json.dumps({
            "status": manifest["status"], "dryRun": True,
            "modelInferenceInvoked": False, "chunks": len(manifest["completedChunks"]),
            "modelWeightsDownloaded": False,
        }, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        save_progress(root, manifest)
        print(f"EPISODE_PILOT_DRY_RUN_FAILURE={manifest['error']}", file=sys.stderr)
        return 1


def load_audio_via_soundfile(
    uri: str | Path,
    frame_offset: int = 0,
    num_frames: int = -1,
    normalize: bool = True,
    channels_first: bool = True,
    format: str | None = None,
    buffer_size: int = 4096,
    backend: str | None = None,
) -> tuple[Any, int]:
    import numpy as np
    import soundfile as sf
    import torch

    if frame_offset < 0 or num_frames < -1:
        raise ValueError("frame_offset must be >= 0 and num_frames must be >= -1")
    audio, sample_rate = sf.read(uri, dtype="float32", always_2d=True)
    if frame_offset:
        audio = audio[frame_offset:]
    if num_frames >= 0:
        audio = audio[:num_frames]
    if audio.size == 0 or not np.isfinite(audio).all():
        raise RuntimeError(f"soundfile could not decode reference audio: {uri}")
    data = audio.T if channels_first else audio
    data = np.ascontiguousarray(data, dtype=np.float32)
    return torch.from_numpy(data), int(sample_rate)


def run_real(
    root: Path,
    references_dir: Path,
    started: float,
    selected_chunk_id: str | None = None,
    seed_override: int | None = None,
) -> int:
    active_chunks = select_chunks(CHUNKS, selected_chunk_id)
    manifest: dict[str, Any] = {
        "schemaVersion": 1,
        "episodeId": EPISODE_ID,
        "status": "running",
        "dryRun": False,
        "method": "A",
        "nfeStep": NFE_STEP,
        "modelInferenceInvoked": False,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "runPolicy": {
            "event": os.environ.get("GITHUB_EVENT_NAME", ""),
            "branch": os.environ.get("GITHUB_REF", ""),
            "attempt": int(os.environ.get("GITHUB_RUN_ATTEMPT", "0")),
            "runId": os.environ.get("GITHUB_RUN_ID", ""),
            "automaticRetry": False,
            "timeoutMinutes": 60,
        },
        "scriptedWords": sum(len(item["text"].split()) for item in active_chunks),
        "episodeScriptSha256": SCRIPT_MANIFEST_SHA256,
        "targetWords": sum(len(item["text"].split()) for item in active_chunks),
        "chunksPlanned": len(active_chunks),
        "totalChunksInScript": len(CHUNKS),
        "selectedChunkId": selected_chunk_id,
        "seedOverride": seed_override,
        "completedChunks": [],
        "weights": {},
    }
    save_progress(root, manifest)

    try:
        manifest["runPolicy"]["request"] = authorize_run_event(
            os.environ.get("GITHUB_EVENT_NAME", ""),
            os.environ.get("GITHUB_RUN_ATTEMPT", ""),
            selected_chunk_id,
            seed_override,
        )

        import numpy as np
        import soundfile as sf
        import torch
        import torchaudio

        if torch.cuda.is_available() or torch.version.cuda is not None:
            raise RuntimeError("CPU-only guard rejected a CUDA-enabled PyTorch runtime")
        versions = {
            "python": platform.python_version(),
            "silma-tts": importlib.metadata.version("silma-tts"),
            "torch": torch.__version__,
            "torchaudio": torchaudio.__version__,
            "numpy": np.__version__,
        }
        if versions["silma-tts"] != EXPECTED_SILMA_TTS:
            raise RuntimeError(f"Expected silma-tts=={EXPECTED_SILMA_TTS}, found {versions['silma-tts']}")
        if versions["torch"].split("+", 1)[0] != EXPECTED_TORCH or versions["torchaudio"].split("+", 1)[0] != EXPECTED_TORCH:
            raise RuntimeError(f"Unexpected CPU PyTorch/torchaudio versions: {versions['torch']} / {versions['torchaudio']}")
        torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:
            pass
        torchaudio.load = load_audio_via_soundfile

        ref_manifest_path = references_dir / "reference-manifest.json"
        if not ref_manifest_path.is_file():
            raise RuntimeError(f"Missing Method A reference manifest: {ref_manifest_path}")
        ref_manifest = json.loads(ref_manifest_path.read_text(encoding="utf-8"))
        if ref_manifest.get("method") != "A" or ref_manifest.get("referenceText") != ARABIC_REFERENCE_TEXT:
            raise RuntimeError("Method A reference manifest does not match the approved Arabic reference text")

        reference_details: dict[str, dict[str, Any]] = {}
        for persona, settings in PERSONAS.items():
            entry = ref_manifest.get("files", {}).get(persona, {})
            ref_path = references_dir / settings["reference"]
            if not ref_path.is_file():
                raise RuntimeError(f"Missing committed Method A PCM reference for {persona}")
            actual_pcm, channels, rate, sample_width, samples = pcm_sha256(ref_path)
            if (channels, rate, sample_width) != (1, 24000, 2):
                raise RuntimeError(f"Method A PCM container mismatch for {persona}")
            if actual_pcm != entry.get("pcmSha256"):
                raise RuntimeError(
                    f"REFERENCE_PCM_SHA256_MISMATCH {persona} "
                    f"expected={entry.get('pcmSha256')} actual={actual_pcm}"
                )
            if samples != entry.get("samples"):
                raise RuntimeError(
                    f"REFERENCE_SAMPLE_COUNT_MISMATCH {persona} "
                    f"expected={entry.get('samples')} actual={samples}"
                )
            audio, decoded_rate = sf.read(ref_path, dtype="float32", always_2d=True)
            if decoded_rate != 24000 or audio.shape[1] != 1 or audio.size == 0 or not np.isfinite(audio).all():
                raise RuntimeError(f"Method A reference audio failed decode preflight for {persona}")
            declared = ref_manifest.get("personas", {}).get(persona, {})
            if (
                declared.get("voice") != settings["voice"]
                or declared.get("speed") != settings["speed"]
                or declared.get("pitch") != settings["pitch"]
            ):
                raise RuntimeError(f"Method A voice settings mismatch for {persona}")
            reference_details[persona] = {
                "path": settings["reference"],
                "sha256": sha256_file(ref_path),
                "pcmSha256": actual_pcm,
                "verification": "pcm-sha256-only",
                "sizeBytes": ref_path.stat().st_size,
                "sampleRate": rate,
                "channels": channels,
                "sampleWidthBytes": sample_width,
                "samples": samples,
                **{
                    key: entry[key]
                    for key in (
                        "approvalId", "approvalName", "canonicalSha256",
                        "legacyReferenceSha256", "legacyReferenceReproducible",
                        "approvalNote",
                    )
                    if key in entry
                },
            }
        manifest["references"] = reference_details
        manifest["runtime"] = versions
        save_progress(root, manifest)

        runner_temp = Path(os.environ.get("RUNNER_TEMP", "/tmp"))
        cache_root = runner_temp / "silma-letter-card-hf-cache"
        hub_cache = cache_root / "hub"
        hub_cache.mkdir(parents=True, exist_ok=True)
        os.environ["HF_HOME"] = str(cache_root)
        os.environ["HUGGINGFACE_HUB_CACHE"] = str(hub_cache)
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

        from cached_path import cached_path

        download_started = time.perf_counter()
        model_path = Path(cached_path(f"hf://{MODEL_REPO}/model.pt", cache_dir=str(hub_cache))).resolve(strict=True)
        vocoder_path = Path(cached_path(f"hf://{VOCODER_REPO}/pytorch_model.bin", cache_dir=str(hub_cache))).resolve(strict=True)
        download_wall = time.perf_counter() - download_started
        model_sha = sha256_file(model_path)
        vocoder_sha = sha256_file(vocoder_path)
        weights = {
            "silma": {
                "repository": MODEL_REPO, "file": model_path.name,
                "sizeBytes": model_path.stat().st_size, "sha256": model_sha,
                "expectedSha256": EXPECTED_MODEL_SHA256,
                "verifiedBeforeInference": model_sha == EXPECTED_MODEL_SHA256,
            },
            "vocos": {
                "repository": VOCODER_REPO, "file": vocoder_path.name,
                "sizeBytes": vocoder_path.stat().st_size, "sha256": vocoder_sha,
                "expectedSha256": EXPECTED_VOCODER_SHA256,
                "verifiedBeforeInference": vocoder_sha == EXPECTED_VOCODER_SHA256,
            },
        }
        manifest["weights"] = weights
        manifest["downloadWallSeconds"] = round(download_wall, 6)
        write_json_atomic(root / "weight-preflight.json", {
            "status": "PASS" if all(item["verifiedBeforeInference"] for item in weights.values()) else "FAIL",
            "weights": weights,
            "downloadWallSeconds": round(download_wall, 6),
        })
        save_progress(root, manifest)
        if not all(item["verifiedBeforeInference"] for item in weights.values()):
            raise RuntimeError("SILMA/Vocos weight SHA-256 mismatch before inference; aborting without generation")

        from silma_tts.api import SilmaTTS

        init_cpu_started = time.process_time()
        init_wall_started = time.perf_counter()
        tts = SilmaTTS(
            device="cpu",
            hf_cache_dir=str(hub_cache),
            enable_normalizer=False,
            force_tashkeel=False,
        )
        model_init_cpu = time.process_time() - init_cpu_started
        model_init_wall = time.perf_counter() - init_wall_started
        manifest["modelInitialization"] = {
            "cpuSeconds": round(model_init_cpu, 6),
            "wallSeconds": round(model_init_wall, 6),
        }
        manifest["runtime"]["device"] = "cpu"
        manifest["runtime"]["cudaAvailable"] = False
        save_progress(root, manifest)

        for index, chunk in enumerate(CHUNKS, start=1):
            if selected_chunk_id is not None and chunk["chunkId"] != selected_chunk_id:
                continue
            speaker = chunk["speakerId"]
            voice = PERSONAS[speaker]
            reference_path = references_dir / voice["reference"]
            stem = f"{index:02d}-{speaker}"
            final_wav = root / "chunks" / f"{stem}.wav"
            final_mp3 = root / "chunks" / f"{stem}.mp3"
            final_wav.parent.mkdir(parents=True, exist_ok=True)

            trial_records: list[dict[str, Any]] = []
            successful_durations: list[float] = []
            if auto_seed:
                candidate_seeds = auto_seed_candidates(index)
            else:
                candidate_seeds = [resolve_seed(index, seed_override)]

            chosen: dict[str, Any] | None = None
            for attempt_number, inference_seed in enumerate(candidate_seeds, start=1):
                trial_dir = root / "auto-seed-trials" / chunk["chunkId"]
                trial_dir.mkdir(parents=True, exist_ok=True)
                candidate_wav = trial_dir / f"seed-{inference_seed}.wav"
                candidate_mp3 = trial_dir / f"seed-{inference_seed}.mp3"
                pending_wav = trial_dir / f"seed-{inference_seed}.pending.wav"
                if pending_wav.exists():
                    pending_wav.unlink()

                cpu_started = time.process_time()
                wall_started = time.perf_counter()
                manifest["modelInferenceInvoked"] = True
                save_progress(root, manifest)
                tts.infer(
                    ref_file=str(reference_path),
                    ref_text=ARABIC_REFERENCE_TEXT,
                    gen_text=chunk["text"],
                    file_wave=str(pending_wav),
                    seed=inference_seed,
                    speed=1.0,
                    nfe_step=NFE_STEP,
                    normalize_numbers=False,
                    force_tashkeel=False,
                )
                cpu_seconds = time.process_time() - cpu_started
                wall_seconds = time.perf_counter() - wall_started
                wav_meta = validate_wav(pending_wav, sf)
                os.replace(pending_wav, candidate_wav)
                wav_meta["path"] = str(candidate_wav.relative_to(root))
                mp3_meta, encode_wall = make_mp3(candidate_wav, candidate_mp3, root)
                mp3_meta["path"] = str(candidate_mp3.relative_to(root))

                candidate_entry = {
                    "chunkId": chunk["chunkId"],
                    "speakerId": speaker,
                    "text": chunk["text"],
                    "settings": {"seed": inference_seed},
                    "wav": wav_meta,
                    "mp3": mp3_meta,
                }
                candidate_manifest_path = trial_dir / f"seed-{inference_seed}.manifest.json"
                write_json_atomic(candidate_manifest_path, {
                    "status": "complete",
                    "dryRun": False,
                    "modelInferenceInvoked": True,
                    "completedChunks": [candidate_entry],
                })

                evaluate_command = [
                    sys.executable,
                    str(Path(__file__).with_name("evaluate_silma_seed.py")),
                    "--mp3", str(candidate_mp3),
                    "--wav", str(candidate_wav),
                    "--manifest", str(candidate_manifest_path),
                    "--chunk-id", chunk["chunkId"],
                    "--maximum-internal-silence", "0.45",
                    "--duration-tolerance", "0.30",
                    "--source-text", chunk["text"],
                ]
                for successful_duration in successful_durations:
                    evaluate_command.extend(["--successful-duration", str(successful_duration)])
                evaluated = subprocess.run(
                    evaluate_command,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if evaluated.returncode not in (0, 1):
                    raise RuntimeError(
                        f"Seed evaluator failed for {chunk['chunkId']} seed {inference_seed}: "
                        f"{evaluated.stderr[-2000:]}"
                    )
                try:
                    evaluation = json.loads(evaluated.stdout)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"Seed evaluator returned invalid JSON for {chunk['chunkId']} seed {inference_seed}"
                    ) from exc
                trial = {
                    "attempt": attempt_number,
                    "seed": inference_seed,
                    "durationSeconds": evaluation.get("durationSeconds"),
                    "longestInternalSilenceSeconds": evaluation.get("longestInternalSilenceSeconds"),
                    "peakAmplitude": evaluation.get("peakAmplitude"),
                    "rmsAmplitude": evaluation.get("rmsAmplitude"),
                    "passesDuration": evaluation.get("passesDuration"),
                    "passesInternalSilence": evaluation.get("passesInternalSilence"),
                    "passesPeakAmplitude": evaluation.get("passesPeakAmplitude"),
                    "passesRms": evaluation.get("passesRms"),
                    "pass": evaluation.get("pass") is True,
                    "durationPolicy": evaluation.get("durationPolicy"),
                    "candidateWav": str(candidate_wav.relative_to(root)),
                    "candidateMp3": str(candidate_mp3.relative_to(root)),
                }
                trial_records.append(trial)
                print(
                    "AUTO_SEED_TRIAL " + json.dumps(
                        {"chunkId": chunk["chunkId"], **trial}, ensure_ascii=False
                    ),
                    flush=True,
                )
                if evaluation.get("pass") is True:
                    chosen = {
                        "seed": inference_seed,
                        "candidateWav": candidate_wav,
                        "candidateMp3": candidate_mp3,
                        "wav": wav_meta,
                        "mp3": mp3_meta,
                        "cpuSeconds": cpu_seconds,
                        "wallSeconds": wall_seconds,
                        "encodeWallSeconds": encode_wall,
                        "evaluation": evaluation,
                    }
                    successful_durations.append(float(evaluation["durationSeconds"]))
                    break

            if chosen is None:
                best = min(
                    trial_records,
                    key=lambda item: (
                        float("inf")
                        if item.get("longestInternalSilenceSeconds") is None
                        else item["longestInternalSilenceSeconds"]
                    ),
                )
                failure = {
                    "chunkId": chunk["chunkId"],
                    "decision": "REJECTED_NEEDS_SCRIPT_EDIT",
                    "message": f"{chunk['chunkId']} لم يجتز، يلزم تعديل النص",
                    "bestRejectedSeed": best["seed"],
                    "triedSeeds": trial_records,
                }
                manifest.setdefault("failedChunks", []).append(failure)
                manifest.setdefault("autoSeedTrials", {})[chunk["chunkId"]] = trial_records
                save_progress(root, manifest)
                print("AUTO_SEED_REJECTED " + json.dumps(failure, ensure_ascii=False), file=sys.stderr, flush=True)
                raise RuntimeError(failure["message"])

            shutil.copy2(chosen["candidateWav"], final_wav)
            shutil.copy2(chosen["candidateMp3"], final_mp3)
            wav_meta = dict(chosen["wav"])
            wav_meta["path"] = str(final_wav.relative_to(root))
            wav_meta["sha256"] = sha256_file(final_wav)
            mp3_meta = dict(chosen["mp3"])
            mp3_meta["path"] = str(final_mp3.relative_to(root))
            mp3_meta["sha256"] = sha256_file(final_mp3)

            timing = {
                "cpuSeconds": round(chosen["cpuSeconds"], 6),
                "wallSeconds": round(chosen["wallSeconds"], 6),
                "audioDurationSeconds": wav_meta["durationSeconds"],
                "rtf": round(chosen["wallSeconds"] / wav_meta["durationSeconds"], 6),
                "cpuRtf": round(chosen["cpuSeconds"] / wav_meta["durationSeconds"], 6),
                "nfeStep": NFE_STEP,
                "encodeWallSeconds": round(chosen["encodeWallSeconds"], 6),
                "synthetic": False,
            }
            entry = {
                "chunkId": chunk["chunkId"],
                "speakerId": speaker,
                "speakerArabic": voice["labelArabic"],
                "text": chunk["text"],
                "wordCount": len(chunk["text"].split()),
                "settings": {
                    "method": "A", "voice": voice["voice"],
                    "referenceSpeed": voice["speed"], "referencePitch": voice["pitch"],
                    "synthesisSpeed": 1.0, "nfeStep": NFE_STEP,
                    "seed": chosen["seed"],
                    "seedSelection": "auto-first-passing" if auto_seed else "explicit-or-default",
                },
                "reference": reference_details[speaker],
                "timing": timing,
                "wav": wav_meta,
                "mp3": mp3_meta,
                "synthetic": False,
            }
            if auto_seed:
                entry["autoSeedTrials"] = trial_records
                manifest.setdefault("autoSeedTrials", {})[chunk["chunkId"]] = trial_records
            manifest["completedChunks"].append(entry)
            save_progress(root, manifest)
            print(json.dumps({
                "checkpoint": index,
                "total": len(CHUNKS),
                "chunkId": chunk["chunkId"],
                "speaker": voice["labelArabic"],
                "chosenSeed": chosen["seed"],
                "trials": trial_records,
                "cpuSeconds": timing["cpuSeconds"],
                "wallSeconds": timing["wallSeconds"],
                "durationSeconds": timing["audioDurationSeconds"],
                "rtf": timing["rtf"],
                "wavSha256": wav_meta["sha256"],
                "mp3Sha256": mp3_meta["sha256"],
            }, ensure_ascii=False), flush=True)

        if len(manifest["completedChunks"]) != len(active_chunks):
            raise RuntimeError(
                "Unexpected completed chunk count: "
                f"{len(manifest['completedChunks'])}/{len(active_chunks)}"
            )

        if selected_chunk_id is None:
            manifest["episode"] = make_episode_mix(root, manifest["completedChunks"])
            total_cpu = sum(
                float(item["timing"]["cpuSeconds"])
                for item in manifest["completedChunks"]
            )
            total_wall = sum(
                float(item["timing"]["wallSeconds"])
                for item in manifest["completedChunks"]
            )
            total_audio = sum(
                float(item["timing"]["audioDurationSeconds"])
                for item in manifest["completedChunks"]
            )
            manifest["measurementSummary"] = {
                "totalInferenceCpuSeconds": round(total_cpu, 6),
                "totalInferenceWallSeconds": round(total_wall, 6),
                "totalSpeechAudioSeconds": round(total_audio, 6),
                "aggregateWallRtf": round(total_wall / total_audio, 6),
                "aggregateCpuRtf": round(total_cpu / total_audio, 6),
            }
        else:
            total_cpu = sum(
                float(item["timing"]["cpuSeconds"])
                for item in manifest["completedChunks"]
            )
            total_wall = sum(
                float(item["timing"]["wallSeconds"])
                for item in manifest["completedChunks"]
            )
            total_audio = sum(
                float(item["timing"]["audioDurationSeconds"])
                for item in manifest["completedChunks"]
            )
            manifest["measurementSummary"] = {
                "scope": "single-chunk",
                "totalInferenceCpuSeconds": round(total_cpu, 6),
                "totalInferenceWallSeconds": round(total_wall, 6),
                "totalSpeechAudioSeconds": round(total_audio, 6),
                "aggregateWallRtf": round(total_wall / total_audio, 6),
                "aggregateCpuRtf": round(total_cpu / total_audio, 6),
            }
            manifest["partialGeneration"] = {
                "chunkId": selected_chunk_id,
                "seed": seed_override,
                "fullEpisodeRebuildRequired": True,
            }
        manifest["jobElapsedSeconds"] = round(time.perf_counter() - started, 6)
        manifest["status"] = "complete"
        manifest["completedAt"] = datetime.now(timezone.utc).isoformat()
        save_progress(root, manifest)
        print(json.dumps({
            "status": "complete",
            "chunks": len(manifest["completedChunks"]),
            "totalInferenceCpuSeconds": manifest["measurementSummary"]["totalInferenceCpuSeconds"],
            "totalInferenceWallSeconds": manifest["measurementSummary"]["totalInferenceWallSeconds"],
            "actualGeneratedSpeechSeconds": manifest["measurementSummary"]["totalSpeechAudioSeconds"],
            "weights": weights,
        }, ensure_ascii=False, indent=2), flush=True)
        return 0

    except Exception as exc:
        manifest["status"] = "failed"
        manifest["failedAt"] = datetime.now(timezone.utc).isoformat()
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        manifest["jobElapsedSeconds"] = round(time.perf_counter() - started, 6)
        try:
            save_progress(root, manifest)
            (root / "failure.txt").write_text(
                manifest["error"] + "\n\n" + traceback.format_exc(),
                encoding="utf-8",
            )
        except Exception as persist_error:
            print(f"FAILED_TO_PERSIST_FAILURE={type(persist_error).__name__}: {persist_error}", file=sys.stderr)
        print(f"EPISODE_PILOT_FAILURE={manifest['error']}", file=sys.stderr, flush=True)
        print(f"COMPLETED_CHUNKS={len(manifest.get('completedChunks', []))}/{len(CHUNKS)}", file=sys.stderr, flush=True)
        return 1


def prepare_output_dir(root: Path) -> None:
    allowed_existing = {"logs", "references", "chunks"}
    if root.exists():
        unexpected = [item.name for item in root.iterdir() if item.name not in allowed_existing]
        if unexpected:
            raise RuntimeError(f"Output directory contains unexpected pre-existing files: {unexpected}")
    root.mkdir(parents=True, exist_ok=True)
    (root / "chunks").mkdir(parents=True, exist_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="artifacts/silma-letter-card")
    parser.add_argument("--references-dir", default="artifacts/silma-letter-card/references")
    parser.add_argument("--dry-run", action="store_true", help="Synthetic media plumbing test; never downloads or invokes SILMA.")
    parser.add_argument(
        "--episode-manifest",
        required=True,
        help="Owner-approved eight-chunk JSON script manifest.",
    )
    parser.add_argument(
        "--only-chunk-id",
        help="Generate exactly one existing chunk without replacing the others.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="Explicit deterministic SILMA seed; requires --only-chunk-id.",
    )
    args = parser.parse_args()

    global CHUNKS, EPISODE_ID, SCRIPT_MANIFEST_SHA256
    script_path = Path(args.episode_manifest).resolve(strict=True)
    script_data = json.loads(script_path.read_text(encoding="utf-8"))
    if script_data.get("schemaVersion") != 1 or script_data.get("status") != "approved-script":
        raise RuntimeError("Script manifest must have schemaVersion=1 and status=approved-script")
    if script_data.get("episodeId") != "letter-alif":
        raise RuntimeError("Expected letter-alif episode manifest")
    chunks = script_data.get("chunks")
    if not isinstance(chunks, list) or len(chunks) != 8:
        raise RuntimeError("Episode manifest must contain exactly eight chunks")
    ids: set[str] = set()
    expected_speakers = ["pistachio", "hazelnut"] * 4
    for index, item in enumerate(chunks):
        if not isinstance(item, dict) or item.get("speakerId") != expected_speakers[index]:
            raise RuntimeError(f"Unexpected speaker or order in chunk {index + 1}")
        chunk_id = item.get("chunkId")
        source_text = item.get("text")
        if not isinstance(chunk_id, str) or not chunk_id.strip() or chunk_id in ids:
            raise RuntimeError(f"Invalid or duplicate chunkId in chunk {index + 1}")
        if not isinstance(source_text, str) or not source_text.strip():
            raise RuntimeError(f"Missing Arabic source text in chunk {index + 1}")
        ids.add(chunk_id)
    if args.seed is not None and args.only_chunk_id is None:
        raise RuntimeError("--seed requires --only-chunk-id")
    if args.only_chunk_id is not None and args.seed is None:
        raise RuntimeError("--only-chunk-id requires an explicit --seed")
    if args.seed is not None and not 0 <= args.seed <= 2**31 - 1:
        raise RuntimeError("--seed must be between 0 and 2147483647")
    select_chunks(chunks, args.only_chunk_id)
    if args.dry_run and args.only_chunk_id is not None:
        raise RuntimeError("--dry-run cannot be combined with --only-chunk-id")

    CHUNKS = chunks
    EPISODE_ID = script_data["episodeId"]
    SCRIPT_MANIFEST_SHA256 = sha256_file(script_path)
    root = Path(args.output_dir).resolve()
    if args.dry_run and root.exists() and any(root.iterdir()):
        raise RuntimeError(f"Dry-run output directory must be new and empty: {root}")
    if not args.dry_run:
        prepare_output_dir(root)
    else:
        root.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    if args.dry_run:
        return run_dry(root, started)
    return run_real(
        root,
        Path(args.references_dir).resolve(),
        started,
        args.only_chunk_id,
        args.seed,
    )


if __name__ == "__main__":
    raise SystemExit(main())

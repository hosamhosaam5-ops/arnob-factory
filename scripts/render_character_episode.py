#!/usr/bin/env python3
"""Code-generated SVG characters and garden, driven by the eight approved WAV chunks."""
from __future__ import annotations

import argparse
import array
import csv
import hashlib
import json
import math
import statistics
import subprocess
import time
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, features

WIDTH, HEIGHT, FPS = 1280, 720, 24
PALETTE = {
    "cream": "#FFF5E6",
    "honey": "#F2C47D",
    "apricot": "#E89A64",
    "clay": "#C96E56",
    "cocoa": "#8E5E49",
    "sage": "#82A88B",
    "forest": "#3D6650",
    "sky": "#90BBC1",
}
TRIM_FILTER = (
    "areverse,silenceremove=start_periods=1:start_duration=0.02:"
    "start_threshold=-50dB:start_silence=0:detection=rms,areverse,"
    "silenceremove=stop_periods=-1:stop_duration=0.3:"
    "stop_threshold=-50dB:stop_silence=0.15:detection=rms"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def media(command: list[str], timeout: int = 180) -> None:
    try:
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       text=True, timeout=timeout)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc))[-5000:]
        raise RuntimeError("Command failed: " + " ".join(command[:3]) + "\n" + detail) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Command timed out: " + " ".join(command[:3])) from exc


def probe_duration(path: Path) -> float:
    raw = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ], text=True).strip()
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise RuntimeError(f"Invalid media duration for {path.name}: {raw}")
    return value


def load_sources(input_dir: Path) -> tuple[dict, list[dict], list[Path]]:
    manifest_path = input_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "complete" or manifest.get("dryRun") is not False:
        raise RuntimeError("Source manifest is not a completed non-dry-run episode")
    if manifest.get("episodeId") != "episode-pilot-1":
        raise RuntimeError("Unexpected episode identifier")
    chunks = manifest.get("completedChunks", [])
    if len(chunks) != 8:
        raise RuntimeError(f"Expected exactly 8 approved chunks, received {len(chunks)}")
    sources: list[Path] = []
    expected_speakers = ["pistachio", "hazelnut"] * 4
    expected_audio_names = {Path(str(item.get("wav", {}).get("path", ""))).name for item in chunks}
    actual_audio_names = {item.name for item in (input_dir / "chunks").iterdir() if item.is_file()}
    if actual_audio_names != expected_audio_names:
        raise RuntimeError("Audio input directory must contain only the eight manifest-approved WAV files")
    if any(Path(name).suffix.lower() != ".wav" for name in expected_audio_names):
        raise RuntimeError("Only WAV assets are permitted in the input set")
    for index, item in enumerate(chunks):
        if item.get("speakerId") != expected_speakers[index]:
            raise RuntimeError(f"Unexpected speaker order in {item.get('chunkId')}")
        wav_meta = item.get("wav") or {}
        rel = Path(str(wav_meta.get("path", "")))
        if rel.is_absolute() or rel.parent != Path("chunks") or rel.name in ("", ".", ".."):
            raise RuntimeError(f"Unsafe source WAV path: {rel}")
        source = input_dir / rel
        if not source.is_file() or not wav_meta.get("sha256"):
            raise RuntimeError(f"Missing approved WAV: {rel}")
        if source.stat().st_size != int(wav_meta.get("sizeBytes", -1)):
            raise RuntimeError(f"Source WAV size mismatch: {rel.name}")
        if sha256(source) != wav_meta["sha256"]:
            raise RuntimeError(f"Source WAV SHA-256 mismatch: {rel.name}")
        sources.append(source)
    return manifest, chunks, sources


def read_mono_pcm(path: Path) -> tuple[array.array, int]:
    with wave.open(str(path), "rb") as src:
        channels = src.getnchannels()
        width = src.getsampwidth()
        rate = src.getframerate()
        frames = src.getnframes()
        if channels != 1 or width != 2 or rate != 24000:
            raise RuntimeError(f"Unexpected processed WAV format: {path.name}")
        samples = array.array("h")
        samples.frombytes(src.readframes(frames))
        if samples.itemsize != 2:
            raise RuntimeError("Unexpected host integer width while reading PCM")
        if __import__("sys").byteorder != "little":
            samples.byteswap()
    return samples, rate


def rms_envelope(path: Path, fps: int = FPS) -> tuple[list[float], float]:
    samples, sample_rate = read_mono_pcm(path)
    per_frame = max(1, round(sample_rate / fps))
    values: list[float] = []
    for start in range(0, len(samples), per_frame):
        block = samples[start:start + per_frame]
        if not block:
            break
        square_sum = sum(int(value) * int(value) for value in block)
        values.append(math.sqrt(square_sum / len(block)) / 32768.0)
    if not values:
        raise RuntimeError(f"No audio samples in {path.name}")
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)]
    scale = max(p95, 0.015)
    normalized = [min(1.0, value / scale) for value in values]
    return normalized, scale


def make_audio_timeline(input_dir: Path, output_dir: Path, manifest: dict,
                        chunks: list[dict], sources: list[Path]) -> tuple[Path, list[dict], float, list[dict]]:
    derived_dir = output_dir / "trimmed-chunks"
    derived_dir.mkdir(parents=True, exist_ok=True)
    clip_rows: list[dict] = []
    trimmed_paths: list[Path] = []
    for item, source in zip(chunks, sources):
        target = derived_dir / (source.stem + "-trimmed.wav")
        media([
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-i", str(source), "-map", "0:a:0", "-af", TRIM_FILTER,
            "-map_metadata", "-1", "-ar", "24000", "-ac", "1",
            "-c:a", "pcm_s16le", str(target)
        ])
        original_duration = probe_duration(source)
        processed_duration = probe_duration(target)
        expected_sha = item["wav"]["sha256"]
        if sha256(source) != expected_sha:
            raise RuntimeError(f"Original WAV changed during processing: {source.name}")
        if processed_duration <= 0 or processed_duration > original_duration + 0.03:
            raise RuntimeError(f"Trimmed duration invalid: {source.name}")
        envelope, scale = rms_envelope(target)
        clip_rows.append({
            "chunkId": item["chunkId"],
            "speakerId": item["speakerId"],
            "sourceWav": item["wav"]["path"],
            "sourceWavSha256": expected_sha,
            "sourceWavPreserved": True,
            "processedWav": target.relative_to(output_dir).as_posix(),
            "processedWavSha256": sha256(target),
            "sourceDurationSeconds": round(original_duration, 6),
            "processedDurationSeconds": round(processed_duration, 6),
            "rmsP95Scale": round(scale, 7),
            "rmsFrames": len(envelope),
            "envelope": envelope,
        })
        trimmed_paths.append(target)
    pause = output_dir / "pause-0.35s.wav"
    media([
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "0.35",
        "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(pause)
    ])
    if abs(probe_duration(pause) - 0.35) > 0.001:
        raise RuntimeError("Could not create the fixed 0.35-second pause")
    concat = output_dir / "episode-audio-concat.txt"
    entries: list[str] = []
    for i, path in enumerate(trimmed_paths):
        entries.append("file '" + path.resolve().as_posix() + "'")
        if i < len(trimmed_paths) - 1:
            entries.append("file '" + pause.resolve().as_posix() + "'")
    concat.write_text("\n".join(entries) + "\n", encoding="utf-8")
    combined = output_dir / "episode-audio-trimmed.wav"
    media([
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat),
        "-map", "0:a:0", "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(combined)
    ])
    duration = probe_duration(combined)
    expected = sum(row["processedDurationSeconds"] for row in clip_rows) + 0.35 * 7
    if abs(duration - expected) > 0.03:
        raise RuntimeError("Combined voice track duration differs from trimmed clips plus pauses")
    # Recheck every original input after all derivatives were generated.
    for item, source in zip(chunks, sources):
        if sha256(source) != item["wav"]["sha256"]:
            raise RuntimeError(f"Original WAV fingerprint changed: {source.name}")
    for row in clip_rows:
        row.pop("envelope")
    envelope_rows = []
    for target in trimmed_paths:
        envelope, scale = rms_envelope(target)
        envelope_rows.append({"values": envelope, "p95Scale": scale})
    return combined, clip_rows, duration, envelope_rows


def esc(value: str) -> str:
    return (value.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def wrap_arabic(text: str, max_chars: int = 43) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        if current and len(candidate) > max_chars:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines[:3]


def mouth_shape(level: float) -> str:
    if level < 0.16:
        return "closed"
    if level < 0.39:
        return "half"
    if level < 0.72:
        return "open"
    return "round"


def blink_amount(seconds: float, phase: float) -> float:
    period = 5.3
    at = (seconds + phase) % period
    center = 4.85
    distance = abs(at - center)
    return max(0.0, 1.0 - distance / 0.19)


def cat_svg(x: float, base_y: float, level: float, seconds: float,
            active: bool, camera_shift: float, phase: float) -> str:
    bounce = (6.0 + 5.5 * level) * math.sin(seconds * 12.0) if active else 1.2 * math.sin(seconds * 1.5)
    breath = 1.0 + 0.012 * math.sin(seconds * 2.1 + phase)
    ear = 4.5 * math.sin(seconds * 3.2 + phase)
    tail = 13.0 * math.sin(seconds * 2.4 + phase)
    blink = blink_amount(seconds, phase)
    mouth = mouth_shape(level) if active else "half"
    eye_h = max(2.5, 18.0 * (1.0 - blink * 0.94))
    return f'''<g transform="translate({x + camera_shift:.2f},{base_y - bounce:.2f}) scale({breath:.4f})">
      <ellipse cx="0" cy="4" rx="150" ry="22" fill="#5A4835" opacity=".16" filter="url(#softShadow)"/>
      <path d="M-90,-75 C-160,-130 -179,-62 -145,-23 C-126,0 -103,-10 -91,-26" fill="none" stroke="#9A623D" stroke-width="25" stroke-linecap="round" transform="rotate({tail:.2f} -96 -50)"/>
      <path d="M-82,-80 C-128,-112 -143,-57 -119,-38" fill="none" stroke="#F2C47D" stroke-width="9" stroke-linecap="round" transform="rotate({tail:.2f} -96 -50)"/>
      <ellipse cx="0" cy="-4" rx="88" ry="92" fill="#C9894F" stroke="#895A39" stroke-width="7"/>
      <ellipse cx="0" cy="18" rx="53" ry="61" fill="#F4DDBA" opacity=".94"/>
      <path d="M-56,35 Q-83,65 -56,86 Q-31,97 -22,72 M56,35 Q83,65 56,86 Q31,97 22,72" fill="#D79A56" stroke="#895A39" stroke-width="7" stroke-linecap="round"/>
      <ellipse cx="-65" cy="70" rx="25" ry="13" fill="#F4DDBA"/>
      <ellipse cx="65" cy="70" rx="25" ry="13" fill="#F4DDBA"/>
      <ellipse cx="0" cy="-65" rx="91" ry="103" fill="#D79A56" stroke="#895A39" stroke-width="7"/>
      <ellipse cx="0" cy="-12" rx="57" ry="61" fill="#F4DDBA" opacity=".96"/>
      <path d="M-69,-133 Q-113,-207 -48,-189 Q-28,-181 -20,-139 Z" fill="#D79A56" stroke="#895A39" stroke-width="7" transform="rotate({-ear:.2f} -55 -140)"/>
      <path d="M-62,-151 Q-82,-184 -52,-176" fill="none" stroke="#BD765C" stroke-width="10" stroke-linecap="round"/>
      <path d="M69,-133 Q113,-207 48,-189 Q28,-181 20,-139 Z" fill="#D79A56" stroke="#895A39" stroke-width="7" transform="rotate({ear:.2f} 55 -140)"/>
      <path d="M62,-151 Q82,-184 52,-176" fill="none" stroke="#BD765C" stroke-width="10" stroke-linecap="round"/>
      <path d="M-42,-151 Q-25,-166 -8,-151" fill="none" stroke="#895A39" stroke-width="8" stroke-linecap="round"/>
      <path d="M8,-151 Q25,-166 42,-151" fill="none" stroke="#895A39" stroke-width="8" stroke-linecap="round"/>
      <ellipse cx="-34" cy="-113" rx="25" ry="{eye_h:.2f}" fill="#FFF9EC"/>
      <ellipse cx="34" cy="-113" rx="25" ry="{eye_h:.2f}" fill="#FFF9EC"/>
      <ellipse cx="-30" cy="-111" rx="11" ry="{max(1.5, eye_h*.62):.2f}" fill="#29483B"/>
      <ellipse cx="38" cy="-111" rx="11" ry="{max(1.5, eye_h*.62):.2f}" fill="#29483B"/>
      <circle cx="-34" cy="-120" r="7" fill="#FFFFFF"/><circle cx="-24" cy="-105" r="3.5" fill="#FFFFFF"/>
      <circle cx="34" cy="-120" r="7" fill="#FFFFFF"/><circle cx="44" cy="-105" r="3.5" fill="#FFFFFF"/>
      <ellipse cx="-62" cy="-74" rx="20" ry="11" fill="#D98278" opacity=".33"/><ellipse cx="62" cy="-74" rx="20" ry="11" fill="#D98278" opacity=".33"/>
      <ellipse cx="0" cy="-77" rx="11" ry="8" fill="#925B46"/>
      <path d="M0,-70 Q-2,-58 -15,-57 M0,-70 Q2,-58 15,-57" fill="none" stroke="#895A39" stroke-width="4" stroke-linecap="round"/>
      {mouth_markup(mouth, 0, -47)}
      <path d="M-18,-206 Q0,-220 18,-206" fill="none" stroke="#82A88B" stroke-width="9" stroke-linecap="round" opacity=".92"/>
      <path d="M-14,-206 Q-4,-226 12,-212 Q0,-196 -14,-206Z" fill="#82A88B" stroke="#3D6650" stroke-width="3"/>
      <path d="M-49,-5 Q0,18 49,-5" fill="none" stroke="#F8DDB5" stroke-width="6" opacity=".55"/>
    </g>'''


def mouse_svg(x: float, base_y: float, level: float, seconds: float,
              active: bool, camera_shift: float, phase: float) -> str:
    bounce = (5.0 + 5.0 * level) * math.sin(seconds * 12.7 + .4) if active else 1.0 * math.sin(seconds * 1.4)
    breath = 1.0 + 0.014 * math.sin(seconds * 2.0 + phase)
    ear = 5.5 * math.sin(seconds * 2.8 + phase)
    tail = 12.0 * math.sin(seconds * 2.1 + phase)
    blink = blink_amount(seconds, phase)
    mouth = mouth_shape(level) if active else "half"
    eye_h = max(2.0, 14.0 * (1.0 - blink * 0.94))
    return f'''<g transform="translate({x + camera_shift:.2f},{base_y - bounce:.2f}) scale({breath:.4f})">
      <ellipse cx="0" cy="4" rx="100" ry="16" fill="#5A4835" opacity=".16" filter="url(#softShadow)"/>
      <ellipse cx="0" cy="5" rx="55" ry="46" fill="#A8795E" stroke="#704B3D" stroke-width="5"/>
      <ellipse cx="0" cy="15" rx="34" ry="28" fill="#F3DDC5"/>
      <path d="M-39 22 q-15 15 -5 27 q14 7 21 -5 M39 22 q15 15 5 27 q-14 7 -21 -5" fill="none" stroke="#A8795E" stroke-width="14" stroke-linecap="round"/>
      <path d="M63,-39 C132,-45 148,-5 113,14 C88,29 69,17 74,2" fill="none" stroke="#A96D57" stroke-width="9" stroke-linecap="round" transform="rotate({tail:.2f} 73 -22)"/>
      <ellipse cx="0" cy="-45" rx="65" ry="78" fill="#A8795E" stroke="#704B3D" stroke-width="6"/>
      <path d="M-48,-92 Q-94,-159 -53,-169 Q-13,-169 -15,-110Z" fill="#A8795E" stroke="#704B3D" stroke-width="6" transform="rotate({-ear:.2f} -50 -117)"/>
      <path d="M-46,-116 Q-72,-151 -49,-151 Q-25,-149 -27,-115Z" fill="#D8897C" opacity=".92"/>
      <path d="M48,-92 Q94,-159 53,-169 Q13,-169 15,-110Z" fill="#A8795E" stroke="#704B3D" stroke-width="6" transform="rotate({ear:.2f} 50 -117)"/>
      <path d="M46,-116 Q72,-151 49,-151 Q25,-149 27,-115Z" fill="#D8897C" opacity=".92"/>
      <ellipse cx="0" cy="-28" rx="48" ry="39" fill="#F3DDC5"/>
      <ellipse cx="-25" cy="-78" rx="18" ry="{eye_h:.2f}" fill="#FFF9EC"/>
      <ellipse cx="25" cy="-78" rx="18" ry="{eye_h:.2f}" fill="#FFF9EC"/>
      <ellipse cx="-22" cy="-76" rx="8" ry="{max(1.5, eye_h*.65):.2f}" fill="#29483B"/>
      <ellipse cx="28" cy="-76" rx="8" ry="{max(1.5, eye_h*.65):.2f}" fill="#29483B"/>
      <circle cx="-26" cy="-84" r="5.5" fill="#FFFFFF"/><circle cx="-18" cy="-72" r="2.7" fill="#FFFFFF"/>
      <circle cx="24" cy="-84" r="5.5" fill="#FFFFFF"/><circle cx="32" cy="-72" r="2.7" fill="#FFFFFF"/>
      <path d="M-43,-106 Q-26,-119 -10,-105" fill="none" stroke="#704B3D" stroke-width="6" stroke-linecap="round"/>
      <path d="M10,-105 Q26,-119 43,-106" fill="none" stroke="#704B3D" stroke-width="6" stroke-linecap="round"/>
      <ellipse cx="-46" cy="-45" rx="14" ry="9" fill="#D98278" opacity=".34"/><ellipse cx="46" cy="-45" rx="14" ry="9" fill="#D98278" opacity=".34"/>
      <ellipse cx="0" cy="-39" rx="9" ry="6" fill="#B96D68"/>
      <path d="M-7,-31 Q-27,-30 -37,-37 M7,-31 Q27,-30 37,-37" fill="none" stroke="#704B3D" stroke-width="3" stroke-linecap="round"/>
      {mouth_markup(mouth, 0, -20)}
      <path d="M-20,-3 Q0,12 20,-3" fill="none" stroke="#F8E4D0" stroke-width="5" opacity=".55"/>
    </g>'''


def mouth_markup(shape: str, x: float, y: float) -> str:
    if shape == "closed":
        return f'<path d="M{x-13},{y} Q{x},{y+9} {x+13},{y}" fill="none" stroke="#70443B" stroke-width="4" stroke-linecap="round"/>'
    if shape == "half":
        return f'<path d="M{x-16},{y} Q{x},{y+18} {x+16},{y} Q{x},{y+7} {x-16},{y}" fill="#9D4D49" stroke="#70443B" stroke-width="3"/>'
    if shape == "open":
        return f'<ellipse cx="{x}" cy="{y+5}" rx="12" ry="19" fill="#743C3A" stroke="#70443B" stroke-width="3"/><path d="M{x-7},{y+14} Q{x},{y+19} {x+7},{y+14}" stroke="#E89A8A" stroke-width="4" fill="none"/>'
    return f'<ellipse cx="{x}" cy="{y+4}" rx="13" ry="17" fill="#743C3A" stroke="#70443B" stroke-width="3"/>'


def svg_frame(scene: dict, scene_index: int, global_time: float,
              normalized_envelope: float, width: int = WIDTH, height: int = HEIGHT,
              include_text: bool = True) -> str:
    camera_scale = 1.0 + 0.018 * math.sin(global_time * 2 * math.pi / 31.0)
    camera_x = 7.0 * math.sin(global_time * 2 * math.pi / 47.0)
    wind = math.sin(global_time * 1.7 + scene_index * 0.6)
    level = max(0.0, min(1.0, normalized_envelope))
    speaker = scene["speakerId"]
    cat = cat_svg(395, 490, level if speaker == "pistachio" else 0.0,
                  global_time, speaker == "pistachio", camera_x * .30, .2)
    mouse = mouse_svg(900, 485, level if speaker == "hazelnut" else 0.0,
                      global_time, speaker == "hazelnut", camera_x * .30, 1.9)
    lines = wrap_arabic(scene["text"])
    caption_text = "".join(
        f'<text x="640" y="{600 + (i-(len(lines)-1)/2)*43:.1f}" text-anchor="middle" '
        f'direction="rtl" unicode-bidi="plaintext" font-family="Noto Sans Arabic, sans-serif" '
        f'font-size="31" font-weight="600" fill="#29483B">{esc(line)}</text>'
        for i, line in enumerate(lines)
    ) if include_text else ""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 1280 720">
    <defs>
      <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#94C6CA"/><stop offset=".52" stop-color="#D8E2C3"/><stop offset="1" stop-color="#F7DDAA"/></linearGradient>
      <linearGradient id="hillFar" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#A7C5A0"/><stop offset="1" stop-color="#8EB49B"/></linearGradient>
      <linearGradient id="hillNear" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#7EA885"/><stop offset="1" stop-color="#527C63"/></linearGradient>
      <radialGradient id="sunGlow"><stop stop-color="#FFF4C9" stop-opacity=".95"/><stop offset="1" stop-color="#F5CC7D" stop-opacity="0"/></radialGradient>
      <filter id="softShadow" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="7"/></filter>
      <filter id="goldGlow" x="-80%" y="-80%" width="260%" height="260%"><feGaussianBlur stdDeviation="9"/></filter>
    </defs>
    <rect width="1280" height="720" fill="url(#sky)"/>
    <g transform="translate({camera_x*.04:.2f} 0) scale({camera_scale:.4f} 1)" opacity=".95">
      <circle cx="1030" cy="142" r="150" fill="url(#sunGlow)"/>
      <circle cx="1030" cy="142" r="54" fill="#F7D48D" opacity=".9"/>
      <path d="M-50 365 Q160 170 355 340 T780 314 T1330 330 L1330 560 L-50 560Z" fill="url(#hillFar)"/>
      <path d="M-50 430 Q210 300 420 420 T850 396 T1330 405 L1330 600 L-50 600Z" fill="#9BBE9C" opacity=".8"/>
    </g>
    <g transform="translate({camera_x*.14:.2f} {wind*2:.2f})">
      <g opacity=".8">
        <path d="M120 440 Q104 302 120 238" stroke="#806148" stroke-width="20" fill="none" stroke-linecap="round"/>
        <circle cx="92" cy="240" r="68" fill="#739D79"/><circle cx="155" cy="220" r="76" fill="#86AC83"/><circle cx="118" cy="180" r="67" fill="#9ABA8F"/>
        <path d="M1170 448 Q1150 320 1167 245" stroke="#806148" stroke-width="23" fill="none" stroke-linecap="round"/>
        <circle cx="1122" cy="250" r="70" fill="#6F9B76"/><circle cx="1192" cy="225" r="78" fill="#85AD82"/><circle cx="1170" cy="182" r="64" fill="#9ABA8F"/>
      </g>
      <path d="M0 475 Q180 420 360 480 T720 478 T1280 465 L1280 720 L0 720Z" fill="url(#hillNear)"/>
    </g>
    <g opacity=".18" stroke="#FFE6AF" stroke-width="30" stroke-linecap="round">
      <path d="M1000 170 L670 570"/><path d="M1080 185 L880 590"/><path d="M930 150 L480 500"/>
    </g>
    <g transform="translate({camera_x*.32:.2f} {wind*5:.2f})">
      <path d="M0 585 Q130 545 260 586 T520 590 T780 582 T1040 588 T1320 575 L1320 720 L0 720Z" fill="#6B966F"/>
      {''.join(f'<path d="M{x} 700 q{-10+wind*4:.1f} -45 {-5+wind*7:.1f} -88 q17 24 13 58 q21 -33 36 -52 q-4 37 -24 68" fill="none" stroke="{col}" stroke-width="9" stroke-linecap="round" opacity=".9"/>' for x,col in [(40,'#A4BE80'),(200,'#D4B879'),(1060,'#9DBA7A'),(1220,'#C8A06C')])}
      <g fill="#F4D18C" opacity=".88"><circle cx="235" cy="622" r="7"/><circle cx="1010" cy="654" r="6"/><circle cx="1110" cy="610" r="5"/></g>
    </g>
    <g opacity=".75">
      <circle cx="245" cy="190" r="4" fill="#FFF6D8"/><circle cx="340" cy="260" r="5" fill="#FFE8B3"/>
      <circle cx="805" cy="205" r="4" fill="#FFF6D8"/><circle cx="920" cy="295" r="5" fill="#FFE8B3"/>
      <path d="M235 170 q-11 -14 -3 -23 q12 7 3 23 M1060 320 q-11 -14 -3 -23 q12 7 3 23" fill="#E9B56D"/>
      <circle cx="{330 + 14*math.sin(global_time*.8):.1f}" cy="{330 + 9*math.cos(global_time*.9):.1f}" r="3.5" fill="#FFF4BE"/>
      <circle cx="{930 + 18*math.sin(global_time*.65+1):.1f}" cy="{250 + 12*math.cos(global_time*.7):.1f}" r="4" fill="#FFF4BE"/>
    </g>
    <g transform="translate(0 0) scale({camera_scale:.4f} 1) translate({(1-camera_scale)*640+camera_x*.23:.2f} 0)">
      {cat}{mouse}
    </g>
    <g>
      <rect x="64" y="28" width="1152" height="68" rx="26" fill="#FFF8EA" fill-opacity=".79" stroke="#FFFFFF" stroke-opacity=".78" stroke-width="2"/>
      {('<text x="640" y="73" text-anchor="middle" direction="rtl" unicode-bidi="plaintext" font-family="Noto Naskh Arabic, serif" font-size="37" font-weight="700" fill="#3D6650">حِكَايَةُ الْبَذْرَةِ الصَّغِيرَةِ</text>' if include_text else '')}
      <rect x="74" y="558" width="1132" height="128" rx="27" fill="#FFF8EA" fill-opacity=".96" stroke="#FFFFFF" stroke-width="3"/>
      {caption_text}
    </g>
    </svg>'''


def draw_arabic_overlay(scene: dict, png_path: Path) -> None:
    """Use RAQM shaping for subtitle glyphs after vector rasterization."""
    if not features.check("raqm"):
        raise RuntimeError("Pillow RAQM is required for correct Arabic shaping")
    font_paths = (
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
    )
    selected = next((candidate for candidate in font_paths if Path(candidate).is_file()), None)
    if not selected:
        raise RuntimeError("Noto Arabic font is required for subtitle rendering")
    title_font = ImageFont.truetype(selected, 37, layout_engine=ImageFont.Layout.RAQM)
    caption_font = ImageFont.truetype(selected, 31, layout_engine=ImageFont.Layout.RAQM)
    image = Image.open(png_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    draw.text((640, 70), "حِكَايَةُ الْبَذْرَةِ الصَّغِيرَةِ", font=title_font,
              fill="#3D6650", anchor="mm", direction="rtl", language="ar")
    lines = wrap_arabic(scene["text"])
    start_y = 600 - ((len(lines) - 1) * 43) / 2
    for index, line in enumerate(lines):
        draw.text((640, start_y + index * 43), line, font=caption_font,
                  fill="#29483B", anchor="mm", direction="rtl", language="ar")
    image.save(png_path, format="PNG", optimize=False)


def rasterize(svg_path: Path, png_path: Path) -> None:
    try:
        subprocess.run(["rsvg-convert", "--width", str(WIDTH), "--height", str(HEIGHT),
                        "--output", str(png_path), str(svg_path)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    except FileNotFoundError:
        # Local preview-only fallback; the CI render installs librsvg2-bin.
        try:
            import cairosvg
            cairosvg.svg2png(url=str(svg_path), write_to=str(png_path),
                             output_width=WIDTH, output_height=HEIGHT)
        except Exception as exc:
            raise RuntimeError("Install librsvg2-bin or CairoSVG to rasterize SVG") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("SVG rasterization failed: " + (exc.stderr or b"").decode("utf-8", "replace")[-3000:]) from exc


def render(args) -> dict:
    started = time.perf_counter()
    input_dir, output_dir = Path(args.input_dir), Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest, chunks, sources = load_sources(input_dir)
    voice_track, clip_rows, audio_duration, envelopes = make_audio_timeline(
        input_dir, output_dir, manifest, chunks, sources
    )
    scenes: list[dict] = []
    cursor = 0.0
    for index, (item, row) in enumerate(zip(chunks, clip_rows)):
        duration = row["processedDurationSeconds"]
        scenes.append({
            "index": index + 1,
            "chunkId": item["chunkId"],
            "speakerId": item["speakerId"],
            "speakerArabic": item["speakerArabic"],
            "text": item["text"],
            "startSeconds": round(cursor, 6),
            "durationSeconds": duration,
            "endSeconds": round(cursor + duration, 6),
        })
        cursor += duration
        if index < len(chunks) - 1:
            cursor += 0.35
    if abs(cursor - audio_duration) > 0.03:
        raise RuntimeError("Scene timeline differs from the prepared WAV timeline")

    frames_dir = output_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    preview_dir = output_dir / "preview-scenes"
    preview_dir.mkdir(parents=True, exist_ok=True)
    total_frames = math.ceil(audio_duration * FPS)
    frame_rows: list[tuple[int, int, float, float]] = []
    boundaries = [scene["startSeconds"] for scene in scenes]
    selected_scene_ids = {0, 3, 7}
    selected_frame_numbers: set[int] = set()
    for scene_index in selected_scene_ids:
        center = (scenes[scene_index]["startSeconds"] + scenes[scene_index]["endSeconds"]) / 2
        selected_frame_numbers.add(min(total_frames, max(1, round(center * FPS) + 1)))

    all_start = time.perf_counter()
    frame = 0
    for frame in range(1, total_frames + 1):
        global_time = (frame - 1) / FPS
        scene_index = max(i for i, value in enumerate(boundaries) if value <= global_time)
        scene = scenes[scene_index]
        local_time = global_time - scene["startSeconds"]
        env_values = envelopes[scene_index]["values"]
        if local_time < 0 or local_time >= scene["durationSeconds"]:
            level = 0.0  # the inter-speaker pause is quiet and keeps the previous caption
        else:
            env_index = min(len(env_values) - 1, max(0, int(local_time * FPS)))
            level = env_values[env_index]
        svg_path = frames_dir / f"frame_{frame:06d}.svg"
        png_path = frames_dir / f"frame_{frame:06d}.png"
        svg_path.write_text(svg_frame(scene, scene_index, global_time, level, include_text=False), encoding="utf-8")
        frame_start = time.perf_counter()
        rasterize(svg_path, png_path)
        draw_arabic_overlay(scene, png_path)
        elapsed = time.perf_counter() - frame_start
        frame_rows.append((frame, scene_index + 1, global_time, elapsed))
        if frame in selected_frame_numbers:
            preview_path = preview_dir / f"scene-{scene_index+1:02d}.png"
            preview_path.write_bytes(png_path.read_bytes())
        if frame % 100 == 0:
            print(f"rendered {frame}/{total_frames} frames", flush=True)
    raster_wall = time.perf_counter() - all_start
    if frame != total_frames:
        raise RuntimeError(f"Frame count mismatch: rendered {frame}, expected {total_frames}")
    for source, item in zip(sources, chunks):
        if sha256(source) != item["wav"]["sha256"]:
            raise RuntimeError(f"Source WAV changed after rendering: {source.name}")

    frame_csv = output_dir / "frame-times.csv"
    with frame_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["frame", "scene", "timestamp_seconds", "rasterize_seconds"])
        writer.writerows((f, s, f"{t:.6f}", f"{elapsed:.6f}") for f, s, t, elapsed in frame_rows)
    values = [row[3] for row in frame_rows]
    ordered = sorted(values)
    p95 = ordered[min(len(ordered)-1, math.ceil(len(ordered)*.95)-1)]
    mean_frame = statistics.mean(values)
    five_min_estimate = mean_frame * (5 * 60 * FPS) / 60

    raw_video = output_dir / "characters-video-raw.mp4"
    media([
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-framerate", str(FPS), "-start_number", "1",
        "-i", str(frames_dir / "frame_%06d.png"),
        "-frames:v", str(total_frames),
        "-vf", "eq=contrast=1.015:saturation=1.035:brightness=0.005,"
              "colorbalance=rs=0.010:gs=0.005:bs=-0.006,vignette=PI/5,noise=alls=1:allf=t+u",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-threads", "2", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-movflags", "+faststart", str(raw_video)
    ], timeout=300)
    probe = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries",
        "stream=codec_name,width,height,r_frame_rate,nb_frames:format=duration,size",
        "-of", "json", str(raw_video)
    ], text=True))
    video_stream = next((x for x in probe.get("streams", []) if x.get("codec_name") == "h264"), None)
    if not video_stream or video_stream.get("width") != WIDTH or video_stream.get("height") != HEIGHT:
        raise RuntimeError("Encoded picture stream is not H.264 1280x720")
    if video_stream.get("r_frame_rate") != f"{FPS}/1":
        raise RuntimeError("Encoded picture stream does not have exactly 24 fps")

    preview_times = {}
    for scene_index in sorted(selected_scene_ids):
        scene = scenes[scene_index]
        preview_times[f"scene-{scene_index+1:02d}"] = round(
            (scene["startSeconds"] + scene["endSeconds"]) / 2, 6
        )
    summary = {
        "mean": round(mean_frame, 6),
        "median": round(statistics.median(values), 6),
        "p95": round(p95, 6),
        "max": round(max(values), 6),
        "min": round(min(values), 6),
        "sampleCount": len(values),
    }
    report = {
        "status": "rendered-awaiting-qa",
        "versionName": "نسخة شخصيات v1",
        "sourceRunId": 45821000995,
        "sourceManifestSha256": sha256(input_dir / "manifest.json"),
        "sourceWavCount": len(sources),
        "sourceWavSha256": [sha256(path) for path in sources],
        "sourceWavFilesModified": False,
        "newTtsOrModelInference": False,
        "visualAssets": "original-code-generated-svg-only",
        "rasterizer": "librsvg rsvg-convert",
        "resolution": {"width": WIDTH, "height": HEIGHT},
        "fps": FPS,
        "framesWritten": len(frame_rows),
        "audioDurationSeconds": round(audio_duration, 6),
        "sceneCount": len(scenes),
        "scenes": scenes,
        "audioProcessing": {
            "trimFilter": TRIM_FILTER,
            "pauseBetweenChunksSeconds": 0.35,
            "voiceTrackPath": voice_track.name,
            "voiceTrackSha256": sha256(voice_track),
            "sourceWavFingerprintsRechecked": True,
            "perChunk": clip_rows,
            "mouthEnvelope": "24 fps RMS per frame from derived PCM WAV clips; normalized using each clip's 95th percentile",
            "mouthStates": {"closed": "level < 0.16", "half": "0.16 to <0.39", "open": "0.39 to <0.72", "round_O": "level >= 0.72"},
        },
        "frameRasterizeSeconds": summary,
        "rasterizeWallSeconds": round(raster_wall, 3),
        "totalRendererWallSeconds": round(time.perf_counter() - started, 3),
        "fiveMinuteEstimate": {
            "frameCount": 5 * 60 * FPS,
            "minutesFromMeasuredRasterizationMean": round(five_min_estimate, 3),
            "measured": False,
            "method": "linear extrapolation from all measured rasterization frames; excludes FFmpeg encode and QA",
        },
        "postProcessing": "subtle noise grain, vignette, warm contrast/saturation balance",
        "picture": {
            "file": raw_video.name,
            "sha256": sha256(raw_video),
            "sizeBytes": raw_video.stat().st_size,
            "durationSeconds": float(probe["format"]["duration"]),
            "probe": probe,
        },
        "previewFrames": preview_times,
    }
    (output_dir / "render-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ["status", "framesWritten", "frameRasterizeSeconds", "rasterizeWallSeconds", "fiveMinuteEstimate", "picture"]}, ensure_ascii=False, indent=2), flush=True)
    return report


def write_preview_set(input_dir: Path, preview_dir: Path) -> None:
    """Write one deterministic, static SVG frame per scene for the PR comparison page."""
    manifest, chunks, sources = load_sources(input_dir)
    scratch = preview_dir / "_audio"
    scratch.mkdir(parents=True, exist_ok=True)
    _, clip_rows, _, envelopes = make_audio_timeline(input_dir, scratch, manifest, chunks, sources)
    preview_dir.mkdir(parents=True, exist_ok=True)
    cursor = 0.0
    for index, (item, row, envelope) in enumerate(zip(chunks, clip_rows, envelopes)):
        middle = len(envelope["values"]) // 2
        scene = {
            "index": index + 1,
            "chunkId": item["chunkId"],
            "speakerId": item["speakerId"],
            "speakerArabic": item["speakerArabic"],
            "text": item["text"],
            "startSeconds": cursor,
            "durationSeconds": row["processedDurationSeconds"],
            "endSeconds": cursor + row["processedDurationSeconds"],
        }
        global_time = cursor + row["processedDurationSeconds"] / 2
        svg = svg_frame(scene, index, global_time, envelope["values"][middle])
        target = preview_dir / f"scene-{index+1:02d}.svg"
        target.write_text(svg, encoding="utf-8")
        png_target = preview_dir / f"scene-{index+1:02d}.png"
        raster_svg = preview_dir / f".scene-{index+1:02d}-raster.svg"
        raster_svg.write_text(svg_frame(scene, index, global_time,
                                        envelope["values"][middle], include_text=False), encoding="utf-8")
        rasterize(raster_svg, png_target)
        draw_arabic_overlay(scene, png_target)
        raster_svg.unlink()
        cursor += row["processedDurationSeconds"] + (0.35 if index < len(chunks)-1 else 0.0)
    write_comparison_sheet(preview_dir, chunks)
    print(f"Wrote {len(chunks)} static SVG scene previews to {preview_dir}")


def write_comparison_sheet(preview_dir: Path, chunks: list[dict]) -> None:
    import re
    width, height, cell_w, cell_h = 2000, 735, 480, 270
    panels = []
    for index, item in enumerate(chunks):
        source = (preview_dir / f"scene-{index+1:02d}.svg").read_text(encoding="utf-8")
        prefix = f"scene{index+1}-"
        source = re.sub(r'id="([^"]+)"', lambda m: f'id="{prefix}{m.group(1)}"', source)
        source = re.sub(r'url\(#([^)]+)\)', lambda m: f'url(#{prefix}{m.group(1)})', source)
        inner = source[source.find(">") + 1:source.rfind("</svg>")]
        col, row = index % 4, index // 4
        x, y = 20 + col * 495, 52 + row * 342
        panels.append(
            f'<rect x="{x}" y="{y}" width="{cell_w}" height="{cell_h}" rx="18" fill="#FFF8EA" stroke="#D8C5A4" stroke-width="2"/>'
            f'<svg x="{x}" y="{y}" width="{cell_w}" height="{cell_h}" viewBox="0 0 1280 720">{inner}</svg>'
            f'<text x="{x+cell_w/2}" y="{y+cell_h+25}" text-anchor="middle" font-family="sans-serif" font-size="20" fill="#3D6650">المشهد {index+1} — {item["speakerArabic"]}</text>'
        )
    sheet = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        '<defs><linearGradient id="sheet-bg" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#EFF3E5"/><stop offset="1" stop-color="#E2E8D7"/></linearGradient></defs>'
        f'<rect width="{width}" height="{height}" fill="url(#sheet-bg)"/>'
        '<text x="1000" y="32" text-anchor="middle" font-family="sans-serif" font-size="25" font-weight="700" fill="#3D6650">مقارنة الإطارات الثابتة — ثمانية مشاهد</text>'
        + "".join(panels) + "</svg>"
    )
    (preview_dir / "scene-comparison.svg").write_text(sheet, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--preview-set", action="store_true")
    args = parser.parse_args()
    if args.preview_set:
        write_preview_set(Path(args.input_dir), Path(args.output_dir))
    else:
        render(args)


if __name__ == "__main__":
    main()

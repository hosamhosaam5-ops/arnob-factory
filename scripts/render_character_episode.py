#!/usr/bin/env python3
"""Code-generated SVG characters and garden, driven by the eight approved WAV chunks."""
from __future__ import annotations

import argparse
import array
import csv
import hashlib
import json
import base64
import re
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
PAUSE_SECONDS = 0.20
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
        "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", str(PAUSE_SECONDS),
        "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(pause)
    ])
    if abs(probe_duration(pause) - PAUSE_SECONDS) > 0.001:
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
    expected = sum(row["processedDurationSeconds"] for row in clip_rows) + PAUSE_SECONDS * (len(chunks) - 1)
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


def wrap_arabic(text: str, max_chars: int = 36, font=None,
                max_width: float = 980.0) -> list[str]:
    """Wrap Arabic words without splitting glyph clusters or exceeding the caption panel."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        too_wide = (font.getlength(candidate, direction="rtl", language="ar") > max_width
                    if font is not None else len(candidate) > max_chars)
        if current and too_wide:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def caption_baselines(line_count: int) -> list[float]:
    if not 1 <= line_count <= 3:
        raise ValueError("Arabic captions must fit within one to three lines")
    return [621.0 + (index - (line_count - 1) / 2) * 34.0
            for index in range(line_count)]


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
            active: bool, camera_shift: float, phase: float,
            expression: str = "excited") -> str:
    pulse = math.sin(seconds * (14.0 if active else 2.0) + phase)
    bounce = (5.0 + 8.0 * level) * pulse if active else 1.0 * math.sin(seconds * 1.4 + phase)
    squash_x = 1.0 + 0.075 * math.sin(seconds * 5.4 + phase) * (1.0 if active else 0.25)
    squash_y = 1.0 - 0.055 * math.sin(seconds * 5.4 + phase) * (1.0 if active else 0.25)
    ear = 6.0 * math.sin(seconds * 3.8 + phase)
    tail = 25.0 * math.sin(seconds * 2.9 + phase)
    head_tilt = 2.5 * math.sin(seconds * 2.5 + phase) if active else 7.0 * math.sin(seconds * 1.25 + phase)
    blink = blink_amount(seconds, phase)
    mouth = mouth_shape(level) if active else "closed"
    if not active:
        expression = "listening"
    brows = {
        "surprise": '<path d="M-51,-151 Q-29,-181 -7,-153 M7,-153 Q29,-181 51,-151"/>',
        "clever": '<path d="M-51,-151 Q-30,-165 -8,-145 M8,-158 Q31,-178 51,-155"/>',
        "listening": '<path d="M-51,-151 Q-30,-173 -8,-155 M8,-147 Q29,-166 51,-150"/>',
        "excited": '<path d="M-51,-151 Q-29,-180 -7,-157 M7,-157 Q29,-180 51,-151"/>',
    }.get(expression, '<path d="M-51,-151 Q-29,-176 -7,-155 M7,-155 Q29,-176 51,-151"/>')
    eye_h = max(3.0, 24.0 * (1.0 - blink * 0.94))
    return f'''<g transform="translate({x + camera_shift:.2f},{base_y - bounce:.2f})">
      <ellipse cx="0" cy="5" rx="125" ry="17" fill="#57412E" opacity=".16" filter="url(#softShadow)"/>
      <g transform="scale({squash_x:.4f},{squash_y:.4f})">
        <path d="M-51,-37 C-137,-17 -195,-74 -177,-137 C-163,-185 -111,-179 -107,-137"
              fill="none" stroke="#A96D35" stroke-width="25" stroke-linecap="round"
              transform="rotate({tail:.2f} -70 -35)"/>
        <path d="M-109,-140 Q-108,-119 -94,-119" fill="none" stroke="#F4DDBA"
              stroke-width="11" stroke-linecap="round" transform="rotate({tail:.2f} -70 -35)"/>
        <ellipse cx="0" cy="-3" rx="61" ry="88" fill="#D99049" stroke="#89542F" stroke-width="7"/>
        <path d="M-44,-45 Q-80,-19 -74,35 M44,-45 Q80,-19 74,35" fill="none"
              stroke="#B97538" stroke-width="17" stroke-linecap="round"/>
        <path d="M-27,58 Q0,76 27,58" fill="none" stroke="#F5DEBD" stroke-width="8" stroke-linecap="round"/>
        <ellipse cx="0" cy="3" rx="34" ry="66" fill="#F3DDBB" opacity=".98"/>
        <ellipse cx="-34" cy="77" rx="29" ry="15" fill="#F0D4AE"/>
        <ellipse cx="34" cy="77" rx="29" ry="15" fill="#F0D4AE"/>
        <path d="M-43,-8 Q-74,-2 -88,20" fill="none" stroke="#D99049" stroke-width="15" stroke-linecap="round"/>
        <path d="M43,-8 Q74,-2 88,20" fill="none" stroke="#D99049" stroke-width="15" stroke-linecap="round"/>
        <ellipse cx="-92" cy="22" rx="13" ry="11" fill="#F3DDBB" stroke="#89542F" stroke-width="4"/>
        <ellipse cx="92" cy="22" rx="13" ry="11" fill="#F3DDBB" stroke="#89542F" stroke-width="4"/>
        <path d="M-101,20 q-10,-13 -13,-3 M-97,29 q-12,-3 -12,8 M-88,31 q-5,12 2,14"
              fill="none" stroke="#89542F" stroke-width="4" stroke-linecap="round"/>
        <path d="M101,20 q10,-13 13,-3 M97,29 q12,-3 12,8 M88,31 q5,12 -2,14"
              fill="none" stroke="#89542F" stroke-width="4" stroke-linecap="round"/>
        <g transform="rotate({head_tilt:.2f} 0 -112)">
          <path d="M-72,-169 Q-119,-244 -47,-224 Q-24,-218 -19,-171 Z"
                fill="#D99049" stroke="#89542F" stroke-width="7"
                transform="rotate({-ear:.2f} -55 -185)"/>
          <path d="M-63,-192 Q-87,-226 -53,-215 Q-37,-210 -35,-187 Z"
                fill="#D47D75" stroke="#A76058" stroke-width="3"/>
          <path d="M72,-169 Q119,-244 47,-224 Q24,-218 19,-171 Z"
                fill="#D99049" stroke="#89542F" stroke-width="7"
                transform="rotate({ear:.2f} 55 -185)"/>
          <path d="M63,-192 Q87,-226 53,-215 Q37,-210 35,-187 Z"
                fill="#D47D75" stroke="#A76058" stroke-width="3"/>
          <ellipse cx="0" cy="-111" rx="82" ry="96" fill="#E2A258" stroke="#89542F" stroke-width="7"/>
          <ellipse cx="0" cy="-69" rx="48" ry="48" fill="#F5E0C0"/>
          <path d="{brows}" fill="none" stroke="#69452F" stroke-width="9" stroke-linecap="round"/>
          <ellipse cx="-33" cy="-127" rx="24" ry="{eye_h:.2f}" fill="#FFFBF1"/>
          <ellipse cx="33" cy="-127" rx="24" ry="{eye_h:.2f}" fill="#FFFBF1"/>
          <ellipse cx="-28" cy="-125" rx="13" ry="{max(2.0, eye_h*.72):.2f}" fill="#3B6952"/>
          <ellipse cx="38" cy="-125" rx="13" ry="{max(2.0, eye_h*.72):.2f}" fill="#3B6952"/>
          <circle cx="-34" cy="-135" r="7" fill="#FFFFFF"/><circle cx="-22" cy="-116" r="3.7" fill="#FFFFFF"/>
          <circle cx="28" cy="-135" r="7" fill="#FFFFFF"/><circle cx="40" cy="-116" r="3.7" fill="#FFFFFF"/>
          <ellipse cx="-58" cy="-78" rx="18" ry="10" fill="#D98278" opacity=".34"/>
          <ellipse cx="58" cy="-78" rx="18" ry="10" fill="#D98278" opacity=".34"/>
          <path d="M-8,-79 Q0,-88 8,-79 Q0,-69 -8,-79Z" fill="#8D583F"/>
          {mouth_markup(mouth, 0, -59)}
        </g>
      </g>
    </g>'''


def mouse_svg(x: float, base_y: float, level: float, seconds: float,
              active: bool, camera_shift: float, phase: float) -> str:
    pulse = math.sin(seconds * 15.5 + phase) if active else math.sin(seconds * 1.5 + phase)
    bounce = (4.0 + 8.0 * level) * pulse if active else 1.0 * pulse
    breath = 1.0 + 0.014 * math.sin(seconds * 2.4 + phase)
    ear = 7.0 * math.sin(seconds * 4.2 + phase)
    tail = 19.0 * math.sin(seconds * 4.7 + phase)
    head_tilt = 2.0 * math.sin(seconds * 3.2 + phase) if active else 8.0 * math.sin(seconds * 1.2 + phase)
    blink = blink_amount(seconds, phase + 1.1)
    mouth = mouth_shape(level) if active else "closed"
    eye_h = max(2.5, 18.0 * (1.0 - blink * 0.94))
    if not active:
        brows = '<path d="M-35,-104 Q-20,-119 -6,-105 M6,-108 Q22,-118 36,-103"/>'
    else:
        brows = '<path d="M-35,-104 Q-20,-121 -6,-106 M6,-106 Q22,-121 36,-104"/>'
    return f'''<g transform="translate({x + camera_shift:.2f},{base_y - bounce:.2f}) scale({breath:.4f})">
      <ellipse cx="0" cy="5" rx="87" ry="14" fill="#57412E" opacity=".16" filter="url(#softShadow)"/>
      <path d="M44,-23 C112,-45 147,-16 132,20 C121,45 89,43 83,22"
            fill="none" stroke="#A9785C" stroke-width="8" stroke-linecap="round"
            transform="rotate({tail:.2f} 55 -10)"/>
      <ellipse cx="0" cy="5" rx="49" ry="53" fill="#A9785E" stroke="#704A3A" stroke-width="6"/>
      <ellipse cx="0" cy="17" rx="30" ry="34" fill="#F2DDC6"/>
      <path d="M-31,31 q-14,14 -4,26 q12,7 20,-5 M31,31 q14,14 4,26 q-12,7 -20,-5"
            fill="none" stroke="#A9785E" stroke-width="12" stroke-linecap="round"/>
      <g transform="rotate({head_tilt:.2f} 0 -48)">
        <path d="M-40,-89 Q-87,-154 -51,-165 Q-12,-166 -12,-103Z"
              fill="#A9785E" stroke="#704A3A" stroke-width="6"
              transform="rotate({-ear:.2f} -47 -119)"/>
        <path d="M-36,-116 Q-64,-149 -49,-149 Q-27,-145 -24,-112Z" fill="#D78B7D"/>
        <path d="M40,-89 Q87,-154 51,-165 Q12,-166 12,-103Z"
              fill="#A9785E" stroke="#704A3A" stroke-width="6"
              transform="rotate({ear:.2f} 47 -119)"/>
        <path d="M36,-116 Q64,-149 49,-149 Q27,-145 24,-112Z" fill="#D78B7D"/>
        <ellipse cx="0" cy="-48" rx="59" ry="68" fill="#B48366" stroke="#704A3A" stroke-width="6"/>
        <ellipse cx="0" cy="-22" rx="42" ry="34" fill="#F3DDC7"/>
        <path d="{brows}" fill="none" stroke="#604538" stroke-width="6" stroke-linecap="round"/>
        <ellipse cx="-24" cy="-73" rx="18" ry="{eye_h:.2f}" fill="#FFF9EF"/>
        <ellipse cx="24" cy="-73" rx="18" ry="{eye_h:.2f}" fill="#FFF9EF"/>
        <ellipse cx="-21" cy="-71" rx="9" ry="{max(2.0,eye_h*.68):.2f}" fill="#3B5148"/>
        <ellipse cx="28" cy="-71" rx="9" ry="{max(2.0,eye_h*.68):.2f}" fill="#3B5148"/>
        <circle cx="-27" cy="-80" r="5.5" fill="#FFFFFF"/><circle cx="-17" cy="-67" r="2.7" fill="#FFFFFF"/>
        <circle cx="21" cy="-80" r="5.5" fill="#FFFFFF"/><circle cx="31" cy="-67" r="2.7" fill="#FFFFFF"/>
        <ellipse cx="-43" cy="-34" rx="13" ry="8" fill="#D98278" opacity=".34"/>
        <ellipse cx="43" cy="-34" rx="13" ry="8" fill="#D98278" opacity=".34"/>
        <ellipse cx="0" cy="-32" rx="8" ry="6" fill="#B96D68"/>
        <path d="M-6,-25 Q-20,-23 -28,-31 M6,-25 Q20,-23 28,-31"
              fill="none" stroke="#704A3A" stroke-width="3" stroke-linecap="round"/>
        {mouth_markup(mouth, 0, -13)}
      </g>
      <path d="M-43,-2 Q0,12 43,-2 L38,13 Q0,27 -38,13Z"
            fill="#4C9A91" stroke="#326D69" stroke-width="4"/>
      <path d="M18,7 Q37,20 29,42 Q9,39 8,21Z"
            fill="#68B4A6" stroke="#326D69" stroke-width="3"/>
      <path d="M-9,1 Q0,14 9,1" fill="none" stroke="#D6F0D8" stroke-width="3"/>
      <circle cx="0" cy="8" r="6" fill="#E8B66C" stroke="#326D69" stroke-width="2"/>
    </g>'''


def mouth_markup(shape: str, x: float, y: float) -> str:
    if shape == "closed":
        return f'<path d="M{x-13},{y} Q{x},{y+9} {x+13},{y}" fill="none" stroke="#70443B" stroke-width="4" stroke-linecap="round"/>'
    if shape == "half":
        return f'<path d="M{x-16},{y} Q{x},{y+18} {x+16},{y} Q{x},{y+7} {x-16},{y}" fill="#9D4D49" stroke="#70443B" stroke-width="3"/>'
    if shape == "open":
        return f'<ellipse cx="{x}" cy="{y+5}" rx="12" ry="19" fill="#743C3A" stroke="#70443B" stroke-width="3"/><path d="M{x-7},{y+14} Q{x},{y+19} {x+7},{y+14}" stroke="#E89A8A" stroke-width="4" fill="none"/>'
    return f'<ellipse cx="{x}" cy="{y+4}" rx="13" ry="17" fill="#743C3A" stroke="#70443B" stroke-width="3"/>'


def story_action_svg(scene_index: int, local_time: float,
                     scene_duration: float, global_time: float) -> str:
    """Small vector actions that make the seed-growing story visible in the episode."""
    duration = max(0.001, scene_duration)
    progress = max(0.0, min(1.0, local_time / duration))
    soil = (
        '<path d="M566,525 Q615,503 654,516 Q695,501 738,526 Q696,541 620,541Z" '
        'fill="#8B6847" opacity=".84"/>'
        '<path d="M594,526 Q649,515 709,529" fill="none" stroke="#C8A477" '
        'stroke-width="4" stroke-linecap="round" opacity=".8"/>'
    )
    seed = ""
    if scene_index <= 3:
        seed_y = 505.0 + (12.0 * progress if scene_index == 3 else 0.0)
        seed_opacity = 1.0 if scene_index < 3 or progress < 0.88 else max(0.0, (1.0-progress)/0.12)
        seed = (
            f'<g id="story-seed" transform="translate(650,{seed_y:.1f})" opacity="{seed_opacity:.3f}">'
            '<ellipse cx="0" cy="0" rx="8" ry="5" fill="#A87942" stroke="#694A31" stroke-width="2"/>'
            '<path d="M-4,-1 Q0,-7 5,-2" fill="none" stroke="#E8C68B" stroke-width="2"/>'
            '</g>'
        )
    spoon = ""
    if scene_index == 2:
        angle = -35.0 + 72.0 * progress
        spoon = (
            f'<g id="story-dig-spoon" transform="translate({586+18*progress:.1f},{461+15*math.sin(progress*math.pi):.1f}) rotate({angle:.1f})">'
            '<path d="M0,0 L0,45" stroke="#8D6847" stroke-width="8" stroke-linecap="round"/>'
            '<ellipse cx="0" cy="52" rx="13" ry="18" fill="#C5B9A0" stroke="#786A56" stroke-width="3"/>'
            '</g>'
            f'<g opacity="{progress:.2f}" fill="#B58B5D"><circle cx="615" cy="508" r="4"/><circle cx="632" cy="500" r="3"/><circle cx="666" cy="511" r="3"/></g>'
        )
    planting = ""
    if scene_index == 3:
        planting = '<path d="M620,521 Q650,536 680,521" fill="none" stroke="#624A35" stroke-width="5" stroke-linecap="round" opacity=".9"/>'
    watering = ""
    if scene_index == 4:
        water_opacity = max(0.0, min(1.0, (progress - 0.18) / 0.35))
        watering = (
            '<g id="story-watering-vessel" transform="translate(760,470)">'
            '<path d="M-28,-28 Q-35,13 -4,24 Q26,24 28,-18 L8,-30Z" fill="#E9B66E" stroke="#8B5E3C" stroke-width="5"/>'
            '<path d="M20,-20 Q49,-42 55,-15" fill="none" stroke="#8B5E3C" stroke-width="6" stroke-linecap="round"/>'
            '<path d="M-17,-25 Q-8,-52 12,-34" fill="none" stroke="#8B5E3C" stroke-width="5" stroke-linecap="round"/>'
            '</g>'
            f'<path d="M814,450 Q790,478 690,510" fill="none" stroke="#76B9CC" stroke-width="5" stroke-linecap="round" opacity="{water_opacity:.2f}"/>'
            f'<g fill="#8ED2E0" opacity="{water_opacity:.2f}"><path d="M738,475 q-8,12 0,17 q8,-5 0,-17Z"/><path d="M714,492 q-7,11 0,15 q7,-5 0,-15Z"/></g>'
        )
    sprout = ""
    if scene_index >= 5:
        growth = max(0.0, min(1.0, (scene_index - 5 + progress) / 3.0))
        stem_height = 7.0 + 67.0 * growth
        leaf_scale = max(0.0, min(1.0, (growth - 0.12) / 0.55))
        sprout = (
            '<g id="story-growing-sprout" transform="translate(650,521)">'
            f'<path d="M0,0 Q{-5*growth:.1f},{-stem_height*.55:.1f} 0,{-stem_height:.1f}" fill="none" stroke="#3F8152" stroke-width="7" stroke-linecap="round"/>'
            f'<g transform="translate(0,{-stem_height*.62:.1f}) scale({leaf_scale:.3f})">'
            '<path d="M0,0 Q-31,-36 -43,-10 Q-36,14 0,0Z" fill="#69A85D" stroke="#39764A" stroke-width="3"/>'
            '<path d="M0,0 Q31,-36 43,-10 Q36,14 0,0Z" fill="#87BC69" stroke="#39764A" stroke-width="3"/>'
            '</g>'
            f'<g transform="translate(0,{-stem_height:.1f}) scale({max(0.0,(growth-.55)/.45):.3f})">'
            '<path d="M0,0 Q-16,-30 0,-43 Q17,-29 0,0Z" fill="#A5CE74" stroke="#39764A" stroke-width="3"/>'
            '</g></g>'
        )
    return soil + seed + spoon + planting + watering + sprout


def svg_frame(scene: dict, scene_index: int, global_time: float,
              normalized_envelope: float, width: int = WIDTH, height: int = HEIGHT,
              include_text: bool = True, local_time: float = 0.0,
              scene_duration: float = 1.0) -> str:
    camera_scale = 1.0 + 0.018 * math.sin(global_time * 2 * math.pi / 31.0)
    camera_x = 7.0 * math.sin(global_time * 2 * math.pi / 47.0)
    wind = math.sin(global_time * 1.7 + scene_index * 0.6)
    level = max(0.0, min(1.0, normalized_envelope))
    speaker = scene["speakerId"]
    cat_expression = "surprise" if scene_index == 0 else ("clever" if scene_index == 2 else "excited")
    cat = cat_svg(385, 490, level if speaker == "pistachio" else 0.0,
                  global_time, speaker == "pistachio", camera_x * .30, .2, cat_expression)
    mouse = mouse_svg(910, 485, level if speaker == "hazelnut" else 0.0,
                      global_time, speaker == "hazelnut", camera_x * .30, 1.9)
    story_actions = story_action_svg(scene_index, local_time, scene_duration, global_time)
    lines = wrap_arabic(scene["text"], max_chars=36)
    caption_text = "".join(
        f'<text x="640" y="{621 + (i-(len(lines)-1)/2)*34:.1f}" text-anchor="middle" '
        f'direction="rtl" unicode-bidi="plaintext" font-family="Noto Sans Arabic, sans-serif" '
        f'font-size="29" font-weight="600" fill="#29483B">{esc(line)}</text>'
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
      {story_actions}{cat}{mouse}
    </g>
    <g>
      <rect x="64" y="28" width="1152" height="68" rx="26" fill="#FFF8EA" fill-opacity=".79" stroke="#FFFFFF" stroke-opacity=".78" stroke-width="2"/>
      {('<text x="640" y="73" text-anchor="middle" direction="rtl" unicode-bidi="plaintext" font-family="Noto Naskh Arabic, serif" font-size="37" font-weight="700" fill="#3D6650">حِكَايَةُ الْبَذْرَةِ الصَّغِيرَةِ</text>' if include_text else '')}
      <rect x="74" y="552" width="1132" height="136" rx="27" fill="#FFF8EA" fill-opacity=".96" stroke="#FFFFFF" stroke-width="3"/>
      {caption_text}
    </g>
    </svg>'''


def draw_arabic_overlay(scene: dict, png_path: Path) -> None:
    """Shape and center Arabic captions inside the panel with measured line widths."""
    if not features.check("raqm"):
        raise RuntimeError("Pillow RAQM is required for correct Arabic shaping")
    font_paths = (
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
    )
    selected = next((candidate for candidate in font_paths if Path(candidate).is_file()), None)
    if not selected:
        raise RuntimeError("Noto Arabic font is required for subtitle rendering")
    title_font = ImageFont.truetype(selected, 35, layout_engine=ImageFont.Layout.RAQM)
    caption_font = None
    lines = []
    for size in (29, 28, 27, 26, 25):
        candidate_font = ImageFont.truetype(selected, size, layout_engine=ImageFont.Layout.RAQM)
        candidate_lines = wrap_arabic(scene["text"], font=candidate_font, max_width=980.0)
        if len(candidate_lines) <= 3:
            caption_font, lines = candidate_font, candidate_lines
            break
    if caption_font is None:
        raise RuntimeError("Arabic caption cannot fit inside the three-line subtitle panel")
    image = Image.open(png_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    draw.text((640, 70), "حِكَايَةُ الْبَذْرَةِ الصَّغِيرَةِ", font=title_font,
              fill="#3D6650", anchor="mm", direction="rtl", language="ar")
    for line, y in zip(lines, caption_baselines(len(lines))):
        draw.text((640, y), line, font=caption_font,
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


def write_before_after_comparison(preview_dir: Path, baseline_dir: Path,
                                 output_dir: Path) -> tuple[Path, Path]:
    """Build a three-scene visual comparison using the v1 SVG previews and rendered v2 frames."""
    selections = (1, 4, 8)
    width, height = 1920, 1810
    left_x, right_x = 35, 985
    frame_y0, row_step = 120, 555
    frame_w, frame_h = 900, 506
    panels = []
    for row, scene_number in enumerate(selections):
        old_path = baseline_dir / f"scene-{scene_number:02d}.svg"
        new_path = preview_dir / f"scene-{scene_number:02d}.png"
        if not old_path.is_file() or not new_path.is_file():
            raise RuntimeError(f"Before/after comparison input missing for scene {scene_number}")
        old_svg = old_path.read_text(encoding="utf-8")
        prefix = f"before{scene_number}-"
        old_svg = re.sub(r'id="([^"]+)"', lambda match: f'id="{prefix}{match.group(1)}"', old_svg)
        old_svg = re.sub(r'url\(#([^)]+)\)', lambda match: f'url(#{prefix}{match.group(1)})', old_svg)
        old_inner = old_svg[old_svg.find(">") + 1:old_svg.rfind("</svg>")]
        new_data = base64.b64encode(new_path.read_bytes()).decode("ascii")
        y = frame_y0 + row * row_step
        panels.append(
            f'<rect x="{left_x}" y="{y}" width="{frame_w}" height="{frame_h}" rx="18" fill="#FFF8EA" stroke="#C9B99C" stroke-width="3"/>'
            f'<rect x="{right_x}" y="{y}" width="{frame_w}" height="{frame_h}" rx="18" fill="#FFF8EA" stroke="#8BAF91" stroke-width="3"/>'
            f'<svg x="{left_x}" y="{y}" width="{frame_w}" height="{frame_h}" viewBox="0 0 1280 720">{old_inner}</svg>'
            f'<image x="{right_x}" y="{y}" width="{frame_w}" height="{frame_h}" href="data:image/png;base64,{new_data}" preserveAspectRatio="xMidYMid meet"/>'
            f'<text x="{left_x+frame_w/2}" y="{y+frame_h+32}" text-anchor="middle" font-family="sans-serif" font-size="24" fill="#604F3C">المشهد {scene_number} — قبل</text>'
            f'<text x="{right_x+frame_w/2}" y="{y+frame_h+32}" text-anchor="middle" font-family="sans-serif" font-size="24" fill="#326D50">المشهد {scene_number} — بعد</text>'
        )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        '<rect width="100%" height="100%" fill="#EFF2E7"/>'
        '<text x="960" y="48" text-anchor="middle" font-family="sans-serif" font-size="34" font-weight="700" fill="#315C48">مقارنة قبل / بعد — نسخة شخصيات v2</text>'
        '<text x="485" y="91" text-anchor="middle" font-family="sans-serif" font-size="27" font-weight="700" fill="#604F3C">قبل — النسخة 1</text>'
        '<text x="1435" y="91" text-anchor="middle" font-family="sans-serif" font-size="27" font-weight="700" fill="#326D50">بعد — النسخة 2</text>'
        + "".join(panels) + "</svg>"
    )
    svg_path = output_dir / "before-after-comparison.svg"
    png_path = output_dir / "before-after-comparison.png"
    svg_path.write_text(svg, encoding="utf-8")
    subprocess.run([
        "rsvg-convert", "--width", str(width), "--height", str(height),
        "--output", str(png_path), str(svg_path)
    ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
    return svg_path, png_path


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
            cursor += PAUSE_SECONDS
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
        svg_path.write_text(svg_frame(scene, scene_index, global_time, level, include_text=False, local_time=local_time, scene_duration=scene["durationSeconds"]), encoding="utf-8")
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

    comparison_svg, comparison_png = write_before_after_comparison(preview_dir, Path("docs/preview"), output_dir)

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
        "versionName": "نسخة شخصيات v2",
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
            "pauseBetweenChunksSeconds": PAUSE_SECONDS,
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
        "beforeAfterComparison": {"svg": comparison_svg.name, "png": comparison_png.name, "referenceScenes": [1, 4, 8]},
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
        cursor += row["processedDurationSeconds"] + (PAUSE_SECONDS if index < len(chunks)-1 else 0.0)
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

#!/usr/bin/env bash
set -euo pipefail

output_dir="artifacts/silma-episode-pilot/references"
if [[ "$#" -gt 0 && -n "$1" ]]; then output_dir="$1"; fi
mkdir -p "$output_dir"

for command in espeak-ng ffmpeg ffprobe python3; do
  command -v "$command" >/dev/null 2>&1 || { echo "Required command missing: $command" >&2; exit 1; }
done

reference_text="مَرْحَبًا، هَذَا صَوْتٌ تَجْرِيبِيٌّ لِشَخْصِيَّةٍ كَرْتُونِيَّةٍ."
espeak-ng -v "ar+f3" -s 160 -p 68 -w "$output_dir/pistachio-source.wav" "$reference_text"
espeak-ng -v "ar+m3" -s 140 -p 43 -w "$output_dir/hazelnut-source.wav" "$reference_text"

ffmpeg -hide_banner -nostdin -loglevel error -y \
  -i "$output_dir/pistachio-source.wav" -map 0:a:0 -vn -ac 1 -ar 24000 \
  -c:a pcm_s16le "$output_dir/pistachio-reference.wav"
ffmpeg -hide_banner -nostdin -loglevel error -y \
  -i "$output_dir/hazelnut-source.wav" -map 0:a:0 -vn -ac 1 -ar 24000 \
  -c:a pcm_s16le "$output_dir/hazelnut-reference.wav"

python3 - "$output_dir" <<'PY'
import hashlib
import json
import math
import sys
from pathlib import Path

import soundfile as sf

root = Path(sys.argv[1])
reference_text = "مَرْحَبًا، هَذَا صَوْتٌ تَجْرِيبِيٌّ لِشَخْصِيَّةٍ كَرْتُونِيَّةٍ."

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

personas = {
    "pistachio": {"voice": "ar+f3", "speed": 160, "pitch": 68, "file": "pistachio-reference.wav"},
    "hazelnut": {"voice": "ar+m3", "speed": 140, "pitch": 43, "file": "hazelnut-reference.wav"},
}
files = {}
for persona, config in personas.items():
    path = root / config["file"]
    audio, rate = sf.read(path, dtype="float32", always_2d=True)
    duration = len(audio) / float(rate)
    if rate != 24000 or audio.shape[1] != 1 or audio.size == 0 or not math.isfinite(float(abs(audio).max())):
        raise SystemExit(f"Method A reference preflight failed: {path}")
    files[persona] = {
        "path": config["file"],
        "sha256": sha256(path),
        "sizeBytes": path.stat().st_size,
        "durationSeconds": round(duration, 6),
        "sampleRate": rate,
        "channels": int(audio.shape[1]),
    }
    print(f"REFERENCE={persona} voice={config['voice']} speed={config['speed']} pitch={config['pitch']} sha256={files[persona]['sha256']} duration={duration:.6f}s")

manifest = {
    "method": "A",
    "engine": "eSpeak NG",
    "referenceText": reference_text,
    "license": "GPL-3.0-or-later",
    "personas": personas,
    "files": files,
}
(root / "reference-manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print("METHOD_A_REFERENCE_PREFLIGHT=PASS")
PY

printf 'eSpeak NG: '
espeak-ng --version | head -n 1
sha256sum "$output_dir"/*reference.wav

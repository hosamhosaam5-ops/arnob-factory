#!/usr/bin/env bash
set -euo pipefail

output_dir="artifacts/silma-letter-card/references"
if [[ "$#" -gt 0 && -n "$1" ]]; then output_dir="$1"; fi
source_dir="${ARNOB_COMMITTED_REFERENCES_DIR:-assets/references}"
approval_manifest="${ARNOB_REFERENCE_APPROVAL_MANIFEST:-assets/references/pistachio-method-a-approval.json}"
mkdir -p "$output_dir"

command -v python3 >/dev/null 2>&1 || {
  echo "Required command missing: python3" >&2
  exit 1
}
test -s "$approval_manifest"

python3 - "$source_dir" "$output_dir" "$approval_manifest" <<'PY'
import hashlib
import json
import shutil
import sys
import wave
from pathlib import Path

source_dir = Path(sys.argv[1])
output_dir = Path(sys.argv[2])
approval_path = Path(sys.argv[3])
approval = json.loads(approval_path.read_text(encoding="utf-8"))

persona_settings = {
    "pistachio": {
        "voice": "ar+f3", "speed": 160, "pitch": 68,
        "file": "pistachio-reference.wav",
    },
    "hazelnut": {
        "voice": "ar+m3", "speed": 140, "pitch": 43,
        "file": "hazelnut-reference.wav",
    },
}

def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

verification = approval.get("verification", {})
if verification.get("mode") != "pcm-sha256-only":
    raise SystemExit("Reference policy must be pcm-sha256-only")
if verification.get("sampleRate") != 24000 or verification.get("channels") != 1:
    raise SystemExit("Approved Method A references must be mono 24kHz PCM")
if verification.get("sampleWidthBytes") != 2:
    raise SystemExit("Approved Method A references must be 16-bit PCM")

files = {}
for persona, config in persona_settings.items():
    approved = approval.get("references", {}).get(persona)
    if not isinstance(approved, dict):
        raise SystemExit(f"Missing committed PCM approval entry for {persona}")
    file_name = config["file"]
    if approved.get("file") != file_name:
        raise SystemExit(f"Reference file name mismatch for {persona}")
    source = source_dir / file_name
    target = output_dir / file_name
    if not source.is_file() or source.stat().st_size == 0:
        raise SystemExit(f"Missing committed reference WAV: {source}")

    with wave.open(str(source), "rb") as wav:
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        sample_width = wav.getsampwidth()
        samples = wav.getnframes()
        pcm = wav.readframes(samples)

    if (channels, sample_rate, sample_width) != (1, 24000, 2):
        raise SystemExit(
            f"Reference container mismatch for {persona}: "
            f"channels={channels} rate={sample_rate} sample_width={sample_width}"
        )
    pcm_sha = sha256_bytes(pcm)
    if pcm_sha != approved.get("pcmSha256"):
        raise SystemExit(
            f"REFERENCE_PCM_SHA256_MISMATCH {persona} "
            f"expected={approved.get('pcmSha256')} actual={pcm_sha}"
        )
    if samples != approved.get("samples"):
        raise SystemExit(
            f"REFERENCE_SAMPLE_COUNT_MISMATCH {persona} "
            f"expected={approved.get('samples')} actual={samples}"
        )

    if source.resolve() != target.resolve():
        shutil.copy2(source, target)

    file_sha = sha256_file(target)
    duration = samples / float(sample_rate)
    files[persona] = {
        "path": file_name,
        "sha256": file_sha,
        "sizeBytes": target.stat().st_size,
        "durationSeconds": round(duration, 6),
        "sampleRate": sample_rate,
        "channels": channels,
        "samples": samples,
        "pcmSha256": pcm_sha,
        "verification": "pcm-sha256-only",
        "approvalId": (
            approval.get("approvalId")
            if persona == "pistachio"
            else "hazelnut-method-a-pinned-v1"
        ),
        "voice": config["voice"],
        "speed": config["speed"],
        "pitch": config["pitch"],
    }
    print(
        f"REFERENCE={persona} pcm_sha256={pcm_sha} "
        f"file_sha256={file_sha} duration={duration:.6f}s"
    )

manifest = {
    "method": "A",
    "engine": "committed approved WAV references",
    "referenceText": approval["referenceText"],
    "license": "GPL-3.0-or-later",
    "personas": {
        persona: {
            "voice": config["voice"],
            "speed": config["speed"],
            "pitch": config["pitch"],
            "file": config["file"],
        }
        for persona, config in persona_settings.items()
    },
    "files": files,
    "referenceApproval": {
        "approvalId": approval["approvalId"],
        "name": approval["name"],
        "legacyReferenceSha256": approval["supersedes"]["sha256"],
        "legacyReferenceReproducible": approval["supersedes"]["reproducible"],
        "note": approval["supersedes"]["note"],
        "verification": "pcm-sha256-only",
    },
}
(output_dir / "reference-manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print("METHOD_A_REFERENCE_PCM_PREFLIGHT=PASS")
PY

echo "Reference policy: committed PCM only; no eSpeak regeneration."
sha256sum "$output_dir"/*reference.wav

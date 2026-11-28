#!/usr/bin/env bash
set -euo pipefail
output_dir="artifacts/silma-episode-pilot/references"
if [[ "$#" -gt 0 && -n "$1" ]]; then output_dir="$1"; fi
mkdir -p "$output_dir"
for command in espeak-ng ffmpeg ffprobe python3; do
  command -v "$command" >/dev/null 2>&1 || { echo "Required command missing: $command" >&2; exit 1; }
done
approval_manifest="${ARNOB_REFERENCE_APPROVAL_MANIFEST:-assets/references/pistachio-method-a-approval.json}"
approved_reference="${ARNOB_PISTACHIO_REFERENCE_FILE:-}"
if [[ ! -s "$approval_manifest" ]]; then
  echo "Missing approved Pistachio reference manifest: $approval_manifest" >&2
  exit 1
fi
reference_text="مَرْحَبًا، هَذَا صَوْتٌ تَجْرِيبِيٌّ لِشَخْصِيَّةٍ كَرْتُونِيَّةٍ."
if [[ -n "$approved_reference" ]]; then
  test -s "$approved_reference"
  cp "$approved_reference" "$output_dir/pistachio-reference.wav"
else
  espeak-ng -v "ar+f3" -s 160 -p 68 -w "$output_dir/pistachio-source.wav" "$reference_text"
  ffmpeg -hide_banner -nostdin -loglevel error -y -i "$output_dir/pistachio-source.wav" -map 0:a:0 -vn -ac 1 -ar 24000 -c:a pcm_s16le "$output_dir/pistachio-reference.wav"
fi
espeak-ng -v "ar+m3" -s 140 -p 43 -w "$output_dir/hazelnut-source.wav" "$reference_text"
ffmpeg -hide_banner -nostdin -loglevel error -y -i "$output_dir/hazelnut-source.wav" -map 0:a:0 -vn -ac 1 -ar 24000 -c:a pcm_s16le "$output_dir/hazelnut-reference.wav"
python3 - "$output_dir" "$approval_manifest" <<'PY'
import hashlib, json, math, sys, wave
from pathlib import Path
import soundfile as sf
root = Path(sys.argv[1])
approval = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
reference_text = approval["referenceText"]
personas = {
    "pistachio": {"voice":"ar+f3","speed":160,"pitch":68,"file":"pistachio-reference.wav"},
    "hazelnut": {"voice":"ar+m3","speed":140,"pitch":43,"file":"hazelnut-reference.wav"},
}
def sha256(path):
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""): digest.update(block)
    return digest.hexdigest()
files={}
for persona, config in personas.items():
    path=root/config["file"]
    audio, rate=sf.read(path,dtype="float32",always_2d=True)
    duration=len(audio)/float(rate)
    if rate != 24000 or audio.shape[1] != 1 or audio.size == 0 or not math.isfinite(float(abs(audio).max())):
        raise SystemExit(f"Method A reference preflight failed: {path}")
    details={"path":config["file"],"sha256":sha256(path),"sizeBytes":path.stat().st_size,"durationSeconds":round(duration,6),"sampleRate":rate,"channels":int(audio.shape[1])}
    if persona=="pistachio":
        with wave.open(str(path),"rb") as wav:
            if wav.getnchannels()!=1 or wav.getframerate()!=24000 or wav.getsampwidth()!=2:
                raise SystemExit("Approved Pistachio reference must be mono 24kHz PCM s16le")
            pcm=wav.readframes(wav.getnframes())
            samples=wav.getnframes()
        pcm_sha=hashlib.sha256(pcm).hexdigest()
        if samples!=approval["samples"] or path.stat().st_size!=approval["sizeBytes"] or pcm_sha!=approval["pcmSha256"]:
            raise SystemExit(f"Pistachio reference approval mismatch: samples={samples} pcm_sha256={pcm_sha}")
        details.update({"approvalId":approval["approvalId"],"approvalName":approval["name"],"canonicalSha256":approval["sha256"],"pcmSha256":pcm_sha,"legacyReferenceSha256":approval["supersedes"]["sha256"],"legacyReferenceReproducible":approval["supersedes"]["reproducible"],"approvalNote":approval["supersedes"]["note"]})
    files[persona]=details
    print(f"REFERENCE={persona} voice={config['voice']} speed={config['speed']} pitch={config['pitch']} sha256={details['sha256']} duration={duration:.6f}s")
manifest={"method":"A","engine":"eSpeak NG","referenceText":reference_text,"license":"GPL-3.0-or-later","personas":personas,"files":files,"referenceApproval":{"approvalId":approval["approvalId"],"name":approval["name"],"legacyReferenceSha256":approval["supersedes"]["sha256"],"legacyReferenceReproducible":False,"note":approval["supersedes"]["note"]}}
(root/"reference-manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print("METHOD_A_REFERENCE_PREFLIGHT=PASS")
PY
printf 'eSpeak NG: '
espeak-ng --version | head -n 1
sha256sum "$output_dir"/*reference.wav

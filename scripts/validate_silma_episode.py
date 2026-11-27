#!/usr/bin/env python3
"""Strictly validate one eight-chunk SILMA CPU run against its source manifest."""

from __future__ import annotations
import hashlib, json, math, subprocess, sys
from pathlib import Path
from typing import Any

EXPECTED = {
    "silma": "f43256d0b78b8803c638aed0875da5a4b372b4a784690a0156e5baff14f7336c",
    "vocos": "97ec976ad1fd67a33ab2682d29c0ac7df85234fae875aefcc5fb215681a91b2a",
}

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""): digest.update(block)
    return digest.hexdigest()

def probe(path: Path) -> dict[str, Any]:
    payload = json.loads(subprocess.run(
        ["ffprobe","-v","error","-show_entries","format=duration,size:stream=codec_name,sample_rate,channels,bit_rate","-of","json",str(path)],
        check=True,capture_output=True,text=True,
    ).stdout)
    stream = payload["streams"][0]
    return {"codec":stream.get("codec_name"),"sampleRate":int(stream.get("sample_rate",0)),
            "channels":int(stream.get("channels",0)),"bitRate":int(stream.get("bit_rate") or 0),
            "durationSeconds":float(payload["format"]["duration"])}

def main() -> int:
    if len(sys.argv) != 3: raise SystemExit("usage: validate_silma_episode.py OUTPUT_DIR EPISODE_MANIFEST")
    root, script_path = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    run=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    script=json.loads(script_path.read_text(encoding="utf-8"))
    if run.get("episodeId") != "letter-alif" or script.get("episodeId") != "letter-alif": raise SystemExit("Unexpected episode identity")
    if run.get("status") != "complete" or run.get("dryRun") is not False or run.get("modelInferenceInvoked") is not True: raise SystemExit("Real SILMA inference did not complete")
    if run.get("method") != "A" or run.get("nfeStep") != 16 or run.get("chunksPlanned") != 8: raise SystemExit("Expected Method A, nfe_step=16 and 8 chunks")
    script_sha=sha256(script_path)
    if run.get("episodeScriptSha256") != script_sha: raise SystemExit("Source script SHA-256 mismatch")
    expected_chunks, completed = script.get("chunks"), run.get("completedChunks", [])
    if not isinstance(expected_chunks,list) or len(expected_chunks)!=8 or len(completed)!=8: raise SystemExit("Expected exactly 8 source and completed chunks")
    for expected, actual in zip(expected_chunks, completed):
        for key in ("chunkId","speakerId","text"):
            if actual.get(key) != expected.get(key): raise SystemExit(f"Exact script mismatch: {expected.get('chunkId')}")
        speaker=actual["speakerId"]
        voice="ar+f3" if speaker=="pistachio" else "ar+m3"
        expected_ref=(160,68) if speaker=="pistachio" else (140,43)
        settings=actual.get("settings",{})
        if settings.get("method")!="A" or settings.get("voice")!=voice or settings.get("nfeStep")!=16: raise SystemExit(f"Method A/nfe_step mismatch: {actual['chunkId']}")
        if (settings.get("referenceSpeed"),settings.get("referencePitch"))!=expected_ref: raise SystemExit(f"Reference speed/pitch mismatch: {actual['chunkId']}")
        timing=actual.get("timing",{})
        for key in ("cpuSeconds","wallSeconds","audioDurationSeconds","rtf"):
            value=timing.get(key)
            if not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0: raise SystemExit(f"Invalid timing {key}: {actual['chunkId']}")
        if abs(timing["rtf"]-timing["wallSeconds"]/timing["audioDurationSeconds"])>0.02: raise SystemExit(f"RTF mismatch: {actual['chunkId']}")
        for media_key, codec in (("wav","pcm_s16le"),("mp3","mp3")):
            item=actual.get(media_key,{})
            rel=Path(item.get("path",""))
            if rel.is_absolute() or ".." in rel.parts: raise SystemExit(f"Unsafe media path: {rel}")
            path=root/rel
            if not path.is_file() or path.stat().st_size==0 or sha256(path)!=item.get("sha256"): raise SystemExit(f"{media_key} SHA mismatch: {actual['chunkId']}")
            meta=probe(path)
            if meta["codec"]!=codec or meta["sampleRate"]!=24000 or meta["channels"]!=1: raise SystemExit(f"Invalid media format: {actual['chunkId']} {media_key}")
            if media_key=="mp3" and not 120000<=meta["bitRate"]<=136000: raise SystemExit(f"MP3 bitrate out of range: {actual['chunkId']}")
    for key, expected in EXPECTED.items():
        item=run.get("weights",{}).get(key,{})
        if item.get("sha256")!=expected or item.get("expectedSha256")!=expected or item.get("verifiedBeforeInference") is not True: raise SystemExit(f"{key} weight SHA verification missing or failed")
    for persona in ("pistachio","hazelnut"):
        ref=run.get("references",{}).get(persona,{})
        path=root/"references"/ref.get("path","")
        if not path.is_file() or sha256(path)!=ref.get("sha256"): raise SystemExit(f"Generated eSpeak reference SHA mismatch: {persona}")
    print(json.dumps({"status":"PASS","chunks":len(completed),"episodeScriptSha256":script_sha,
        "weights":{k:run["weights"][k]["sha256"] for k in EXPECTED},
        "references":{k:run["references"][k]["sha256"] for k in ("pistachio","hazelnut")},
        "totalInferenceWallSeconds":run["measurementSummary"]["totalInferenceWallSeconds"],
        "totalInferenceCpuSeconds":run["measurementSummary"]["totalInferenceCpuSeconds"],
        "mp3":[x["mp3"]["path"] for x in completed]},ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__": raise SystemExit(main())

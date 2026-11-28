# Alif v1 — Run #8 asset inventory

Source: GitHub Actions production Pipeline run #8, artifact ID 15257366397.
Source artifact ZIP SHA-256: fd9f5ea62e2413dc5b1fdfb9a76c86cd15a965d707fdef69593c3f9c93336c6b
Source artifact: 594 files; ZIP integrity validation passed.

The following media hashes are recorded from the verified Run #8 payload. The MP4, eight MP3 files and three preview PNGs are now stored on this branch.

## Final MP4 (persisted to this branch)
- `alif-episode-v1.mp4` — 1,733,199 bytes — SHA-256 `2cc2e7b9254beadc908ca62fd1f64d44d0ad89ad2c4b70aead4ed8c8269a8a8d`

## Eight MP3 chunks (persisted to this branch)
| File | Bytes | SHA-256 |
|---|---:|---|
| `audio/chunk-01.mp3` | 34,988 | `fb0355425e3837d7e75ac1a129eea526f1e3b1e6a8a25e071430d3685c031179` |
| `audio/chunk-02.mp3` | 58,412 | `7446bce63ccc2e5a3e1063b547e775466458110ea941fc56ee69b904895f588b` |
| `audio/chunk-03.mp3` | 39,980 | `3a9459f3115007cdd0e6871b72367c45bb589e654ce293ae3d886bd2756502f8` |
| `audio/chunk-04.mp3` | 44,588 | `4b7e3afd6cba5433856ef834f37592b71006dc9e08e99eb9c9ded6f9f9e4f403` |
| `audio/chunk-05.mp3` | 32,300 | `41426b8e0b70f0a8449d2f8f7ccf0de961e741f337f1d8371e263352b352eb78` |
| `audio/chunk-06.mp3` | 33,452 | `587a05613ee22d4769200179e8443ce941bd14b7a33a7e0874903256d84e42ec` |
| `audio/chunk-07.mp3` | 54,956 | `00b6ab3345b40c1bf35550720abe2fcba9b1fb9494ee05e06409cac552130aa0` |
| `audio/chunk-08.mp3` | 50,732 | `cc727ac0fb6f604d830522ccd4590be761d8ca16fff190c3636508bad8f3a3cc` |

## Three previews (persisted to this branch)
| File | Bytes | SHA-256 |
|---|---:|---|
| `preview/final-scene.png` | 170,439 | `742aa3d752a59929f631ddc8638bb8f9cd270ef682c2cfafb90d55186512d666` |
| `preview/letter-card.png` | 172,154 | `3b350256a9f8fad52cbc7e8ea5fce1d1475d4a2e91b7f20c58d54605c9806972` |
| `preview/word-rabbit.png` | 165,364 | `b45b16ec24459214f0ca8f02a532166cab9230ab3dce95ecd0d919890bb7d269` |

## Persisted text artifacts
- `reports/manifest.json` — SHA-256 is in `SHA256SUMS` (hash applies to the stored branch copy).
- `reports/silencedetect-report.json` — SHA-256 is in `SHA256SUMS` (hash applies to the stored branch copy).
- `reports/timeline.json` — SHA-256 is in `SHA256SUMS` (hash applies to the stored branch copy).
- `reports/audio-mix-qa-summary.json` and `reports/video-qa-summary.json` — compact QA summaries derived from Run #8 evidence. The full original reports remain in the source Run #8 artifact.

Run #8 QA evidence: Audio Mix QA PASS; Video QA PASS; maximum measured silence 0.445229 seconds against the unchanged 0.5-second limit; black-frame duration 0; A/V duration difference 0.000667 seconds.

The 535-frame ZIP is retained as one asset on GitHub Release alif-episode-v1; it is not mirrored to this branch.

## Transfer outcome
The temporary GitHub Actions workflow downloaded the original Run #8 artifact, verified the supplied SHA-256 values, and pushed the MP4, eight MP3 chunks and three preview PNGs to this branch without rewriting its history. The GitHub Release contains the additional QA reports, silence/timeline/manifest files, SHA256SUMS and the 535-frame ZIP.

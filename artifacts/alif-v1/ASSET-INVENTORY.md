# Alif v1 — Run #8 asset inventory

Source: GitHub Actions production Pipeline run #8, artifact ID 15257366397.
Source artifact ZIP SHA-256: fd9f5ea62e2413dc5b1fdfb9a76c86cd15a965d707fdef69593c3f9c93336c6b
Source artifact: 594 files; source ZIP CRC/integrity validation passed.

The hashes below are SHA-256 fingerprints from the verified Run #8 payload. This inventory is not a substitute for storing the corresponding binary files.

## Final MP4 (not persisted to this branch)
- `alif-episode-v1.mp4` — 1,733,199 bytes — `2cc2e7b9254beadc908ca62fd1f64d44d0ad89ad2c4b70aead4ed8c8269a8a8d`

## Eight MP3 chunks (not persisted to this branch)
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

## Three previews (not persisted to this branch)
| File | Bytes | SHA-256 |
|---|---:|---|
| `preview/final-scene.png` | 170,439 | `742aa3d752a59929f631ddc8638bb8f9cd270ef682c2cfafb90d55186512d666` |
| `preview/letter-card.png` | 172,154 | `3b350256a9f8fad52cbc7e8ea5fce1d1475d4a2e91b7f20c58d54605c9806972` |
| `preview/word-rabbit.png` | 165,364 | `b45b16ec24459214f0ca8f02a532166cab9230ab3dce95ecd0d919890bb7d269` |

## Reports and manifest (persisted)
- `reports/manifest.json` — SHA-256 `4f43a2815606e2ae9066f29f855b37cbcf1d563cf2eba02c3e4b7894eb378d14`
- `reports/silencedetect-report.json` — SHA-256 `5bfc09e9a9a358d675c4e74057cd79aec27f46f2d55374a0271e358e64ebf4a8`
- `reports/timeline.json` — SHA-256 `720319b95bd9f3d53ebbb58aba76c9612a14490b6eca13b45d0999bd782ff6ea`

The original Run #8 Audio Mix QA and Video QA report checks were PASS. The final measured maximum silence was 0.445229 seconds against the unchanged 0.5-second limit, black-frame duration was 0, and A/V duration difference was 0.000667 seconds. The source reports remain in the Run #8 artifact; only the three text reports above were copied into this branch.

The 535-frame ZIP is intentionally excluded, per the owner's instruction.

## Transfer limitation
The connected GitHub actions available in this session expose text blob/tree/ref operations, but no file-path or binary-upload operation. Therefore the MP4, MP3s and PNG previews were not written to this branch. The MP4 itself is 1,733,199 bytes (within GitHub's usual per-object size ceiling), but the connector does not expose a verified binary-transfer path for it. No claim is made that these binary assets are present here.

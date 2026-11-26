# Provenance of the media QA gates

The video and audio quality modules are copied byte-for-byte from the approved private production snapshot at commit `2f19287905404b628437115d543f0f02398a60f5`.

- `src/production/audio-mixer.js` — Git blob `2e8495f18716556a1617666b535d2911865af276`
- `src/production/video-qa.js` — Git blob `7ebd1f573351b4726be87a138b6af87fe5c16863`

The episode wrapper calls `mixAudioForVideo()` and `inspectVideoQuality()` with their default limits. It does not override, bypass, or weaken thresholds. No music or sound effects are added; the mixer's required bed input is generated digital silence.

The current default gates include continuous silence <= 0.5 s, A/V duration difference <= 0.15 s, black segment <= 0.25 s, H.264/AAC streams, maximum duration 300 s and maximum file size 512 MiB. Audio Mix QA also checks PCM s16le, 48 kHz stereo, A/V duration difference <= 0.15 s and continuous silence <= 0.5 s.

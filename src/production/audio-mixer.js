import { spawn } from "node:child_process";
import { access, lstat, realpath, stat } from "node:fs/promises";
import { constants as fsConstants } from "node:fs";
import path from "node:path";
import { DEFAULT_LIMITS, parseSilenceSegments } from "./video-qa.js";

const DEFAULT_TIMEOUT_MS = 120_000;
const MAX_OUTPUT_CHARS = 64 * 1024;
const SAMPLE_RATE = 48_000;
const CHANNELS = 2;
const CHANNEL_LAYOUT = "stereo";
const DEFAULT_DUCKING = Object.freeze({
  musicGainDb: -18,
  voiceGainDb: 0,
  sidechainThreshold: 0.03,
  sidechainRatio: 10,
  attackMs: 20,
  releaseMs: 250,
  // Retain a real, uncompressed music-bed contribution so sidechain output
  // cannot mute the bed during low/zero voiceover windows.
  sidechainMix: 0.7
});

function assertCondition(condition, message) {
  if (!condition) throw new TypeError(message);
}

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function appendBounded(current, chunk) {
  return (current + chunk.toString()).slice(-MAX_OUTPUT_CHARS);
}

function runCommand(command, args, { timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      shell: false,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"]
    });
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    let settled = false;

    const finish = (callback, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      callback(value);
    };

    child.stdout.on("data", chunk => {
      stdout = appendBounded(stdout, chunk);
    });
    child.stderr.on("data", chunk => {
      stderr = appendBounded(stderr, chunk);
    });

    const timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGKILL");
    }, timeoutMs);

    child.once("error", error => finish(reject, error));
    child.once("close", (code, signal) => {
      if (timedOut) {
        finish(reject, new Error(command + " timed out after " + timeoutMs + "ms"));
      } else if (code !== 0) {
        const detail = stderr.trim() || ("process exited with code " + String(code));
        finish(reject, new Error(command + " failed: " + detail));
      } else {
        finish(resolve, { code, signal, stdout, stderr });
      }
    });
  });
}

async function resolveReadableFile(inputPath, label) {
  assertCondition(typeof inputPath === "string" && inputPath.trim().length > 0,
    label + " must be a non-empty path");
  const absolutePath = path.resolve(inputPath);
  const actualPath = await realpath(absolutePath);
  await access(actualPath, fsConstants.R_OK);
  const info = await stat(actualPath);
  assertCondition(info.isFile(), label + " must be a regular file");
  assertCondition(info.size > 0, label + " must not be empty");
  return { path: actualPath, sizeBytes: info.size };
}

async function resolveNewWaveFile(outputPath) {
  assertCondition(typeof outputPath === "string" && outputPath.trim().length > 0,
    "outputPath must be a non-empty path");
  const absolutePath = path.resolve(outputPath);
  assertCondition(path.extname(absolutePath).toLowerCase() === ".wav",
    "outputPath must use the .wav extension");
  const parentPath = await realpath(path.dirname(absolutePath));
  const parentInfo = await stat(parentPath);
  assertCondition(parentInfo.isDirectory(), "output directory must exist");
  await access(parentPath, fsConstants.W_OK);
  const resolvedPath = path.join(parentPath, path.basename(absolutePath));
  try {
    await lstat(resolvedPath);
    throw new Error("outputPath already exists; refusing to overwrite it");
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  return resolvedPath;
}

function parseJsonOutput(result, label) {
  try {
    return JSON.parse(result.stdout);
  } catch {
    throw new Error(label + " returned invalid JSON");
  }
}

function parseRational(value) {
  if (typeof value !== "string") return null;
  const parts = value.split("/").map(Number);
  if (parts.length !== 2 || !parts.every(Number.isFinite) || parts[1] === 0) return null;
  return parts[0] / parts[1];
}

function getVideoDurationSeconds(probe) {
  const streams = Array.isArray(probe.streams) ? probe.streams : [];
  const video = streams.find(stream => stream.codec_type === "video");
  assertCondition(Boolean(video), "videoPath must contain a video stream");

  const explicitDuration = finiteNumber(video.duration);
  if (explicitDuration !== null && explicitDuration > 0) return explicitDuration;

  const durationTicks = finiteNumber(video.duration_ts);
  const timeBase = parseRational(video.time_base);
  if (durationTicks !== null && durationTicks > 0 && timeBase !== null) {
    const derived = durationTicks * timeBase;
    if (Number.isFinite(derived) && derived > 0) return derived;
  }

  const formatDuration = finiteNumber(probe.format?.duration);
  assertCondition(formatDuration !== null && formatDuration > 0,
    "ffprobe did not report a positive video duration");
  return formatDuration;
}

function validateGainDb(value, label) {
  assertCondition(Number.isFinite(value) && value >= -60 && value <= 12,
    label + " must be between -60 and +12 dB");
}

function validateDucking(input = {}) {
  assertCondition(input !== null && typeof input === "object" && !Array.isArray(input),
    "ducking must be an object");
  const config = { ...DEFAULT_DUCKING, ...input };
  validateGainDb(config.musicGainDb, "ducking.musicGainDb");
  validateGainDb(config.voiceGainDb, "ducking.voiceGainDb");
  assertCondition(Number.isFinite(config.sidechainThreshold) &&
    config.sidechainThreshold > 0 && config.sidechainThreshold <= 1,
  "ducking.sidechainThreshold must be greater than 0 and at most 1");
  assertCondition(Number.isFinite(config.sidechainRatio) &&
    config.sidechainRatio >= 1 && config.sidechainRatio <= 20,
  "ducking.sidechainRatio must be between 1 and 20");
  assertCondition(Number.isFinite(config.sidechainMix) &&
    config.sidechainMix >= 0 && config.sidechainMix <= 1,
  "ducking.sidechainMix must be between 0 and 1");
  for (const key of ["attackMs", "releaseMs"]) {
    assertCondition(Number.isInteger(config[key]) && config[key] >= 1 && config[key] <= 5000,
      "ducking." + key + " must be an integer between 1 and 5000");
  }
  return config;
}

function formatSeconds(value) {
  return Number(value.toFixed(6)).toFixed(6);
}

function validateSoundEffects(soundEffects) {
  assertCondition(Array.isArray(soundEffects) && soundEffects.length <= 32,
    "soundEffects must be an array containing no more than 32 items");
  return soundEffects.map((effect, index) => {
    assertCondition(effect !== null && typeof effect === "object" && !Array.isArray(effect),
      "soundEffects[" + index + "] must be an object");
    const offsetSeconds = effect.offsetSeconds ?? 0;
    const gainDb = effect.gainDb ?? -10;
    assertCondition(Number.isFinite(offsetSeconds) && offsetSeconds >= 0,
      "soundEffects[" + index + "].offsetSeconds must be a finite non-negative number");
    validateGainDb(gainDb, "soundEffects[" + index + "].gainDb");
    assertCondition(typeof effect.path === "string" && effect.path.trim().length > 0,
      "soundEffects[" + index + "].path must be a non-empty path");
    return { path: effect.path, offsetSeconds, gainDb };
  });
}

/**
 * Build an FFmpeg-only mix: narration, a looped/ducked music bed, and timed SFX.
 * All input paths are separate argv values; no shell or network is used.
 */
export function buildAudioMixerArgs({
  voiceoverPath,
  musicPath,
  soundEffects = [],
  outputPath,
  videoDurationSeconds,
  ducking = DEFAULT_DUCKING
}) {
  const duration = formatSeconds(videoDurationSeconds);
  const filters = [
    "[0:a]aresample=" + SAMPLE_RATE +
      ",aformat=sample_fmts=fltp:channel_layouts=" + CHANNEL_LAYOUT +
      ",volume=" + ducking.voiceGainDb + "dB,apad,atrim=duration=" + duration +
      ",asetpts=PTS-STARTPTS[voice]",
    "[1:a]aresample=" + SAMPLE_RATE +
      ",aformat=sample_fmts=fltp:channel_layouts=" + CHANNEL_LAYOUT +
      ",volume=" + ducking.musicGainDb + "dB,apad,atrim=duration=" + duration +
      ",asetpts=PTS-STARTPTS[music]",
    "[music]asplit=2[music_dry][music_sidechain]",
    "[music_sidechain][voice]sidechaincompress=threshold=" + ducking.sidechainThreshold +
      ":ratio=" + ducking.sidechainRatio +
      ":attack=" + ducking.attackMs +
      ":release=" + ducking.releaseMs +
      ":makeup=1:mix=1[compressed_music]",
    "[music_dry][compressed_music]amix=inputs=2:duration=first:dropout_transition=0" +
      ":normalize=0:weights=\'" + Number((1 - ducking.sidechainMix).toFixed(6)) + " " + Number(ducking.sidechainMix.toFixed(6)) + "\'[ducked_music]"
  ];

  const mixInputs = ["[voice]", "[ducked_music]"];
  soundEffects.forEach((effect, index) => {
    const inputIndex = index + 2;
    const delayMs = Math.round(effect.offsetSeconds * 1000);
    const outputLabel = "sfx" + index;
    filters.push(
      "[" + inputIndex + ":a]aresample=" + SAMPLE_RATE +
      ",aformat=sample_fmts=fltp:channel_layouts=" + CHANNEL_LAYOUT +
      ",volume=" + effect.gainDb + "dB,adelay=" + delayMs +
      ":all=1,apad,atrim=duration=" + duration +
      ",asetpts=PTS-STARTPTS[" + outputLabel + "]"
    );
    mixInputs.push("[" + outputLabel + "]");
  });

  filters.push(
    mixInputs.join("") + "amix=inputs=" + mixInputs.length +
    ":duration=longest:dropout_transition=0:normalize=0" +
    ",alimiter=limit=0.95,atrim=duration=" + duration +
    ",asetpts=PTS-STARTPTS[mixed]"
  );

  const args = [
    "-hide_banner", "-nostdin", "-n", "-loglevel", "error",
    "-i", voiceoverPath,
    "-stream_loop", "-1", "-i", musicPath
  ];
  for (const effect of soundEffects) args.push("-i", effect.path);
  args.push(
    "-filter_complex", filters.join(";"),
    "-map", "[mixed]",
    "-map_metadata", "-1",
    "-ar", String(SAMPLE_RATE),
    "-ac", String(CHANNELS),
    "-c:a", "pcm_s16le",
    "-t", duration,
    "-f", "wav",
    outputPath
  );
  return args;
}

/**
 * Mix local audio assets into one PCM WAV whose target duration is read from
 * the source video's video stream. A short music bed is looped to cover the
 * video; sidechain compression lowers it under the voiceover.
 *
 * The output is retained on disk even when its checks fail. No input is
 * modified, and no media is uploaded, delivered, published or deleted.
 */
export async function mixAudioForVideo(options) {
  assertCondition(options !== null && typeof options === "object" && !Array.isArray(options),
    "options must be an object");
  const soundEffectsInput = validateSoundEffects(options.soundEffects ?? []);
  const ducking = validateDucking(options.ducking);
  const ffmpegPath = options.ffmpegPath ?? "ffmpeg";
  const ffprobePath = options.ffprobePath ?? "ffprobe";
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  assertCondition(typeof ffmpegPath === "string" && ffmpegPath.length > 0,
    "ffmpegPath must be a non-empty command");
  assertCondition(typeof ffprobePath === "string" && ffprobePath.length > 0,
    "ffprobePath must be a non-empty command");
  assertCondition(Number.isInteger(timeoutMs) && timeoutMs >= 1000 && timeoutMs <= 600_000,
    "timeoutMs must be an integer between 1000 and 600000");

  const video = await resolveReadableFile(options.videoPath, "videoPath");
  const voiceover = await resolveReadableFile(options.voiceoverPath, "voiceoverPath");
  const music = await resolveReadableFile(options.musicPath, "musicPath");
  const outputPath = await resolveNewWaveFile(options.outputPath);
  const soundEffects = [];
  for (const effect of soundEffectsInput) {
    const file = await resolveReadableFile(effect.path, "soundEffects path");
    soundEffects.push({ ...effect, path: file.path });
  }

  const videoProbeResult = await runCommand(ffprobePath, [
    "-v", "error",
    "-show_entries", "stream=codec_type,duration,duration_ts,time_base:format=duration",
    "-of", "json",
    video.path
  ], { timeoutMs });
  const videoProbe = parseJsonOutput(videoProbeResult, "ffprobe");
  const videoDurationSeconds = getVideoDurationSeconds(videoProbe);
  assertCondition(videoDurationSeconds <= DEFAULT_LIMITS.maxDurationSeconds,
    "video duration exceeds the existing Video QA maximum of " +
      DEFAULT_LIMITS.maxDurationSeconds + " seconds");
  for (let index = 0; index < soundEffects.length; index += 1) {
    assertCondition(soundEffects[index].offsetSeconds < videoDurationSeconds,
      "soundEffects[" + index + "].offsetSeconds must be shorter than the video duration");
  }

  const ffmpegArgs = buildAudioMixerArgs({
    voiceoverPath: voiceover.path,
    musicPath: music.path,
    soundEffects,
    outputPath,
    videoDurationSeconds,
    ducking
  });
  await runCommand(ffmpegPath, ffmpegArgs, { timeoutMs });

  const outputProbeResult = await runCommand(ffprobePath, [
    "-v", "error",
    "-show_entries", "stream=codec_type,codec_name,sample_rate,channels,duration:format=duration",
    "-of", "json",
    outputPath
  ], { timeoutMs });
  const outputProbe = parseJsonOutput(outputProbeResult, "ffprobe");
  const audioStream = (outputProbe.streams ?? [])
    .find(stream => stream.codec_type === "audio");
  const outputDurationSeconds = finiteNumber(audioStream?.duration) ??
    finiteNumber(outputProbe.format?.duration);
  const avDurationDifferenceSeconds = outputDurationSeconds === null
    ? null
    : Math.abs(videoDurationSeconds - outputDurationSeconds);

  const silenceResult = await runCommand(ffmpegPath, [
    "-hide_banner", "-nostdin", "-nostats",
    "-i", outputPath,
    "-map", "0:a:0",
    "-af", "silencedetect=noise=" + DEFAULT_LIMITS.silenceNoise +
      ":d=" + DEFAULT_LIMITS.silenceDetectMinSeconds,
    "-vn", "-f", "null", "-"
  ], { timeoutMs });
  const silenceSegments = parseSilenceSegments(
    silenceResult.stderr,
    outputDurationSeconds
  );
  const maxSilenceSegmentSeconds = silenceSegments.reduce(
    (maximum, segment) => Math.max(maximum, segment.durationSeconds), 0
  );

  const checks = [
    {
      name: "wav_codec",
      value: audioStream?.codec_name ?? null,
      limit: "pcm_s16le",
      unit: "codec",
      status: audioStream?.codec_name === "pcm_s16le" ? "pass" : "fail"
    },
    {
      name: "sample_rate",
      value: finiteNumber(audioStream?.sample_rate),
      limit: SAMPLE_RATE,
      unit: "Hz",
      status: Number(audioStream?.sample_rate) === SAMPLE_RATE ? "pass" : "fail"
    },
    {
      name: "channels",
      value: finiteNumber(audioStream?.channels),
      limit: CHANNELS,
      unit: "channels",
      status: Number(audioStream?.channels) === CHANNELS ? "pass" : "fail"
    },
    {
      name: "duration_matches_video",
      value: avDurationDifferenceSeconds,
      limit: DEFAULT_LIMITS.maxAvDurationDifferenceSeconds,
      unit: "seconds",
      status: avDurationDifferenceSeconds !== null &&
        avDurationDifferenceSeconds <= DEFAULT_LIMITS.maxAvDurationDifferenceSeconds
        ? "pass"
        : "fail"
    },
    {
      name: "continuous_audio",
      value: maxSilenceSegmentSeconds,
      limit: DEFAULT_LIMITS.maxSilenceSegmentSeconds,
      unit: "seconds",
      status: maxSilenceSegmentSeconds <= DEFAULT_LIMITS.maxSilenceSegmentSeconds
        ? "pass"
        : "fail",
      details: { segments: silenceSegments, noiseThreshold: DEFAULT_LIMITS.silenceNoise }
    }
  ];
  const passed = checks.every(check => check.status === "pass");

  return {
    schemaVersion: 1,
    status: passed ? "pass" : "fail",
    passed,
    sourceVideo: {
      path: video.path,
      sizeBytes: video.sizeBytes,
      durationSeconds: videoDurationSeconds
    },
    inputs: {
      voiceoverPath: voiceover.path,
      musicPath: music.path,
      musicLoopedToVideoDuration: true,
      soundEffects: soundEffects.map(effect => ({
        path: effect.path,
        offsetSeconds: effect.offsetSeconds,
        gainDb: effect.gainDb
      }))
    },
    output: {
      path: outputPath,
      codec: audioStream?.codec_name ?? null,
      sampleRate: finiteNumber(audioStream?.sample_rate),
      channels: finiteNumber(audioStream?.channels),
      durationSeconds: outputDurationSeconds
    },
    ducking: {
      ...ducking,
      implementation: "FFmpeg sidechaincompress",
      sidechain: "voiceover",
      compressedSignalMix: ducking.sidechainMix,
      dryMusicSignalMix: 1 - ducking.sidechainMix
    },
    measurements: {
      avDurationDifferenceSeconds,
      maxSilenceSegmentSeconds,
      silenceSegments
    },
    limits: {
      maxAvDurationDifferenceSeconds: DEFAULT_LIMITS.maxAvDurationDifferenceSeconds,
      maxSilenceSegmentSeconds: DEFAULT_LIMITS.maxSilenceSegmentSeconds,
      silenceNoise: DEFAULT_LIMITS.silenceNoise,
      silenceDetectMinSeconds: DEFAULT_LIMITS.silenceDetectMinSeconds
    },
    checks
  };
}

export {
  DEFAULT_DUCKING,
  SAMPLE_RATE,
  CHANNELS
};

import { spawn } from "node:child_process";
import { access, realpath, stat } from "node:fs/promises";
import { constants as fsConstants } from "node:fs";

const DEFAULT_LIMITS = Object.freeze({
  maxBlackSegmentSeconds: 0.25,
  maxSilenceSegmentSeconds: 0.5,
  maxAvDurationDifferenceSeconds: 0.15,
  maxDurationSeconds: 300,
  maxFileSizeBytes: 512 * 1024 * 1024,
  blackDetectMinSeconds: 0.02,
  silenceDetectMinSeconds: 0.02,
  blackPixelThreshold: 0.10,
  blackPictureThreshold: 0.98,
  silenceNoise: "-50dB"
});
const DEFAULT_TIMEOUT_MS = 120_000;
const MAX_OUTPUT_CHARS = 64 * 1024;

function assertCondition(condition, message) {
  if (!condition) throw new TypeError(message);
}

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function validateLimits(input = {}) {
  assertCondition(input !== null && typeof input === "object" && !Array.isArray(input),
    "limits must be an object");
  const limits = { ...DEFAULT_LIMITS, ...input };

  for (const key of [
    "maxBlackSegmentSeconds",
    "maxSilenceSegmentSeconds",
    "maxAvDurationDifferenceSeconds",
    "blackDetectMinSeconds",
    "silenceDetectMinSeconds"
  ]) {
    assertCondition(Number.isFinite(limits[key]) && limits[key] >= 0,
      key + " must be a finite non-negative number");
  }
  for (const key of ["maxDurationSeconds", "maxFileSizeBytes"]) {
    assertCondition(Number.isFinite(limits[key]) && limits[key] > 0,
      key + " must be a finite positive number");
  }
  for (const key of ["blackPixelThreshold", "blackPictureThreshold"]) {
    assertCondition(Number.isFinite(limits[key]) && limits[key] > 0 && limits[key] <= 1,
      key + " must be greater than 0 and at most 1");
  }
  assertCondition(limits.blackDetectMinSeconds <= limits.maxBlackSegmentSeconds,
    "blackDetectMinSeconds must not exceed maxBlackSegmentSeconds");
  assertCondition(limits.silenceDetectMinSeconds <= limits.maxSilenceSegmentSeconds,
    "silenceDetectMinSeconds must not exceed maxSilenceSegmentSeconds");
  assertCondition(typeof limits.silenceNoise === "string" &&
    /^-?\d+(?:\.\d+)?dB$/i.test(limits.silenceNoise),
    "silenceNoise must be a decibel value such as -50dB");

  return limits;
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
      stdout = (stdout + chunk.toString()).slice(-MAX_OUTPUT_CHARS);
    });
    child.stderr.on("data", chunk => {
      stderr = (stderr + chunk.toString()).slice(-MAX_OUTPUT_CHARS);
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
        finish(reject, new Error(
          command + " exited with code " + String(code) +
          (signal ? " (" + signal + ")" : "") +
          (stderr.trim() ? ": " + stderr.trim() : "")
        ));
      } else {
        finish(resolve, { code, signal, stdout, stderr });
      }
    });
  });
}

async function resolveMediaFile(inputPath) {
  assertCondition(typeof inputPath === "string" && inputPath.trim().length > 0,
    "inputPath must be a non-empty path");
  const absolutePath = await realpath(inputPath);
  await access(absolutePath, fsConstants.R_OK);
  const fileInfo = await stat(absolutePath);
  assertCondition(fileInfo.isFile(), "inputPath must be a regular file");
  assertCondition(fileInfo.size > 0, "inputPath must not be empty");
  return { path: absolutePath, sizeBytes: fileInfo.size };
}

function parseJsonOutput(result, command) {
  const output = typeof result.stdout === "string"
    ? result.stdout
    : Buffer.from(result.stdout ?? "").toString("utf8");
  try {
    return JSON.parse(output);
  } catch {
    throw new Error(command + " returned invalid JSON");
  }
}

function parseBlackSegments(stderr = "") {
  const segments = [];
  const pattern = /black_start:\s*(-?\d+(?:\.\d+)?)\s+black_end:\s*(-?\d+(?:\.\d+)?)\s+black_duration:\s*(\d+(?:\.\d+)?)/g;
  let match;
  while ((match = pattern.exec(stderr)) !== null) {
    segments.push({
      startSeconds: Number(match[1]),
      endSeconds: Number(match[2]),
      durationSeconds: Number(match[3])
    });
  }
  return segments;
}

function parseSilenceSegments(stderr = "", audioDurationSeconds = null) {
  const segments = [];
  let pendingStart = null;

  for (const line of stderr.split(/\r?\n/)) {
    const startMatch = line.match(/silence_start:\s*(-?\d+(?:\.\d+)?)/);
    if (startMatch) pendingStart = Number(startMatch[1]);

    const endMatch = line.match(/silence_end:\s*(-?\d+(?:\.\d+)?)/);
    if (endMatch && pendingStart !== null) {
      const durationMatch = line.match(/silence_duration:\s*(\d+(?:\.\d+)?)/);
      const endSeconds = Number(endMatch[1]);
      const durationSeconds = durationMatch
        ? Number(durationMatch[1])
        : Math.max(0, endSeconds - pendingStart);
      segments.push({
        startSeconds: pendingStart,
        endSeconds,
        durationSeconds
      });
      pendingStart = null;
    }
  }

  // silencedetect omits silence_end for silence extending to EOF.
  if (pendingStart !== null && Number.isFinite(audioDurationSeconds)) {
    segments.push({
      startSeconds: pendingStart,
      endSeconds: audioDurationSeconds,
      durationSeconds: Math.max(0, audioDurationSeconds - pendingStart)
    });
  }
  return segments;
}

function makeCheck(name, value, limit, pass, unit, details) {
  return {
    name,
    value,
    limit,
    unit,
    status: pass ? "pass" : "fail",
    ...(details === undefined ? {} : { details })
  };
}

/**
 * Inspect a local video file with ffprobe, FFmpeg blackdetect and silencedetect.
 * The function is read-only: it does not modify the media file or call external services.
 */
export async function inspectVideoQuality(options) {
  assertCondition(options !== null && typeof options === "object" && !Array.isArray(options),
    "options must be an object");

  const media = await resolveMediaFile(options.inputPath);
  const limits = validateLimits(options.limits);
  const ffmpegPath = options.ffmpegPath ?? "ffmpeg";
  const ffprobePath = options.ffprobePath ?? "ffprobe";
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const runProcess = options.runProcess ?? runCommand;

  assertCondition(typeof ffmpegPath === "string" && ffmpegPath.length > 0,
    "ffmpegPath must be a non-empty command");
  assertCondition(typeof ffprobePath === "string" && ffprobePath.length > 0,
    "ffprobePath must be a non-empty command");
  assertCondition(Number.isInteger(timeoutMs) && timeoutMs >= 1000 && timeoutMs <= 600_000,
    "timeoutMs must be an integer between 1000 and 600000");
  assertCondition(typeof runProcess === "function", "runProcess must be a function");

  const probeResult = await runProcess(ffprobePath, [
    "-v", "error",
    "-show_entries",
    "stream=index,codec_type,codec_name,width,height,duration,r_frame_rate,avg_frame_rate:format=duration,size",
    "-of", "json",
    media.path
  ], { timeoutMs });
  const probe = parseJsonOutput(probeResult, "ffprobe");
  const streams = Array.isArray(probe.streams) ? probe.streams : [];
  const videoStream = streams.find(stream => stream.codec_type === "video") ?? null;
  const audioStream = streams.find(stream => stream.codec_type === "audio") ?? null;

  const formatDurationSeconds = finiteNumber(probe.format?.duration);
  const videoDurationSeconds = videoStream
    ? finiteNumber(videoStream.duration) ?? formatDurationSeconds
    : null;
  const audioDurationSeconds = audioStream
    ? finiteNumber(audioStream.duration) ?? formatDurationSeconds
    : null;
  const mediaDurationSeconds = formatDurationSeconds ??
    (videoDurationSeconds === null ? null : videoDurationSeconds);
  const avDurationDifferenceSeconds =
    videoDurationSeconds !== null && audioDurationSeconds !== null
      ? Math.abs(videoDurationSeconds - audioDurationSeconds)
      : null;

  let blackdetectStderr = "";
  let silencedetectStderr = "";
  let blackSegments = [];
  let silenceSegments = [];

  if (videoStream) {
    const blackResult = await runProcess(ffmpegPath, [
      "-hide_banner", "-nostats",
      "-i", media.path,
      "-map", "0:v:0",
      "-vf", "blackdetect=d=" + limits.blackDetectMinSeconds +
        ":pix_th=" + limits.blackPixelThreshold +
        ":pic_th=" + limits.blackPictureThreshold,
      "-an", "-f", "null", "-"
    ], { timeoutMs });
    blackdetectStderr = blackResult.stderr ?? "";
    blackSegments = parseBlackSegments(blackdetectStderr);
  }

  if (audioStream) {
    const silenceResult = await runProcess(ffmpegPath, [
      "-hide_banner", "-nostats",
      "-i", media.path,
      "-map", "0:a:0",
      "-af", "silencedetect=noise=" + limits.silenceNoise +
        ":d=" + limits.silenceDetectMinSeconds,
      "-vn", "-f", "null", "-"
    ], { timeoutMs });
    silencedetectStderr = silenceResult.stderr ?? "";
    silenceSegments = parseSilenceSegments(silencedetectStderr, audioDurationSeconds);
  }

  const maxBlackSegmentSeconds = blackSegments.reduce(
    (maximum, segment) => Math.max(maximum, segment.durationSeconds), 0
  );
  const maxSilenceSegmentSeconds = silenceSegments.reduce(
    (maximum, segment) => Math.max(maximum, segment.durationSeconds), 0
  );

  const checks = [
    makeCheck(
      "black_frames",
      videoStream ? maxBlackSegmentSeconds : null,
      limits.maxBlackSegmentSeconds,
      Boolean(videoStream) && maxBlackSegmentSeconds <= limits.maxBlackSegmentSeconds,
      "seconds",
      { segments: blackSegments, detector: "blackdetect" }
    ),
    makeCheck(
      "silence",
      audioStream ? maxSilenceSegmentSeconds : null,
      limits.maxSilenceSegmentSeconds,
      Boolean(audioStream) && maxSilenceSegmentSeconds <= limits.maxSilenceSegmentSeconds,
      "seconds",
      { segments: silenceSegments, detector: "silencedetect" }
    ),
    makeCheck(
      "av_duration_difference",
      avDurationDifferenceSeconds,
      limits.maxAvDurationDifferenceSeconds,
      avDurationDifferenceSeconds !== null &&
        avDurationDifferenceSeconds <= limits.maxAvDurationDifferenceSeconds,
      "seconds",
      { videoDurationSeconds, audioDurationSeconds }
    ),
    makeCheck(
      "maximum_duration",
      mediaDurationSeconds,
      limits.maxDurationSeconds,
      mediaDurationSeconds !== null && mediaDurationSeconds <= limits.maxDurationSeconds,
      "seconds"
    ),
    makeCheck(
      "maximum_file_size",
      media.sizeBytes,
      limits.maxFileSizeBytes,
      media.sizeBytes <= limits.maxFileSizeBytes,
      "bytes"
    ),
    makeCheck(
      "video_stream_present",
      Boolean(videoStream),
      true,
      Boolean(videoStream),
      "boolean"
    ),
    makeCheck(
      "audio_stream_present",
      Boolean(audioStream),
      true,
      Boolean(audioStream),
      "boolean"
    ),
    makeCheck(
      "video_codec",
      videoStream?.codec_name ?? null,
      "h264",
      videoStream?.codec_name === "h264",
      "codec"
    ),
    makeCheck(
      "audio_codec",
      audioStream?.codec_name ?? null,
      "aac",
      audioStream?.codec_name === "aac",
      "codec"
    )
  ];

  const passed = checks.every(check => check.status === "pass");
  return {
    schemaVersion: 1,
    file: {
      path: media.path,
      sizeBytes: media.sizeBytes
    },
    measurements: {
      durationSeconds: mediaDurationSeconds,
      videoDurationSeconds,
      audioDurationSeconds,
      avDurationDifferenceSeconds,
      width: videoStream?.width ?? null,
      height: videoStream?.height ?? null,
      videoCodec: videoStream?.codec_name ?? null,
      audioCodec: audioStream?.codec_name ?? null
    },
    limits,
    checks,
    passed,
    status: passed ? "pass" : "fail",
    evidence: {
      ffprobe: probe,
      blackdetectStderr,
      silencedetectStderr
    }
  };
}

export {
  DEFAULT_LIMITS,
  parseBlackSegments,
  parseSilenceSegments,
  validateLimits
};

#!/usr/bin/env node
import { createHash } from 'node:crypto';
import { readFile, readdir, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { mixAudioForVideo } from '../src/production/audio-mixer.js';
import { inspectVideoQuality } from '../src/production/video-qa.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const mediaDir = path.resolve(root, process.argv[2] ?? 'build/characters-v1');
const phase = process.argv[3] ?? 'all';
const inputDir = path.join(root, 'assets/audio');
const chunkDir = path.join(inputDir, 'chunks');
const rawVideo = path.join(mediaDir, 'characters-video-raw.mp4');
const voiceTrack = path.join(mediaDir, 'episode-audio-trimmed.wav');
const silentBed = path.join(mediaDir, 'digital-silence-bed.wav');
const outputMix = path.join(mediaDir, 'episode-audio-mix.wav');
const outputMp4 = path.join(mediaDir, 'character-episode-v2.mp4');
const audioReportPath = path.join(mediaDir, 'audio-mix-qa-report.json');
const videoReportPath = path.join(mediaDir, 'video-qa-report.json');
const reportPath = path.join(mediaDir, 'audio-video-qa-report.json');

function run(command, args, timeoutMs = 180_000) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { shell: false, stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '', stderr = '';
    const timer = setTimeout(() => child.kill('SIGKILL'), timeoutMs);
    child.stdout.on('data', chunk => { stdout = (stdout + chunk).slice(-16_384); });
    child.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-16_384); });
    child.on('error', error => { clearTimeout(timer); reject(error); });
    child.on('close', code => {
      clearTimeout(timer);
      if (code === 0) resolve({ stdout, stderr });
      else reject(new Error(`${command} exited ${code}: ${stderr.slice(-4000)}`));
    });
  });
}

async function sha256(file) {
  return createHash('sha256').update(await readFile(file)).digest('hex');
}

function ensure(condition, message) {
  if (!condition) throw new Error(message);
}

async function verifyApprovedSources() {
  const manifestPath = path.join(inputDir, 'manifest.json');
  const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
  ensure(manifest.status === 'complete' && manifest.dryRun === false,
    'Source manifest is not complete');
  ensure(manifest.episodeId === 'episode-pilot-1', 'Unexpected source episode');
  ensure(Array.isArray(manifest.completedChunks) && manifest.completedChunks.length === 8,
    'Expected exactly eight approved WAV chunks');

  const expectedNames = new Set(manifest.completedChunks.map(chunk => path.basename(chunk.wav.path)));
  const actualNames = new Set();
  for (const name of await readdir(chunkDir)) {
    if ((await stat(path.join(chunkDir, name))).isFile()) actualNames.add(name);
  }
  ensure(actualNames.size === 8 && [...actualNames].every(name => expectedNames.has(name)),
    'Audio chunk folder must contain only the eight manifest-approved WAV files');

  const sourceFingerprints = [];
  for (const chunk of manifest.completedChunks) {
    const source = path.join(inputDir, chunk.wav.path);
    const actual = await sha256(source);
    ensure(actual === chunk.wav.sha256, `Source WAV hash mismatch: ${path.basename(source)}`);
    sourceFingerprints.push({
      file: chunk.wav.path,
      sha256: actual,
      sizeBytes: (await stat(source)).size
    });
  }
  return {
    manifestSha256: await sha256(manifestPath),
    sourceFingerprints
  };
}

async function requireFile(file, label) {
  const info = await stat(file);
  ensure(info.isFile() && info.size > 0, `${label} is missing or empty: ${path.basename(file)}`);
  return info;
}

async function runAudioMixPhase() {
  const sources = await verifyApprovedSources();
  await requireFile(rawVideo, 'Raw H.264 video');
  await requireFile(voiceTrack, 'Prepared voice track');

  // The existing mixer requires a music-bed input. This file is digital silence:
  // no music or SFX are introduced into the children's episode.
  await run('ffmpeg', ['-hide_banner', '-nostdin', '-loglevel', 'error', '-y',
    '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', '1', '-ar', '48000', '-ac', '2',
    '-c:a', 'pcm_s16le', silentBed]);

  const audioMix = await mixAudioForVideo({
    videoPath: rawVideo,
    voiceoverPath: voiceTrack,
    musicPath: silentBed,
    outputPath: outputMix,
    soundEffects: []
  });

  // Audio Mix QA owns the final mux; it does not run Video QA.
  await run('ffmpeg', ['-hide_banner', '-nostdin', '-loglevel', 'error', '-y',
    '-i', rawVideo, '-i', outputMix,
    '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '128k',
    '-ar', '48000', '-ac', '2', '-shortest', '-movflags', '+faststart', outputMp4]);

  const finalVideo = await requireFile(outputMp4, 'Final mixed MP4');
  const report = {
    status: audioMix.status,
    versionName: 'نسخة شخصيات v2',
    sourceManifestSha256: sources.manifestSha256,
    sourceWavs: sources.sourceFingerprints,
    audioMix,
    musicAndEffects: 'none; required mixer bed is digital silence',
    output: {
      rawVideoPath: path.basename(rawVideo),
      rawVideoSha256: await sha256(rawVideo),
      outputMixPath: path.basename(outputMix),
      outputMixSha256: await sha256(outputMix),
      outputMixSizeBytes: (await requireFile(outputMix, 'Mixed WAV')).size,
      path: path.basename(outputMp4),
      sha256: await sha256(outputMp4),
      sizeBytes: finalVideo.size
    }
  };
  await writeFile(audioReportPath, JSON.stringify(report, null, 2) + '\n', 'utf8');
  console.log(JSON.stringify({
    phase: 'audio-mix',
    status: report.status,
    output: report.output,
    report: audioReportPath
  }, null, 2));
  if (report.status !== 'pass') process.exitCode = 1;
  return report;
}

async function runVideoQaPhase() {
  await requireFile(outputMp4, 'Final mixed MP4');
  const audioReport = JSON.parse(await readFile(audioReportPath, 'utf8'));
  ensure(audioReport.status === 'pass', 'Audio Mix QA report is not passing');
  const videoQa = await inspectVideoQuality({ inputPath: outputMp4 });
  const sourceWavsUnmodified = [];
  for (const source of audioReport.sourceWavs) {
    const sourcePath = path.join(inputDir, source.file);
    const actual = await sha256(sourcePath);
    sourceWavsUnmodified.push({ file: source.file, expectedSha256: source.sha256, actualSha256: actual });
  }
  const sourcesUnmodified = sourceWavsUnmodified.every(item => item.expectedSha256 === item.actualSha256);
  const finalVideo = await requireFile(outputMp4, 'Final mixed MP4');
  const report = {
    status: videoQa.status === 'pass' && sourcesUnmodified ? 'pass' : 'fail',
    versionName: audioReport.versionName,
    sourceManifestSha256: audioReport.sourceManifestSha256,
    sourceWavs: audioReport.sourceWavs,
    sourceWavsUnmodified: sourcesUnmodified,
    sourceWavRecheck: sourceWavsUnmodified,
    audioMix: audioReport.audioMix,
    videoQa,
    musicAndEffects: audioReport.musicAndEffects,
    output: {
      ...audioReport.output,
      path: path.basename(outputMp4),
      sha256: await sha256(outputMp4),
      sizeBytes: finalVideo.size
    }
  };
  await writeFile(videoReportPath, JSON.stringify(videoQa, null, 2) + '\n', 'utf8');
  await writeFile(reportPath, JSON.stringify(report, null, 2) + '\n', 'utf8');
  console.log(JSON.stringify({
    phase: 'video-qa',
    status: report.status,
    audioMixStatus: audioReport.status,
    videoQaStatus: videoQa.status,
    sourceWavsUnmodified: sourcesUnmodified,
    output: report.output,
    report: reportPath
  }, null, 2));
  if (report.status !== 'pass') process.exitCode = 1;
  return report;
}

async function main() {
  if (!['all', 'audio-mix', 'video-qa'].includes(phase)) {
    throw new Error('phase must be all, audio-mix, or video-qa');
  }
  if (phase === 'audio-mix') {
    await runAudioMixPhase();
    return;
  }
  if (phase === 'video-qa') {
    await runVideoQaPhase();
    return;
  }
  const audio = await runAudioMixPhase();
  if (audio.status !== 'pass') return;
  await runVideoQaPhase();
}

main().catch(error => {
  console.error(error.stack || String(error));
  process.exitCode = 1;
});

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
const inputDir = path.join(root, 'assets/audio');
const chunkDir = path.join(inputDir, 'chunks');
const outputMp4 = path.join(mediaDir, 'character-episode-v2.mp4');
const outputMix = path.join(mediaDir, 'episode-audio-mix.wav');
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

async function main() {
  const manifest = JSON.parse(await readFile(path.join(inputDir, 'manifest.json'), 'utf8'));
  ensure(manifest.status === 'complete' && manifest.dryRun === false, 'Source manifest is not complete');
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
    sourceFingerprints.push({ file: chunk.wav.path, sha256: actual, sizeBytes: (await stat(source)).size });
  }

  const rawVideo = path.join(mediaDir, 'characters-video-raw.mp4');
  const voiceTrack = path.join(mediaDir, 'episode-audio-trimmed.wav');
  const silentBed = path.join(mediaDir, 'digital-silence-bed.wav');
  ensure((await stat(rawVideo)).isFile(), 'Raw H.264 video is missing');
  ensure((await stat(voiceTrack)).isFile(), 'Prepared voice track is missing');

  // The existing mixer requires a music-bed input. This file is digital silence;
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

  await run('ffmpeg', ['-hide_banner', '-nostdin', '-loglevel', 'error', '-y',
    '-i', rawVideo, '-i', outputMix,
    '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '128k',
    '-ar', '48000', '-ac', '2', '-shortest', '-movflags', '+faststart', outputMp4]);

  const videoQa = await inspectVideoQuality({ inputPath: outputMp4 });
  const afterFingerprints = [];
  for (const chunk of manifest.completedChunks) {
    const source = path.join(inputDir, chunk.wav.path);
    const actual = await sha256(source);
    ensure(actual === chunk.wav.sha256, `Source WAV changed during QA: ${path.basename(source)}`);
    afterFingerprints.push({ file: chunk.wav.path, sha256: actual });
  }

  const report = {
    status: audioMix.status === 'pass' && videoQa.status === 'pass' ? 'pass' : 'fail',
    versionName: 'نسخة شخصيات v2',
    sourceManifestSha256: await sha256(path.join(inputDir, 'manifest.json')),
    sourceWavs: sourceFingerprints,
    sourceWavsUnmodified: JSON.stringify(sourceFingerprints.map(x => x.sha256)) ===
      JSON.stringify(afterFingerprints.map(x => x.sha256)),
    audioMix,
    videoQa,
    musicAndEffects: 'none; required mixer bed is digital silence',
    output: {
      path: path.basename(outputMp4),
      sha256: await sha256(outputMp4),
      sizeBytes: (await stat(outputMp4)).size
    }
  };
  await writeFile(reportPath, JSON.stringify(report, null, 2) + '\n', 'utf8');
  console.log(JSON.stringify({
    status: report.status,
    audioMixStatus: audioMix.status,
    videoQaStatus: videoQa.status,
    output: report.output,
    report: reportPath
  }, null, 2));
  if (report.status !== 'pass') process.exitCode = 1;
}

main().catch(error => {
  console.error(error.stack || String(error));
  process.exitCode = 1;
});

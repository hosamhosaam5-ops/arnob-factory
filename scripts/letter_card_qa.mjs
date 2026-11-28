#!/usr/bin/env node
import { createHash } from 'node:crypto';
import { readFile, writeFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { mixAudioForVideo } from '../src/production/audio-mixer.js';
import { DEFAULT_LIMITS, inspectVideoQuality } from '../src/production/video-qa.js';

const [generationArg, renderArg, phase='all'] = process.argv.slice(2);
if (!generationArg || !renderArg || !['all','audio-mix','video-qa'].includes(phase)) {
  throw new Error('usage: letter_card_qa.mjs GENERATION_DIR RENDER_DIR [audio-mix|video-qa|all]');
}
const generationDir=path.resolve(generationArg), renderDir=path.resolve(renderArg);
const manifestPath=path.join(generationDir,'manifest.json');
const renderReportPath=path.join(renderDir,'render-report.json');
const rawVideoDefault=path.join(renderDir,'letter-card-video-raw.mp4');
const voiceTrackDefault=path.join(renderDir,'episode-audio.wav');
const silentBed=path.join(renderDir,'digital-silence-bed.wav');
const mixedWav=path.join(renderDir,'episode-audio-mix.wav');
const finalVideo=path.join(renderDir,'letter-card-episode.mp4');
const audioReportPath=path.join(renderDir,'audio-mix-qa-report.json');
const videoReportPath=path.join(renderDir,'video-qa-report.json');
const combinedReportPath=path.join(renderDir,'audio-video-qa-report.json');

function ensure(value,message){if(!value)throw new Error(message);}

function printFailedChecks(phase, result) {
 const checks = Array.isArray(result?.checks) ? result.checks : [];
 const failed = checks.filter(check => check?.status === 'fail');
 console.error(JSON.stringify({
  phase,
  status: result?.status ?? 'fail',
  failedCheckCount: failed.length,
  failedChecks: failed.map(check => ({
   name: check.name,
   value: check.value ?? null,
   limit: check.limit ?? null,
   unit: check.unit ?? null,
   details: check.details ?? null
  })),
  allChecks: checks,
  limits: result?.limits ?? null
 }, null, 2));
}
async function sha(file){return createHash('sha256').update(await readFile(file)).digest('hex');}
async function requireFile(file,label){const info=await stat(file);ensure(info.isFile()&&info.size>0,label+' is missing or empty');return info;}
function run(command,args,timeoutMs=180000){
 return new Promise((resolve,reject)=>{
  const child=spawn(command,args,{shell:false,stdio:['ignore','pipe','pipe']});
  let stderr=''; const timer=setTimeout(()=>child.kill('SIGKILL'),timeoutMs);
  child.stderr.on('data',chunk=>{stderr=(stderr+chunk.toString()).slice(-12000);});
  child.once('error',error=>{clearTimeout(timer);reject(error);});
  child.once('close',code=>{clearTimeout(timer);if(code===0)resolve();else reject(new Error(command+' exited '+code+': '+stderr));});
 });
}
async function verifySources(){
 const manifest=JSON.parse(await readFile(manifestPath,'utf8'));
 ensure(manifest.status==='complete'&&manifest.dryRun===false&&manifest.modelInferenceInvoked===true,'SILMA manifest is not a completed real generation');
 ensure(typeof manifest.episodeId==='string'&&Array.isArray(manifest.completedChunks)&&manifest.completedChunks.length>=1&&manifest.completedChunks.length<=16,'Expected one to sixteen completed episode chunks');
 const files=[];
 for(const chunk of manifest.completedChunks){
  for(const key of ['wav','mp3']){
   const item=chunk[key], file=path.resolve(generationDir,item.path);
   ensure(file.startsWith(generationDir+path.sep),'Unsafe generated media path');
   ensure(await sha(file)===item.sha256,'Generated '+key+' SHA mismatch for '+chunk.chunkId);
   files.push({chunkId:chunk.chunkId,media:key,path:item.path,sha256:item.sha256});
  }
 }
 return {manifest,files,manifestSha256:await sha(manifestPath)};
}
async function audioMix(){
 const sources=await verifySources();
 const renderReport=JSON.parse(await readFile(renderReportPath,'utf8'));
 const rawVideoName=renderReport.rawVideo?.path ?? renderReport.picture?.file ?? path.basename(rawVideoDefault);
 const voiceTrackName=renderReport.voiceTrack?.path ?? renderReport.audioProcessing?.voiceTrackPath ?? path.basename(voiceTrackDefault);
 const rawVideo=path.resolve(renderDir,rawVideoName);
 const voiceTrack=path.resolve(renderDir,voiceTrackName);
 ensure(rawVideo.startsWith(renderDir+path.sep)&&voiceTrack.startsWith(renderDir+path.sep),'Unsafe render-report media path');
 await requireFile(rawVideo,'Raw rendered H.264 video');
 await requireFile(voiceTrack,'Rendered voice track');
 await run('ffmpeg',['-hide_banner','-nostdin','-loglevel','error','-y','-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t','1','-ar','48000','-ac','2','-c:a','pcm_s16le',silentBed]);
 const audioMix=await mixAudioForVideo({videoPath:rawVideo,voiceoverPath:voiceTrack,musicPath:silentBed,outputPath:mixedWav,soundEffects:[]});
 if(audioMix.status!=='pass') printFailedChecks('audio-mix',audioMix);
 ensure(audioMix.status==='pass','Audio Mix QA failed');
 await run('ffmpeg',['-hide_banner','-nostdin','-loglevel','error','-y','-i',rawVideo,'-i',mixedWav,
  '-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','128k','-ar','48000','-ac','2',
  '-shortest','-movflags','+faststart',finalVideo],300000);
 const finalInfo=await requireFile(finalVideo,'Final MP4');
 const report={status:'pass',episodeScriptSha256:sources.manifest.episodeScriptSha256,
  sourceManifestSha256:sources.manifestSha256,sourceMediaFingerprints:sources.files,
  audioMix,musicAndEffects:'none; digital silence bed only',
  output:{path:path.basename(finalVideo),sha256:await sha(finalVideo),sizeBytes:finalInfo.size}};
 await writeFile(audioReportPath,JSON.stringify(report,null,2)+'\n','utf8');
 console.log(JSON.stringify({phase:'audio-mix',status:report.status,output:report.output},null,2));
}
async function videoQa(){
 ensure(DEFAULT_LIMITS.maxSilenceSegmentSeconds===0.5,'Protected maximum silence threshold must remain 0.5 seconds');
 const audio=JSON.parse(await readFile(audioReportPath,'utf8'));
 ensure(audio.status==='pass','Audio Mix QA must pass before Video QA');
 await requireFile(finalVideo,'Final MP4');
 const videoQa=await inspectVideoQuality({inputPath:finalVideo});
 const sources=await verifySources();
 const sourceMediaUnmodified=audio.sourceMediaFingerprints.every(old=>sources.files.some(now=>now.path===old.path&&now.sha256===old.sha256));
 const status=videoQa.status==='pass'&&sourceMediaUnmodified?'pass':'fail';
 if(status!=='pass') printFailedChecks('video-qa',videoQa);
 const finalInfo=await requireFile(finalVideo,'Final MP4');
 const report={status,episodeScriptSha256:sources.manifest.episodeScriptSha256,
  sourceManifestSha256:sources.manifestSha256,sourceMediaUnmodified,
  audioMix:audio.audioMix,videoQa,protectedSilenceLimitSeconds:0.5,
  output:{path:path.basename(finalVideo),sha256:await sha(finalVideo),sizeBytes:finalInfo.size}};
 await writeFile(videoReportPath,JSON.stringify(videoQa,null,2)+'\n','utf8');
 await writeFile(combinedReportPath,JSON.stringify(report,null,2)+'\n','utf8');
 ensure(status==='pass','Video QA failed or source media changed');
 console.log(JSON.stringify({phase:'video-qa',status,videoQaStatus:videoQa.status,protectedSilenceLimitSeconds:0.5,output:report.output},null,2));
}
if(phase==='audio-mix')await audioMix();
else if(phase==='video-qa')await videoQa();
else {await audioMix();await videoQa();}

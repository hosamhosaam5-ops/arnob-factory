#!/usr/bin/env python3
"""Original animated Arabic Alif card using the existing SVG character engine."""
from __future__ import annotations
import argparse, hashlib, json, math, subprocess, sys, time
from pathlib import Path
from typing import Any
from PIL import Image, ImageDraw, ImageFont, features
sys.path.insert(0,str(Path(__file__).resolve().parent))
import render_character_episode as engine

W,H,FPS=1280,720,24
PAUSE=0.20
WORDS=("أَرْنَبٌ","أَسَدٌ","أُذُنٌ","إِبْرَةٌ")
EXAMPLE="حَرْفُ الْأَلِفِ: أَرْنَبٌ، أَسَدٌ، أُذُنٌ، إِبْرَةٌ."

def sha(p:Path)->str:
 d=hashlib.sha256()
 with p.open("rb") as f:
  for b in iter(lambda:f.read(1024*1024),b""):d.update(b)
 return d.hexdigest()

def run(cmd:list[str],timeout:int=180)->None:
 try: subprocess.run(cmd,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=timeout)
 except subprocess.CalledProcessError as e: raise RuntimeError((e.stderr or e.stdout or str(e))[-4000:]) from e

def duration(p:Path)->float:
 x=float(subprocess.check_output(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(p)],text=True).strip())
 if not math.isfinite(x) or x<=0:raise RuntimeError("Invalid media duration: "+p.name)
 return x

def verify_smoke_source(manifest_path:Path,wav_path:Path)->dict[str,Any]:
 m=json.loads(manifest_path.read_text(encoding="utf-8"))
 if m.get("status")!="complete" or m.get("dryRun") is not False:raise RuntimeError("Smoke manifest is not a completed real run")
 matches=[x for x in m.get("completedChunks",[]) if Path(x.get("wav",{}).get("path","")).name==wav_path.name]
 if len(matches)!=1:raise RuntimeError("Smoke source must map to exactly one manifest chunk")
 row=matches[0]["wav"]; actual=sha(wav_path)
 if actual!=row.get("sha256") or wav_path.stat().st_size!=int(row.get("sizeBytes",-1)):raise RuntimeError("Smoke WAV SHA/size mismatch")
 return {"path":wav_path.as_posix(),"sha256":actual,"sizeBytes":wav_path.stat().st_size,"episodeId":m.get("episodeId"),"chunkId":matches[0].get("chunkId")}

def rabbit(cx:int,cy:int,s:float,t:float)->str:
 h=3*math.sin(t*5)
 return f'''<g id="letter-rabbit" transform="translate({cx},{cy+h:.2f}) scale({s})">
 <ellipse cy="18" rx="48" ry="38" fill="#F5E8D7" stroke="#8B7867" stroke-width="5"/>
 <ellipse cx="-22" cy="-45" rx="12" ry="48" transform="rotate(-9 -22 -45)" fill="#F5E8D7" stroke="#8B7867" stroke-width="5"/>
 <ellipse cx="22" cy="-45" rx="12" ry="48" transform="rotate(8 22 -45)" fill="#F5E8D7" stroke="#8B7867" stroke-width="5"/>
 <path d="M-25,-35 Q-15,-58 0,-61 Q24,-59 28,-32 Q34,4 0,12 Q-33,4 -25,-35Z" fill="#FFF7EC" stroke="#8B7867" stroke-width="5"/>
 <circle cx="-10" cy="-25" r="5" fill="#4B4039"/><circle cx="12" cy="-25" r="5" fill="#4B4039"/>
 <ellipse cy="-9" rx="6" ry="4" fill="#C97E83"/><path d="M1,-5 Q-7,4 -15,-1 M1,-5 Q9,4 17,-1" fill="none" stroke="#8B7867" stroke-width="3"/>
 </g>'''

def lion(cx:int,cy:int,s:float,t:float)->str:
 k=1+.015*math.sin(t*2)
 return f'''<g id="letter-lion" transform="translate({cx},{cy}) scale({s*k:.3f})">
 <path d="M0,-83 C49,-86 84,-51 83,-2 C84,47 49,82 0,83 C-48,82 -84,46 -83,-2 C-83,-51 -48,-86 0,-83Z" fill="#C98542" stroke="#85512E" stroke-width="6"/>
 <path d="M0,-58 C31,-61 53,-36 52,-4 C50,28 29,54 0,53 C-29,54 -51,28 -52,-4 C-52,-36 -31,-61 0,-58Z" fill="#F4D8A9"/>
 <path d="M-52,-36 L-71,-64 L-35,-58 M52,-36 L71,-64 L35,-58" fill="#E6B66C" stroke="#85512E" stroke-width="5"/>
 <ellipse cx="-20" cy="-13" rx="7" ry="10" fill="#453B31"/><ellipse cx="20" cy="-13" rx="7" ry="10" fill="#453B31"/>
 <ellipse cy="9" rx="11" ry="7" fill="#805040"/><path d="M0,15 Q-12,30 -27,18 M0,15 Q12,30 27,18" fill="none" stroke="#805040" stroke-width="4"/>
 </g>'''

def ear(cx:int,cy:int,s:float,t:float)->str:
 r=2*math.sin(t*1.6)
 return f'''<g id="letter-ear" transform="translate({cx},{cy}) rotate({r:.2f}) scale({s})">
 <path d="M-28,59 C-57,19 -45,-54 -12,-78 C12,-96 50,-74 48,-34 C47,1 27,30 20,61Z" fill="#E6AF91" stroke="#976B59" stroke-width="6"/>
 <path d="M-11,45 C-33,6 -25,-47 -5,-60 C11,-68 30,-54 27,-31 C22,0 11,23 7,47Z" fill="#C97F78"/>
 </g>'''

def needle(cx:int,cy:int,s:float,t:float)->str:
 r=4*math.sin(t*1.4)
 return f'''<g id="letter-needle" transform="translate({cx},{cy}) rotate({r:.2f}) scale({s})">
 <path d="M-50,55 L46,-66" stroke="#A6A9A6" stroke-width="9" stroke-linecap="round"/>
 <path d="M-57,64 L-40,52 L46,-66" fill="none" stroke="#657579" stroke-width="4"/>
 <ellipse cx="48" cy="-68" rx="8" ry="15" transform="rotate(37 48 -68)" fill="none" stroke="#9B9FA0" stroke-width="6"/>
 <path d="M-48,53 L-64,70" stroke="#657579" stroke-width="4" stroke-linecap="round"/>
 </g>'''

def card_svg(speaker:str,t:float,level:float,highlight:int=-1)->str:
 # The existing engine draws Pistachio as a cat and Hazelnut as a mouse.
 cat=engine.cat_svg(225,552,level,t,speaker=="pistachio",0,.6,"clever")
 mouse=engine.mouse_svg(1050,552,level,t,speaker=="hazelnut",0,2.2)
 centers=(425,605,785,970)
 arts=(rabbit(425,374,.68,t),lion(605,374,.68,t),ear(785,375,.76,t),needle(970,374,.8,t))
 tiles=[]
 for i,(cx,drawing) in enumerate(zip(centers,arts)):
  color="#E0A84F" if i==highlight else "#D9D5C8"; width=6 if i==highlight else 3
  tiles.append(f'<rect x="{cx-78}" y="292" width="156" height="165" rx="24" fill="#FFFCF5" stroke="{color}" stroke-width="{width}"/>'+drawing+f'<text x="{cx}" y="443" text-anchor="middle" direction="rtl" unicode-bidi="plaintext" font-family="Noto Sans Arabic, sans-serif" font-size="22" font-weight="700" fill="#3C5546">{WORDS[i]}</text>')
 rays=[]
 for i in range(20):
  a=math.pi*2*i/20+math.sin(t*.7)*.035
  x1,y1=640+math.cos(a)*104,200+math.sin(a)*104
  x2,y2=640+math.cos(a)*178,200+math.sin(a)*178
  op=.10+.07*(.5+.5*math.sin(t*1.6+i*.4))
  rays.append(f'<path d="M{x1:.1f},{y1:.1f} L{x2:.1f},{y2:.1f}" stroke="#F4C56E" stroke-width="8" stroke-linecap="round" opacity="{op:.3f}"/>')
 return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 1280 720">
 <defs>
 <linearGradient id="card-bg" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#E9F2E8"/><stop offset=".54" stop-color="#FFF4D9"/><stop offset="1" stop-color="#E8D8BE"/></linearGradient>
 <linearGradient id="alif-fill" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#D98C3A"/><stop offset=".46" stop-color="#F4C66D"/><stop offset="1" stop-color="#B96645"/></linearGradient>
 <radialGradient id="alif-aura"><stop stop-color="#FFE8A5" stop-opacity=".9"/><stop offset="1" stop-color="#FFE8A5" stop-opacity="0"/></radialGradient>
 <filter id="alif-glow"><feGaussianBlur stdDeviation="13"/></filter></defs>
 <rect width="1280" height="720" fill="url(#card-bg)"/><circle cx="640" cy="205" r="205" fill="url(#alif-aura)"/>
 {''.join(rays)}
 <rect x="40" y="24" width="1200" height="64" rx="24" fill="#FFFCF2" opacity=".9"/>
 <text x="640" y="69" text-anchor="middle" direction="rtl" font-family="Noto Naskh Arabic, serif" font-size="38" font-weight="700" fill="#3D6650">حَرْفُ الْأَلِفِ</text>
 <text x="640" y="274" text-anchor="middle" direction="rtl" font-family="Noto Sans Arabic, sans-serif" font-size="236" font-weight="800" fill="#D98C3A" opacity=".34" filter="url(#alif-glow)">أ</text>
 <text x="640" y="270" text-anchor="middle" direction="rtl" font-family="Noto Sans Arabic, sans-serif" font-size="226" font-weight="800" fill="url(#alif-fill)" stroke="#FFF6D9" stroke-width="3" paint-order="stroke">أ</text>
 {''.join(tiles)}{cat}{mouse}
 <text x="225" y="578" text-anchor="middle" direction="rtl" font-family="Noto Sans Arabic" font-size="23" font-weight="700" fill="#3D6650">فُسْتُقٌ</text>
 <text x="1050" y="578" text-anchor="middle" direction="rtl" font-family="Noto Sans Arabic" font-size="23" font-weight="700" fill="#3D6650">بُنْدُقٌ</text>
 <rect x="44" y="596" width="1192" height="100" rx="24" fill="#FFFCF4" stroke="#E4D7BF" stroke-width="2"/>
 </svg>'''

def draw_caption(path:Path,text:str)->None:
 if not features.check("raqm"):raise RuntimeError("Pillow RAQM required for Arabic shaping")
 fonts=("/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf","/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf")
 font_path=next((p for p in fonts if Path(p).is_file()),None)
 if font_path is None:raise RuntimeError("Noto Arabic font missing")
 selected=None; lines=[]
 for size in (28,27,26,25,24,23):
  f=ImageFont.truetype(font_path,size,layout_engine=ImageFont.Layout.RAQM)
  wrapped=engine.wrap_arabic(text,font=f,max_width=1080)
  if len(wrapped)<=2:selected,lines=f,wrapped;break
 if selected is None:raise RuntimeError("Caption does not fit two lines")
 image=Image.open(path).convert("RGB"); draw=ImageDraw.Draw(image)
 ys=(636,668) if len(lines)==2 else (652,)
 for line,y in zip(lines,ys):draw.text((640,y),line,font=selected,fill="#29483B",anchor="mm",direction="rtl",language="ar")
 image.save(path,format="PNG",optimize=False)

def raster(svg:str,path:Path)->None:
 svg_path=path.with_suffix(".svg"); svg_path.write_text(svg,encoding="utf-8")
 run(["rsvg-convert","--width",str(W), "--height",str(H),"--output",str(path),str(svg_path)],45)

def smoke(args:argparse.Namespace)->dict[str,Any]:
 source=Path(args.source_wav).resolve(strict=True)
 fp=verify_smoke_source(Path(args.source_manifest).resolve(strict=True),source)
 out=Path(args.output_dir).resolve(); out.mkdir(parents=True,exist_ok=True)
 png=out/"letter-card-smoke.png"; raster(card_svg("pistachio",0,.0),png); draw_caption(png,EXAMPLE)
 mp4=out/"letter-card-smoke.mp4"
 run(["ffmpeg","-hide_banner","-nostdin","-loglevel","error","-y","-loop","1","-framerate",str(FPS),"-i",str(png),"-i",str(source),"-t","2.5","-map","0:v:0","-map","1:a:0","-vf","format=yuv420p","-c:v","libx264","-preset","ultrafast","-threads","2","-c:a","aac","-shortest","-movflags","+faststart",str(mp4)],90)
 if sha(source)!=fp["sha256"]:raise RuntimeError("Smoke source WAV was modified")
 report={"status":"pass","source":fp,"sourceUnmodified":True,"letter":"أ","svgDrawings":["rabbit","lion","ear","needle"],"pngSha256":sha(png),"mp4Sha256":sha(mp4),"durationSeconds":round(duration(mp4),6)}
 (out/"smoke-report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 print(json.dumps(report,ensure_ascii=False,indent=2),flush=True); return report

def make_voice(root:Path,out:Path,chunks:list[dict[str,Any]])->tuple[Path,list[dict[str,Any]]]:
 pause=out/"pause-0.20s.wav"
 run(["ffmpeg","-hide_banner","-nostdin","-loglevel","error","-y","-f","lavfi","-i","anullsrc=r=24000:cl=mono","-t",str(PAUSE),"-ar","24000","-ac","1","-c:a","pcm_s16le",str(pause)])
 lines=[]; details=[]
 for i,item in enumerate(chunks):
  wav=root/item["wav"]["path"]
  if sha(wav)!=item["wav"]["sha256"]:raise RuntimeError("Input WAV SHA mismatch: "+wav.name)
  lines.append("file '"+wav.resolve().as_posix().replace("'","'\\''")+"'")
  details.append({"item":item,"wav":wav})
  if i<len(chunks)-1:lines.append("file '"+pause.resolve().as_posix()+"'")
 listing=out/"voice-concat.txt"; listing.write_text("\n".join(lines)+"\n",encoding="utf-8")
 voice=out/"episode-audio.wav"
 run(["ffmpeg","-hide_banner","-nostdin","-loglevel","error","-y","-f","concat","-safe","0","-i",str(listing),"-map","0:a:0","-ar","24000","-ac","1","-c:a","pcm_s16le",str(voice)],300)
 return voice,details

def full_render(root:Path,out:Path)->dict[str,Any]:
 started=time.perf_counter(); manifest_path=root/"manifest.json"
 m=json.loads(manifest_path.read_text(encoding="utf-8"))
 if m.get("status")!="complete" or m.get("dryRun") is not False or m.get("modelInferenceInvoked") is not True:raise RuntimeError("Renderer requires a completed real SILMA run")
 if m.get("episodeId")!="letter-alif" or m.get("method")!="A" or m.get("nfeStep")!=16:raise RuntimeError("Unexpected episode or synthesis settings")
 chunks=m.get("completedChunks",[])
 if len(chunks)!=8:raise RuntimeError("Exactly eight chunks required")
 for i,c in enumerate(chunks):
  if c.get("speakerId")!=("pistachio" if i%2==0 else "hazelnut"):raise RuntimeError("Unexpected speaker order")
 out.mkdir(parents=True,exist_ok=True); voice,details=make_voice(root,out,chunks)
 timeline=[]; envelopes=[]; cursor=0.
 for i,d in enumerate(details):
  vals,_=engine.rms_envelope(d["wav"]); envelopes.append(vals)
  dur=float(d["item"]["wav"]["durationSeconds"])
  timeline.append({"speaker":d["item"]["speakerId"],"text":d["item"]["text"],"start":cursor,"duration":dur})
  cursor+=dur+(PAUSE if i<7 else 0)
 total=math.ceil(cursor*FPS); frames=out/"frames"; preview=out/"preview"
 frames.mkdir(parents=True,exist_ok=True); preview.mkdir(parents=True,exist_ok=True)
 frame_wall=0.
 for n in range(1,total+1):
  t=(n-1)/FPS; idx=max(i for i,x in enumerate(timeline) if x["start"]<=t); row=timeline[idx]
  local=t-row["start"]; level=0. if local>=row["duration"] else envelopes[idx]["values"][min(len(envelopes[idx]["values"])-1,max(0,int(local*FPS)))]
  p=frames/("frame_%06d.png"%n); before=time.perf_counter()
  raster(card_svg(row["speaker"],t,level),p); draw_caption(p,row["text"])
  frame_wall+=time.perf_counter()-before
  if n%120==0:print("letter-card-frames="+str(n)+"/"+str(total),flush=True)
 # Three explicit deliverable stills.
 p=preview/"letter-card.png"; raster(card_svg("pistachio",1,.2),p); draw_caption(p,EXAMPLE)
 p=preview/"word-rabbit.png"; raster(card_svg("pistachio",2,.35,0),p); draw_caption(p,"أَرْنَبٌ")
 last=timeline[-1]; p=preview/"final-scene.png"; raster(card_svg(last["speaker"],cursor,0),p); draw_caption(p,last["text"])
 raw=out/"letter-card-video-raw.mp4"
 run(["ffmpeg","-hide_banner","-nostdin","-loglevel","error","-y","-framerate",str(FPS),"-i",str(frames/"frame_%06d.png"),"-frames:v",str(total),"-an","-c:v","libx264","-preset","veryfast","-crf","20","-threads","2","-pix_fmt","yuv420p","-r",str(FPS),"-movflags","+faststart",str(raw)],300)
 if abs(duration(raw)-duration(voice))>.08:raise RuntimeError("Audio/video durations differ")
 for d in details:
  if sha(d["wav"])!=d["item"]["wav"]["sha256"]:raise RuntimeError("Source WAV modified during render")
 report={"status":"rendered-awaiting-qa","episodeId":"letter-alif","sourceManifestSha256":sha(manifest_path),
  "episodeScriptSha256":m.get("episodeScriptSha256"),"sourceWavCount":8,
  "sourceWavSha256":[x["item"]["wav"]["sha256"] for x in details],
  "sourceMp3Sha256":[x["item"]["mp3"]["sha256"] for x in details],
  "resolution":{"width":W,"height":H},"fps":FPS,"frames":total,"durationSeconds":round(duration(raw),6),
  "rawVideo":{"path":raw.name,"sha256":sha(raw),"sizeBytes":raw.stat().st_size},
  "voiceTrack":{"path":voice.name,"sha256":sha(voice),"sizeBytes":voice.stat().st_size},
  "previewFrames":{n:{"path":"preview/"+n,"sha256":sha(preview/n)} for n in ("letter-card.png","word-rabbit.png","final-scene.png")},
  "renderWallSeconds":round(time.perf_counter()-started,3),"frameRasterWallSeconds":round(frame_wall,3),
  "visualAssets":"original-code-generated-svg","rasterizer":"librsvg rsvg-convert","sourceWavsUnmodified":True}
 (out/"render-report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 print(json.dumps(report,ensure_ascii=False,indent=2),flush=True); return report

def main()->None:
 ap=argparse.ArgumentParser(); ap.add_argument("--smoke",action="store_true"); ap.add_argument("--source-manifest"); ap.add_argument("--source-wav"); ap.add_argument("--generation-dir"); ap.add_argument("--output-dir",required=True); a=ap.parse_args()
 if a.smoke:
  if not a.source_manifest or not a.source_wav:ap.error("--smoke requires a real source manifest and WAV")
  smoke(a)
 else:
  if not a.generation_dir:ap.error("full render requires --generation-dir")
  full_render(Path(a.generation_dir).resolve(),Path(a.output_dir).resolve())
if __name__=="__main__":main()

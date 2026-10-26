"""Background, sequential batch controller for the complete video pipeline."""
import gc, threading, time, uuid, os, requests, copy, re, unicodedata
from datetime import datetime
from common import DIRS, env_bool
from script_generator import ScriptGenerator
from audio_generator import AudioGenerator
from image_generator import ImageGenerator
from video_fetcher import VideoFetcher
from local_ai_generator import LocalAIGenerator
from video_assembler import VideoAssembler
from thumbnail_generator import ThumbnailGenerator
from youtube_seo import YouTubeSEO
from youtube_uploader import YouTubeUploader

VIDEO_STEP_KEYS=("script","images","clips","audio","assembly","subtitles","music","encoding","seo","thumbnail","upload","finalize")

class BatchOrchestrator:
    def __init__(self):
        self.scripts=ScriptGenerator();self.audio=AudioGenerator();self.images=ImageGenerator();self.fetcher=VideoFetcher();self.local=LocalAIGenerator();self.assembler=VideoAssembler();self.thumbnails=ThumbnailGenerator(self.local);self.seo=YouTubeSEO();self.youtube=YouTubeUploader()
        self.batches={};self.lock=threading.RLock();self.pipeline_lock=threading.Lock();self.sequence=int(time.time())
    def start_batch(self,titles:list,duration:int,niche:str,auto_upload:bool,privacy:str,custom_scripts=None,resolution="720p")->str:
        batch_id=datetime.utcnow().strftime("%Y%m%dT%H%M%S")+"-"+uuid.uuid4().hex[:6]
        data={"batch_id":batch_id,"created_at":datetime.utcnow().isoformat()+"Z","status":"queued","total":len(titles),"completed":0,"failed":0,"progress":0,"resolution":resolution,"videos":[{"title":t,"status":"waiting","progress":0,"current_stage":"Waiting","progress_message":"Queued","steps":{key:"pending" for key in VIDEO_STEP_KEYS},"step_messages":{}} for t in titles],"error":None}
        with self.lock:self.batches[batch_id]=data
        threading.Thread(target=self._process_batch,args=(batch_id,titles,duration,niche,auto_upload,privacy,custom_scripts,resolution),daemon=True,name=f"batch-{batch_id}").start()
        return batch_id
    def get_status(self,batch_id):
        with self.lock:
            item=self.batches.get(batch_id)
            return copy.deepcopy(item) if item else None
    def list_batches(self):
        with self.lock:return [dict(v) for v in sorted(self.batches.values(),key=lambda x:x["created_at"],reverse=True)]
    def _set(self,batch_id,**kwargs):
        with self.lock:self.batches[batch_id].update(kwargs)
    def _update_video_progress(self,batch_id,index,progress,stage,message,step_updates=None):
        progress=max(0,min(100,int(progress)))
        with self.lock:
            batch=self.batches.get(batch_id)
            if not batch:return
            video=batch["videos"][index]
            steps=video.setdefault("steps",{key:"pending" for key in VIDEO_STEP_KEYS})
            previous=video.get("current_stage")
            if previous in steps and previous!=stage and steps.get(previous)=="running":steps[previous]="done"
            if stage in steps and steps.get(stage)=="pending":steps[stage]="running"
            messages=video.setdefault("step_messages",{})
            messages[stage]=message
            for key,value in (step_updates or {}).items():
                if key in steps:steps[key]=value
            video.update(progress=progress,current_stage=stage,progress_message=message,steps=steps,step_messages=messages)
            batch["progress"]=round(100*(batch["completed"]+progress/100)/max(1,batch["total"]))
    def _process_batch(self,batch_id,titles,duration,niche,auto_upload,privacy,custom_scripts=None,resolution="720p"):
        # Serialize independent batches too: never overlap GPU model loads or media assembly.
        with self.pipeline_lock:
            self._run_batch_locked(batch_id,titles,duration,niche,auto_upload,privacy,custom_scripts,resolution)
    def _run_batch_locked(self,batch_id,titles,duration,niche,auto_upload,privacy,custom_scripts=None,resolution="720p"):
        self._set(batch_id,status="running")
        for index,title in enumerate(titles):
            with self.lock:self.batches[batch_id]["videos"][index].update(status="processing",started_at=datetime.utcnow().isoformat()+"Z")
            custom_script=custom_scripts[index] if custom_scripts and index<len(custom_scripts) else None
            try:result=self._process_single_video(title,index,duration,niche,batch_id,auto_upload,privacy,custom_script,resolution)
            except Exception as exc:
                with self.lock:
                    live=self.batches[batch_id]["videos"][index]
                    failed_steps=copy.deepcopy(live.get("steps",{}));stage=live.get("current_stage")
                if stage in failed_steps:failed_steps[stage]="failed"
                self._update_video_progress(batch_id,index,live.get("progress",0),stage or "failed",str(exc),{stage:"failed"} if stage in failed_steps else None)
                result={"title":title,"success":False,"steps":failed_steps,"error":str(exc)}
            with self.lock:
                videos=self.batches[batch_id]["videos"];videos[index].update(result,status="done" if result.get("success") else "failed")
                self.batches[batch_id]["completed"]+=1
                if not result.get("success"):self.batches[batch_id]["failed"]+=1
                self.batches[batch_id]["progress"]=round(100*self.batches[batch_id]["completed"]/len(titles))
            self._clear_gpu()
            if index<len(titles)-1:
                try:time.sleep(max(0,int(__import__('os').getenv("BATCH_DELAY_SECONDS","10"))))
                except Exception:pass
        self._set(batch_id,status="completed",finished_at=datetime.utcnow().isoformat()+"Z")
    @staticmethod
    def _title_video_stem(title):
        """Create a readable, filesystem-safe stem and avoid overwriting same-title videos."""
        normalized=unicodedata.normalize("NFKC",str(title or "")).strip().lower()
        stem=re.sub(r"[\s_]+","-",normalized)
        stem=re.sub(r"[^\w-]","-",stem,flags=re.UNICODE)
        stem=re.sub(r"-+","-",stem).strip("-._")[:100].rstrip("-._") or "video"
        if stem.upper() in {"CON","PRN","AUX","NUL",*(f"COM{i}" for i in range(1,10)),*(f"LPT{i}" for i in range(1,10))}:
            stem="video-"+stem
        candidate=stem;counter=2
        while any((DIRS["videos"]/f"{candidate}.{extension}").exists() for extension in ("mp4","srt")):
            suffix=f"-{counter}"
            candidate=stem[:100-len(suffix)].rstrip("-._")+suffix
            counter+=1
        return candidate
    def _process_single_video(self,title,index,duration,niche,batch_id,auto_upload,privacy,custom_script=None,resolution="720p"):
        steps={name:"pending" for name in VIDEO_STEP_KEYS}
        base=self._title_video_stem(title);self.sequence+=1;serial=self.sequence
        result={"title":title,"success":False,"steps":steps,"video_path":None,"subtitle_path":None,"youtube_url":None,"seo":None,"error":None}
        report=lambda pct,stage,message,step_updates=None:self._update_video_progress(batch_id,index,pct,stage,message,step_updates)
        model_name=getattr(self.scripts,"model","local AI model")
        if custom_script is not None:script_message=f"Submitting your draft to Ollama {model_name} for analysis and rewrite…"
        elif self.scripts.gemini_ready:script_message=f"Asking Google AI Studio {self.scripts.gemini_model} to draft narration; Ollama {model_name} will finalize the script and scene plan…"
        elif self.scripts.provider=="gemini" and self.scripts.gemini_api_key and not self.scripts.enabled:script_message="Gemini drafting is skipped because Ollama finalization is disabled; using the built-in template fallback…"
        elif self.scripts.provider=="gemini" and not self.scripts.gemini_api_key and self.scripts.enabled:script_message=f"Google AI Studio key is not configured; asking Ollama {model_name} to write narration and plan scenes…"
        elif self.scripts.enabled:script_message=f"Asking Ollama {model_name} to write narration and plan scenes…"
        else:script_message="Creating narration and scene plan from the built-in template…"
        report(1,"script",script_message)
        try:
            if custom_script is not None:
                script=self.scripts.generate_custom_script(title,custom_script,duration)
            else:
                script=self.scripts.generate_script(title,duration)
            result["script"]=script;steps["script"]="done"
            script_source=getattr(self.scripts,"last_source",None)
            ready_message=f"Script and scene plan ready · {script_source}" if script_source else "Script and scene plan ready"
            report(8,"script",ready_message,{"script":"done"})
        except Exception as exc:
            steps["script"]="failed";result["error"]=f"Script generation failed: {exc}"
            report(8,"script",result["error"],{"script":"failed"});return result
        if env_bool("USE_LOCAL_IMAGES",False) or env_bool("USE_LOCAL_VIDEO",False):
            self._release_ollama_for_gpu()
        media=[];scene_images=[]
        scenes=script.get("scenes",[])
        for i,scene in enumerate(scenes):
            pct=8+int(20*i/max(1,len(scenes)))
            local_images=env_bool("USE_LOCAL_IMAGES",False) and self.local.available
            source="local SDXL image model" if local_images else "Pexels stock search" if self.images.api_key else "visual fallback renderer"
            report(pct,"images",f"Creating scene visual {i+1} of {len(scenes)} with {source}…")
            path=self.local.generate_image(scene.get("visual_description",title),serial*100+i)
            if not path:
                if local_images:report(pct,"images",f"Local image generation unavailable for scene {i+1}; switching to stock/fallback visuals…")
                path=self.images.generate_scene_image(scene,serial*100+i)
            scene_images.append(path);media.append({"type":"image","path":path})
            report(8+int(20*(i+1)/max(1,len(scenes))),"images",f"Scene visual {i+1} of {len(scenes)} ready")
        steps["images"]="done" if scene_images else "failed"
        report(28,"images",f"Created {len(scene_images)} scene visual(s)",{"images":steps["images"]})
        # Try image-to-video in sequence; failed scenes retain their still image.
        for i,scene in enumerate(scenes):
            pct=28+int(17*i/max(1,len(scenes)))
            local_video=env_bool("USE_LOCAL_VIDEO",False) and self.local.available
            message=f"Animating scene {i+1} of {len(scenes)} with Stable Video Diffusion…" if local_video else f"Searching stock footage for scene {i+1} of {len(scenes)}…"
            report(pct,"clips",message)
            clip=self.local.generate_video_clip(scene_images[i],scene.get("visual_description",title),serial*100+i)
            if not clip:
                if local_video:report(pct,"clips",f"Local animation unavailable for scene {i+1}; searching stock footage…")
                clip=self.fetcher.fetch_clip(scene.get("search_keywords",title),serial*100+i,min_dur=5)
            if clip:media[i]={"type":"video","path":clip}
            else:report(pct,"clips",f"No motion clip found for scene {i+1}; will use the still image with animated transitions…")
            report(28+int(17*(i+1)/max(1,len(scenes))),"clips",f"Scene {i+1} motion option ready")
        steps["clips"]="done" if any(m["type"]=="video" for m in media) else "fallback"
        clip_summary="Stock or AI motion clips ready" if steps["clips"]=="done" else "Using still-image motion and transitions"
        report(45,"clips",clip_summary,{"clips":steps["clips"]})
        audio_paths=[]
        if self.audio.voice_engine == "chatterbox":
            # Ollama may otherwise occupy most of an 8 GB GPU while Chatterbox loads.
            self._release_ollama_for_gpu()
        try:
            for i,scene in enumerate(scenes):
                pct=45+int(15*i/max(1,len(scenes)))
                engine_label="local Chatterbox (Edge TTS fallback available)" if self.audio.voice_engine == "chatterbox" else "Edge TTS (offline voice fallback available)"
                report(pct,"audio",f"Generating narration {i+1} of {len(scenes)} with {engine_label}…")
                try:
                    audio_path=self.audio.generate(scene.get("narration",title),serial*100+i)
                    if not os.path.isfile(audio_path) or os.path.getsize(audio_path)==0:
                        raise RuntimeError("Narration output is missing or empty.")
                    audio_paths.append(audio_path)
                    if self.audio.last_warning and not result.get("voice_warning"):
                        result["voice_warning"]=self.audio.last_warning
                        result["voice_fallback_engine"]=self.audio.last_engine
                    report(45+int(15*(i+1)/max(1,len(scenes))),"audio",f"Narration audio {i+1} of {len(scenes)} ready with {self.audio.last_engine}")
                except Exception as exc:
                    steps["audio"]="failed"
                    result["error"]=f"Voiceover generation failed in scene {i+1}: {exc}"
                    report(pct,"audio",result["error"],{"audio":"failed"});return result
        finally:
            self.audio.release_chatterbox_model()
        steps["audio"]="done" if audio_paths else "failed"
        report(60,"audio",f"Narration ready for {len(audio_paths)} scene(s)",{"audio":steps["audio"]})
        report(60,"assembly","Arranging scene order and preparing transitions…")
        assembly_percent=[60]
        try:
            def assembly_progress(fraction):
                assembly_percent[0]=60+int(22*max(0,min(1,fraction)))
                encoder=self.assembler.last_video_encoder or "video encoder"
                report(assembly_percent[0],"encoding",f"Encoding MP4 with {encoder}… {int(100*max(0,min(1,fraction)))}%")
            def assembly_stage(stage,message):
                report(assembly_percent[0],stage,message)
            video_path=self.assembler.create(script,media,audio_paths,base,progress_callback=assembly_progress,video_title=title,resolution=resolution,use_nvenc=env_bool("USE_NVENC",True),stage_callback=assembly_stage);result["video_path"]=video_path
            result["video_encoder"]=self.assembler.last_video_encoder
            subtitle_path=os.path.splitext(video_path)[0]+".srt"
            result["subtitle_path"]=subtitle_path if os.path.isfile(subtitle_path) else None
            steps["assembly"]="done";steps["subtitles"]="done" if result["subtitle_path"] else "failed";steps["music"]="done" if self.assembler.last_music_style else "fallback";steps["encoding"]="done"
            if self.assembler.last_music_error:result["music_warning"]=self.assembler.last_music_error
            report(82,"encoding",f"MP4 encoded with {self.assembler.last_video_encoder}",{"assembly":"done","subtitles":steps["subtitles"],"music":steps["music"],"encoding":"done"})
        except Exception as exc:
            result["error"]=f"Video assembly failed: {exc}"
            with self.lock:
                current=self.batches[batch_id]["videos"][index].get("current_stage")
            failed_step=current if current in steps else "assembly"
            steps[failed_step]="failed"
            report(82,failed_step,result["error"],{failed_step:"failed"});return result
        seo_message=f"Asking Ollama {getattr(self.seo,'model','local model')} to prepare title, description and tags (built-in fallback available)…" if self.seo.enabled else "Preparing YouTube metadata with the built-in SEO template…"
        report(83,"seo",seo_message)
        try:
            seo=self.seo.generate_seo_package(title,script,niche);result["seo"]=seo;steps["seo"]="done"
            report(87,"seo","YouTube title, description and tags ready",{"seo":"done"})
        except Exception as exc:
            steps["seo"]="failed";result["error"]=f"SEO generation failed: {exc}"
            report(87,"seo",result["error"],{"seo":"failed"});return result
        thumbnail_message="Generating thumbnail artwork with the local image model and title graphics…" if env_bool("USE_LOCAL_IMAGES",False) else "Designing branded thumbnail artwork from the video title…"
        report(88,"thumbnail",thumbnail_message)
        try:
            thumb=self.thumbnails.generate(seo,title,serial);result["thumbnail_path"]=thumb;steps["thumbnail"]="done"
            report(92,"thumbnail","Thumbnail artwork ready",{"thumbnail":"done"})
        except Exception as exc:
            steps["thumbnail"]="failed";result["error"]=f"Thumbnail generation failed: {exc}"
            report(92,"thumbnail",result["error"],{"thumbnail":"failed"});return result
        result["success"]=True
        if auto_upload:
            report(94,"upload","Starting resumable upload to your YouTube channel…")
            def upload_progress(fraction):
                report(94+int(5*max(0,min(1,fraction))),"upload",f"Uploading video to YouTube… {int(100*max(0,min(1,fraction)))}%")
            upload=self.youtube.upload_video(video_path,seo,thumb,privacy,progress_callback=upload_progress)
            if upload.get("success"):result["youtube_url"]=upload.get("url");result["youtube_video_id"]=upload.get("video_id");steps["upload"]="done"
            else:steps["upload"]="failed";result["upload_error"]=upload.get("error");result["success"]=False
            report(99,"upload","Upload complete" if result["success"] else f"Upload failed: {result.get('upload_error','Unknown error')}",{"upload":steps["upload"]})
        else:
            steps["upload"]="skipped"
            report(94,"upload","Auto-upload is off; YouTube upload skipped",{"upload":"skipped"})
        steps["finalize"]="done"
        report(100,"finalize","Video complete and ready to download" if result["success"] else "Video created, but publishing did not complete",{"finalize":"done"})
        return result
    @staticmethod
    def _release_ollama_for_gpu():
        """Unload the selected LLM before loading diffusion models onto the same GPU."""
        if not env_bool("USE_OLLAMA",True):return
        model=os.getenv("OLLAMA_MODEL","qwen2.5:7b")
        url=os.getenv("OLLAMA_URL","http://localhost:11434").rstrip("/")
        try:
            requests.post(url+"/api/generate",json={"model":model,"prompt":"","stream":False,"keep_alive":0,"options":{"num_predict":0}},timeout=8)
        except requests.RequestException as exc:
            print(f"[gpu] Could not request Ollama model unload before diffusion step: {exc}")

    @staticmethod
    def _clear_gpu():
        try:
            import torch
            if torch.cuda.is_available():torch.cuda.empty_cache()
        except Exception:pass
        gc.collect()

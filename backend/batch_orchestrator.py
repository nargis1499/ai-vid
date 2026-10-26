"""Background, sequential batch controller for the complete video pipeline."""
import gc, threading, time, uuid, os, requests, copy, re, unicodedata, traceback
from datetime import datetime
from common import DIRS, env_bool
from script_generator import ScriptGenerator
from audio_generator import AudioGenerator, NarrationCancelled
from image_generator import ImageGenerator
from video_fetcher import VideoFetcher
from local_ai_generator import LocalAIGenerator
from video_assembler import VideoAssembler
from thumbnail_generator import ThumbnailGenerator
from youtube_seo import YouTubeSEO
from youtube_uploader import YouTubeUploader

VIDEO_STEP_KEYS=("script","images","clips","audio","assembly","subtitles","music","encoding","seo","thumbnail","upload","finalize")


class BatchCancelled(Exception):
    """Cooperative cancellation requested by the user."""


class BatchOrchestrator:
    def __init__(self):
        self.scripts=ScriptGenerator();self.audio=AudioGenerator();self.images=ImageGenerator();self.fetcher=VideoFetcher();self.local=LocalAIGenerator();self.assembler=VideoAssembler();self.thumbnails=ThumbnailGenerator();self.seo=YouTubeSEO();self.youtube=YouTubeUploader()
        self.batches={};self.lock=threading.RLock();self.pipeline_lock=threading.Lock();self.upload_lock=threading.Lock();self.cancel_events={};self.active_batch_id=None;self.sequence=int(time.time())
    def start_batch(self,titles:list,duration:int,niche:str,auto_upload:bool,privacy:str,custom_scripts=None,resolution="720p",voice_speed="medium")->str:
        batch_id=datetime.utcnow().strftime("%Y%m%dT%H%M%S")+"-"+uuid.uuid4().hex[:6]
        data={"batch_id":batch_id,"created_at":datetime.utcnow().isoformat()+"Z","status":"queued","total":len(titles),"completed":0,"failed":0,"cancelled":0,"progress":0,"resolution":resolution,"voice_speed":voice_speed,"auto_upload":auto_upload,"privacy":privacy,"videos":[{"title":t,"status":"waiting","progress":0,"current_stage":"Waiting","progress_message":"Queued","upload_status":"not_requested","steps":{key:"pending" for key in VIDEO_STEP_KEYS},"step_messages":{}} for t in titles],"error":None}
        with self.lock:
            self.batches[batch_id]=data
            self.cancel_events[batch_id]=threading.Event()
        threading.Thread(target=self._process_batch,args=(batch_id,titles,duration,niche,auto_upload,privacy,custom_scripts,resolution,voice_speed),daemon=True,name=f"batch-{batch_id}").start()
        return batch_id

    def request_stop(self,batch_id):
        with self.lock:
            batch=self.batches.get(batch_id)
            if not batch:
                return False,"Batch not found."
            if batch.get("status") not in {"queued","running","stopping"}:
                return False,"This batch is not generating."
            event=self.cancel_events.get(batch_id)
            if event:
                event.set()
            batch["status"]="stopping"
            active=self.active_batch_id==batch_id
        if active:
            self.audio.cancel_current()
        return True,"Stop requested. The current operation is being cancelled."

    def _is_cancelled(self,batch_id):
        event=self.cancel_events.get(batch_id)
        return bool(event and event.is_set())

    def _check_cancel(self,batch_id):
        if self._is_cancelled(batch_id):
            raise BatchCancelled("Generation stopped by user.")
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
    def _process_batch(self,batch_id,titles,duration,niche,auto_upload,privacy,custom_scripts=None,resolution="720p",voice_speed="medium"):
        # Serialize independent batches too: never overlap GPU model loads or media assembly.
        try:
            with self.pipeline_lock:
                with self.lock:
                    self.active_batch_id=batch_id
                try:
                    self._run_batch_locked(batch_id,titles,duration,niche,auto_upload,privacy,custom_scripts,resolution,voice_speed)
                finally:
                    with self.lock:
                        if self.active_batch_id==batch_id:
                            self.active_batch_id=None
        except BatchCancelled:
            with self.lock:
                batch=self.batches.get(batch_id)
                if not batch:
                    return
                for video in batch.get("videos",[]):
                    if video.get("status") not in {"waiting","processing"}:
                        continue
                    steps=video.setdefault("steps",{key:"pending" for key in VIDEO_STEP_KEYS})
                    for key,value in steps.items():
                        if value in {"pending","running"}:
                            steps[key]="skipped"
                    video.update(status="cancelled",progress_message="Stopped by user.",steps=steps)
                    batch["cancelled"]+=1
                batch.update(status="cancelled",error=None,finished_at=datetime.utcnow().isoformat()+"Z")
                batch["progress"]=round(100*(batch["completed"]+batch["cancelled"])/max(1,batch["total"]))
            print(f"[batch] {batch_id}: stopped by user")
        except Exception as exc:
            error=f"Batch stopped unexpectedly: {type(exc).__name__}: {exc}"
            print(f"[batch] {batch_id}: {error}")
            traceback.print_exc()
            with self.lock:
                batch=self.batches.get(batch_id)
                if not batch or batch.get("status")=="completed":
                    return
                for video in batch.get("videos",[]):
                    if video.get("status") not in {"waiting","processing"}:
                        continue
                    steps=video.setdefault("steps",{key:"pending" for key in VIDEO_STEP_KEYS})
                    stage=video.get("current_stage")
                    if stage in steps:
                        steps[stage]="failed"
                    video.update(status="failed",error=error,steps=steps,progress_message=error)
                    batch["completed"]+=1
                    batch["failed"]+=1
                batch.update(status="failed",error=error,finished_at=datetime.utcnow().isoformat()+"Z")
                batch["progress"]=round(100*batch["completed"]/max(1,batch["total"]))
    def _run_batch_locked(self,batch_id,titles,duration,niche,auto_upload,privacy,custom_scripts=None,resolution="720p",voice_speed="medium"):
        self._check_cancel(batch_id)
        self._set(batch_id,status="running")
        for index,title in enumerate(titles):
            self._check_cancel(batch_id)
            with self.lock:self.batches[batch_id]["videos"][index].update(status="processing",started_at=datetime.utcnow().isoformat()+"Z")
            custom_script=custom_scripts[index] if custom_scripts and index<len(custom_scripts) else None
            try:result=self._process_single_video(title,index,duration,niche,batch_id,auto_upload,privacy,custom_script,resolution,voice_speed)
            except BatchCancelled:
                raise
            except Exception as exc:
                with self.lock:
                    live=self.batches[batch_id]["videos"][index]
                    failed_steps=copy.deepcopy(live.get("steps",{}));stage=live.get("current_stage")
                if stage in failed_steps:failed_steps[stage]="failed"
                self._update_video_progress(batch_id,index,live.get("progress",0),stage or "failed",str(exc),{stage:"failed"} if stage in failed_steps else None)
                result={"title":title,"success":False,"steps":failed_steps,"error":str(exc)}
            with self.lock:
                if self._is_cancelled(batch_id) and not result.get("success"):
                    raise BatchCancelled("Generation stopped by user.")
                videos=self.batches[batch_id]["videos"];videos[index].update(result,status="done" if result.get("success") else "failed")
                self.batches[batch_id]["completed"]+=1
                if not result.get("success"):self.batches[batch_id]["failed"]+=1
                self.batches[batch_id]["progress"]=round(100*self.batches[batch_id]["completed"]/len(titles))
            self._clear_gpu()
            if index<len(titles)-1:
                try:
                    delay=max(0,int(__import__('os').getenv("BATCH_DELAY_SECONDS","10")))
                    event=self.cancel_events.get(batch_id)
                    if event and event.wait(delay):
                        self._check_cancel(batch_id)
                    elif not event:
                        time.sleep(delay)
                except BatchCancelled:
                    raise
                except Exception:
                    pass
        with self.lock:
            self._check_cancel(batch_id)
            self.batches[batch_id].update(status="completed",finished_at=datetime.utcnow().isoformat()+"Z")
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
    def _process_single_video(self,title,index,duration,niche,batch_id,auto_upload,privacy,custom_script=None,resolution="720p",voice_speed="medium"):
        self._check_cancel(batch_id)
        steps={name:"pending" for name in VIDEO_STEP_KEYS}
        base=self._title_video_stem(title);self.sequence+=1;serial=self.sequence
        result={"title":title,"success":False,"steps":steps,"video_path":None,"subtitle_path":None,"youtube_url":None,"seo":None,"error":None}
        report=lambda pct,stage,message,step_updates=None:self._update_video_progress(batch_id,index,pct,stage,message,step_updates)
        model_name=getattr(self.scripts,"model","local AI model")
        if custom_script is not None:script_message=f"Submitting your draft to Ollama {model_name} for analysis and rewrite…"
        elif getattr(self.scripts,"provider","ollama")=="openrouter":script_message=f"Drafting with OpenRouter {getattr(self.scripts,'openrouter_model','free model')}, then asking Ollama {model_name} to review and finalize…"
        elif self.scripts.enabled:script_message=f"Asking Ollama {model_name} to write narration and plan scenes…"
        else:script_message="Creating narration and scene plan from the built-in template…"
        report(1,"script",script_message)
        try:
            self._check_cancel(batch_id)
            if custom_script is not None:
                script=self.scripts.generate_custom_script(title,custom_script,duration)
            else:
                script=self.scripts.generate_script(title,duration)
            self._check_cancel(batch_id)
            result["script"]=script;steps["script"]="done"
            script_source=getattr(self.scripts,"last_source",None)
            ready_message=f"Script and scene plan ready · {script_source}" if script_source else "Script and scene plan ready"
            report(8,"script",ready_message,{"script":"done"})
        except BatchCancelled:
            raise
        except Exception as exc:
            steps["script"]="failed";result["error"]=f"Script generation failed: {exc}"
            report(8,"script",result["error"],{"script":"failed"});return result
        if env_bool("USE_LOCAL_IMAGES",False):
            self._release_ollama_for_gpu()
        scenes=script.get("scenes",[])
        media=[None]*len(scenes)
        stock_video_count=0
        local_images=env_bool("USE_LOCAL_IMAGES",False) and self.local.available

        # Prefer a relevant stock clip, then a locally downloaded stock still; only
        # generate a custom still when neither provider has usable stock media.
        for i,scene in enumerate(scenes):
            self._check_cancel(batch_id)
            pct=8+int(20*i/max(1,len(scenes)))
            search_query=(scene.get("search_keywords") or scene.get("visual_description")
                          or scene.get("narration") or title)
            if self.fetcher.api_key or self.fetcher.pixabay.api_key:
                report(pct,"images",f"Searching Pexels and Pixabay for scene {i+1}; choosing the closest available stock clip…")
                stock_clip=self.fetcher.fetch_clip(search_query,serial*100+i,min_dur=5)
            else:
                stock_clip=None
                report(pct,"images",f"No stock API key configured; preparing a fallback visual for scene {i+1} of {len(scenes)}…")
            self._check_cancel(batch_id)
            if stock_clip:
                media[i]={"type":"video","path":stock_clip}
                stock_video_count+=1
                provider=self.fetcher.last_source or "stock"
                report(8+int(20*(i+1)/max(1,len(scenes))),"images",f"Selected the closest available {provider} stock clip for scene {i+1} of {len(scenes)}")
                continue

            report(pct,"images",f"No usable stock clip found; searching Pexels and Pixabay for a stock still for scene {i+1}…")
            image_path=self.images.generate_scene_image(scene,serial*100+i)
            self._check_cancel(batch_id)
            if self.images.last_source=="Gradient" and local_images:
                report(pct,"images",f"No usable stock image found; creating a local SDXL fallback for scene {i+1}…")
                generated=self.local.generate_image(scene.get("visual_description",title),serial*100+i)
                self._check_cancel(batch_id)
                if generated:
                    image_path=generated
                    self.images.last_source="SDXL"
            media[i]={"type":"image","path":image_path}
            provider=self.images.last_source or "fallback"
            report(8+int(20*(i+1)/max(1,len(scenes))),"images",f"Prepared {provider} still visual for scene {i+1} of {len(scenes)}")

        steps["images"]="done" if scenes and all(media) else "failed"
        fallback_count=sum(1 for item in media if item and item["type"]=="image")
        report(28,"images",f"Selected stock footage for {stock_video_count} scene(s); prepared still visuals for {fallback_count}",{"images":steps["images"]})

        for i in range(len(scenes)):
            pct=28+int(17*i/max(1,len(scenes)))
            if media[i] and media[i]["type"]=="video":
                report(pct,"clips",f"Using the selected stock footage for scene {i+1} of {len(scenes)}…")
            else:
                report(pct,"clips",f"Using the still fallback for scene {i+1} of {len(scenes)} with standard transitions…")
            report(28+int(17*(i+1)/max(1,len(scenes))),"clips",f"Scene {i+1} footage and transitions ready")
        steps["clips"]="done" if any(m and m["type"]=="video" for m in media) else "fallback"
        stock_count=sum(1 for item in media if item and item["type"]=="video")
        clip_summary=f"Stock footage ready for {stock_count} scene(s)" if stock_count else "Using still fallbacks with standard transitions"
        report(45,"clips",clip_summary,{"clips":steps["clips"]})
        audio_paths=[]
        if self.audio.voice_engine == "chatterbox":
            # Ollama may otherwise occupy most of an 8 GB GPU while Chatterbox loads.
            self._release_ollama_for_gpu()
        try:
            for i,scene in enumerate(scenes):
                self._check_cancel(batch_id)
                pct=45+int(15*i/max(1,len(scenes)))
                engine_label="local Chatterbox (Edge TTS fallback available)" if self.audio.voice_engine == "chatterbox" else "Edge TTS (offline voice fallback available)"
                pace_label={"slower":"Slower","slow":"Slow","medium":"Medium","faster":"Faster"}[voice_speed]
                report(pct,"audio",f"Generating narration {i+1} of {len(scenes)} with {engine_label} · {pace_label} pace…")
                try:
                    audio_path=self.audio.generate(scene.get("narration",title),serial*100+i,speed=voice_speed,cancel_check=lambda:self._check_cancel(batch_id))
                    self._check_cancel(batch_id)
                    if not os.path.isfile(audio_path) or os.path.getsize(audio_path)==0:
                        raise RuntimeError("Narration output is missing or empty.")
                    audio_paths.append(audio_path)
                    if self.audio.last_warning and not result.get("voice_warning"):
                        result["voice_warning"]=self.audio.last_warning
                        result["voice_fallback_engine"]=self.audio.last_engine
                    report(45+int(15*(i+1)/max(1,len(scenes))),"audio",f"Narration audio {i+1} of {len(scenes)} ready with {self.audio.last_engine} at {pace_label} pace")
                except NarrationCancelled:
                    raise BatchCancelled("Generation stopped by user.")
                except Exception as exc:
                    if self._is_cancelled(batch_id):
                        raise BatchCancelled("Generation stopped by user.") from exc
                    steps["audio"]="failed"
                    result["error"]=f"Voiceover generation failed in scene {i+1}: {exc}"
                    report(pct,"audio",result["error"],{"audio":"failed"});return result
        finally:
            self.audio.release_chatterbox_model()
        self._check_cancel(batch_id)
        steps["audio"]="done" if audio_paths else "failed"
        report(60,"audio",f"Narration ready for {len(audio_paths)} scene(s)",{"audio":steps["audio"]})
        report(60,"assembly","Arranging scene order and preparing transitions…")
        assembly_percent=[60]
        try:
            def assembly_progress(fraction):
                self._check_cancel(batch_id)
                assembly_percent[0]=60+int(22*max(0,min(1,fraction)))
                encoder=self.assembler.last_video_encoder or "video encoder"
                report(assembly_percent[0],"encoding",f"Encoding MP4 with {encoder}… {int(100*max(0,min(1,fraction)))}%")
            def assembly_stage(stage,message):
                report(assembly_percent[0],stage,message)
            video_path=self.assembler.create(script,media,audio_paths,base,progress_callback=assembly_progress,video_title=title,resolution=resolution,use_nvenc=env_bool("USE_NVENC",True),stage_callback=assembly_stage,cancel_check=lambda:self._check_cancel(batch_id));result["video_path"]=video_path
            result["video_encoder"]=self.assembler.last_video_encoder
            subtitle_path=os.path.splitext(video_path)[0]+".srt"
            result["subtitle_path"]=subtitle_path if os.path.isfile(subtitle_path) else None
            steps["assembly"]="done";steps["subtitles"]="done" if result["subtitle_path"] else "failed";steps["music"]="done" if self.assembler.last_music_style else "fallback";steps["encoding"]="done"
            if self.assembler.last_music_error:result["music_warning"]=self.assembler.last_music_error
            report(82,"encoding",f"MP4 encoded with {self.assembler.last_video_encoder}",{"assembly":"done","subtitles":steps["subtitles"],"music":steps["music"],"encoding":"done"})
        except BatchCancelled:
            raise
        except Exception as exc:
            result["error"]=f"Video assembly failed: {exc}"
            with self.lock:
                current=self.batches[batch_id]["videos"][index].get("current_stage")
            failed_step=current if current in steps else "assembly"
            steps[failed_step]="failed"
            report(82,failed_step,result["error"],{failed_step:"failed"});return result
        self._check_cancel(batch_id)
        seo_message=f"Asking Ollama {getattr(self.seo,'model','local model')} to prepare title, description and tags (built-in fallback available)…" if self.seo.enabled else "Preparing YouTube metadata with the built-in SEO template…"
        report(83,"seo",seo_message)
        try:
            seo=self.seo.generate_seo_package(title,script,niche)
            self._check_cancel(batch_id)
            result["seo"]=seo;steps["seo"]="done"
            report(87,"seo","YouTube title, description and tags ready",{"seo":"done"})
        except BatchCancelled:
            raise
        except Exception as exc:
            steps["seo"]="failed";result["error"]=f"SEO generation failed: {exc}"
            report(87,"seo",result["error"],{"seo":"failed"});return result
        report(88,"thumbnail","Creating a branded thumbnail from the scene visuals and title graphics…")
        thumbnail_background=next((item.get("path") for item in media if item and item.get("type")=="image"),None)
        try:
            thumb=self.thumbnails.generate(seo,title,serial,background_path=thumbnail_background,scenes=scenes,media_items=media)
            self._check_cancel(batch_id)
            result["thumbnail_path"]=thumb;steps["thumbnail"]="done"
            report(92,"thumbnail","Thumbnail artwork ready",{"thumbnail":"done"})
        except BatchCancelled:
            raise
        except Exception as exc:
            steps["thumbnail"]="failed";result["error"]=f"Thumbnail generation failed: {exc}"
            report(92,"thumbnail",result["error"],{"thumbnail":"failed"});return result
        result["success"]=True
        if auto_upload and not self._is_cancelled(batch_id):
            report(94,"upload","Starting resumable upload to your YouTube channel…")
            def upload_progress(fraction):
                report(94+int(5*max(0,min(1,fraction))),"upload",f"Uploading video to YouTube… {int(100*max(0,min(1,fraction)))}%")
            try:
                with self.upload_lock:
                    upload=self.youtube.upload_video(video_path,seo,thumb,privacy,progress_callback=upload_progress,cancel_check=lambda:self._check_cancel(batch_id))
            except BatchCancelled:
                steps["upload"]="skipped"
                result["upload_cancelled"]=True
                report(99,"upload","Upload stopped by user; generated video is still available.",{"upload":"skipped"})
            else:
                if upload.get("success"):result["youtube_url"]=upload.get("url");result["youtube_video_id"]=upload.get("video_id");steps["upload"]="done"
                else:steps["upload"]="failed";result["upload_error"]=upload.get("error");result["success"]=False
                report(99,"upload","Upload complete" if result["success"] else f"Upload failed: {result.get('upload_error','Unknown error')}",{"upload":steps["upload"]})
        else:
            steps["upload"]="skipped"
            message="Auto-upload skipped because generation was stopped." if auto_upload else "Auto-upload is off; YouTube upload skipped"
            report(94,"upload",message,{"upload":"skipped"})
        steps["finalize"]="done"
        report(100,"finalize","Video complete and ready to download" if result["success"] else "Video created, but publishing did not complete",{"finalize":"done"})
        return result
    def start_manual_publish(self,batch_id,index):
        with self.lock:
            batch=self.batches.get(batch_id)
            if not batch:
                return False,"Batch not found."
            if batch.get("auto_upload"):
                return False,"This batch was configured for automatic publishing."
            videos=batch.get("videos",[])
            if index<0 or index>=len(videos):
                return False,"Video not found."
            video=videos[index]
            if video.get("status")!="done" or not video.get("video_path") or not video.get("seo"):
                return False,"This video is not ready to publish."
            if video.get("youtube_url"):
                return False,"This video is already published."
            if video.get("upload_status")=="uploading":
                return False,"This video is already being published."
            video.update(upload_status="uploading",upload_progress=0,upload_error=None,upload_message="Preparing YouTube upload…")
            video.setdefault("steps",{})["upload"]="running"
            video.setdefault("step_messages",{})["upload"]="Preparing YouTube upload…"
            privacy=batch.get("privacy","private")
        thread=threading.Thread(target=self._manual_publish_worker,args=(batch_id,index,privacy),daemon=True,name=f"youtube-upload-{batch_id}-{index}")
        thread.start()
        return True,"Publishing started."

    def _manual_publish_worker(self,batch_id,index,privacy):
        with self.lock:
            batch=self.batches.get(batch_id)
            if not batch or index>=len(batch.get("videos",[])):
                return
            video=batch["videos"][index]
            video_path=video["video_path"]
            seo=copy.deepcopy(video["seo"])
            thumbnail_path=video.get("thumbnail_path")

        def report_upload(fraction):
            percent=int(100*max(0,min(1,float(fraction))))
            with self.lock:
                batch=self.batches.get(batch_id)
                if not batch or index>=len(batch.get("videos",[])):
                    return
                current=batch["videos"][index]
                current.update(upload_progress=percent,upload_message=f"Uploading to YouTube… {percent}%")
                current.setdefault("step_messages",{})["upload"]=current["upload_message"]

        try:
            with self.upload_lock:
                upload=self.youtube.upload_video(video_path,seo,thumbnail_path,privacy,progress_callback=report_upload)
            with self.lock:
                batch=self.batches.get(batch_id)
                if not batch or index>=len(batch.get("videos",[])):
                    return
                video=batch["videos"][index]
                if upload.get("success"):
                    video.update(upload_status="done",upload_progress=100,upload_error=None,upload_message="Published to YouTube.",youtube_url=upload.get("url"),youtube_video_id=upload.get("video_id"))
                    video.setdefault("steps",{})["upload"]="done"
                    video.setdefault("step_messages",{})["upload"]="Published to YouTube."
                else:
                    error=upload.get("error") or "YouTube upload failed."
                    video.update(upload_status="failed",upload_error=error,upload_message=error)
                    video.setdefault("steps",{})["upload"]="failed"
                    video.setdefault("step_messages",{})["upload"]=error
        except Exception as exc:
            error=f"{type(exc).__name__}: {exc}"
            print(f"[youtube] Manual publish failed for {batch_id}/{index}: {error}")
            with self.lock:
                batch=self.batches.get(batch_id)
                if batch and index<len(batch.get("videos",[])):
                    video=batch["videos"][index]
                    video.update(upload_status="failed",upload_error=error,upload_message=error)
                    video.setdefault("steps",{})["upload"]="failed"
                    video.setdefault("step_messages",{})["upload"]=error

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

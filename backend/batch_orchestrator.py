"""Background, sequential batch controller for the complete video pipeline."""
import threading, time, uuid, os, copy, re, unicodedata, traceback
from datetime import datetime
from common import DIRS
from script_generator import ScriptGenerator
from audio_generator import AudioGenerator, NarrationCancelled
from image_generator import ImageGenerator
from video_fetcher import VideoFetcher
from video_assembler import VideoAssembler
from thumbnail_generator import ThumbnailGenerator
from youtube_seo import YouTubeSEO
from youtube_uploader import YouTubeUploader

VIDEO_STEP_KEYS=("script","images","clips","audio","assembly","subtitles","music","encoding","seo","thumbnail","upload","finalize")


class BatchCancelled(Exception):
    """Cooperative cancellation requested by the user."""


class BatchOrchestrator:
    def __init__(self):
        self.scripts=ScriptGenerator();self.audio=AudioGenerator();self.images=ImageGenerator();self.fetcher=VideoFetcher();self.assembler=VideoAssembler();self.thumbnails=ThumbnailGenerator();self.seo=YouTubeSEO();self.youtube=YouTubeUploader()
        self.batches={};self.lock=threading.RLock();self.pipeline_lock=threading.Lock();self.upload_lock=threading.Lock();self.cancel_events={};self.retry_contexts={};self.active_batch_id=None;self.sequence=int(time.time())
    def start_batch(self,titles:list,duration:int,niche:str,auto_upload:bool,privacy:str,custom_scripts=None,resolution="720p",voice_speed="slow",thumbnail_only=False)->str:
        batch_id=datetime.utcnow().strftime("%Y%m%dT%H%M%S")+"-"+uuid.uuid4().hex[:6]
        data={"batch_id":batch_id,"created_at":datetime.utcnow().isoformat()+"Z","status":"queued","total":len(titles),"completed":0,"failed":0,"cancelled":0,"progress":0,"resolution":resolution,"voice_speed":voice_speed,"thumbnail_only":thumbnail_only,"auto_upload":auto_upload,"privacy":privacy,"videos":[{"title":t,"thumbnail_only":thumbnail_only,"status":"waiting","progress":0,"current_stage":"Waiting","progress_message":"Queued","upload_status":"not_requested","steps":{key:"pending" for key in VIDEO_STEP_KEYS},"step_messages":{}} for t in titles],"error":None}
        with self.lock:
            self.batches[batch_id]=data
            self.cancel_events[batch_id]=threading.Event()
        threading.Thread(target=self._process_batch,args=(batch_id,titles,duration,niche,auto_upload,privacy,custom_scripts,resolution,voice_speed,thumbnail_only),daemon=True,name=f"batch-{batch_id}").start()
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

    def _save_retry_context(self,batch_id,index,**values):
        with self.lock:
            context=self.retry_contexts.setdefault((batch_id,index),{})
            context.update(values)
            return context

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
            if stage in steps and steps.get(stage) in {"pending","failed"}:steps[stage]="running"
            messages=video.setdefault("step_messages",{})
            messages[stage]=message
            for key,value in (step_updates or {}).items():
                if key in steps:
                    steps[key]=value
                    if value=="skipped":messages[key]="Skipped because only thumbnails were requested." if batch.get("thumbnail_only") else message
            video.update(progress=progress,current_stage=stage,progress_message=message,steps=steps,step_messages=messages)
            if stage=="upload":
                video.update(upload_status="uploading",upload_progress=0,upload_error=None,upload_message=message)
            batch["progress"]=round(100*(batch["completed"]+progress/100)/max(1,batch["total"]))
    def _process_batch(self,batch_id,titles,duration,niche,auto_upload,privacy,custom_scripts=None,resolution="720p",voice_speed="slow",thumbnail_only=False):
        # Serialize independent batches too: avoid overlapping CPU-heavy media pipelines.
        try:
            with self.pipeline_lock:
                with self.lock:
                    self.active_batch_id=batch_id
                try:
                    self._run_batch_locked(batch_id,titles,duration,niche,auto_upload,privacy,custom_scripts,resolution,voice_speed,thumbnail_only)
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
    def _run_batch_locked(self,batch_id,titles,duration,niche,auto_upload,privacy,custom_scripts=None,resolution="720p",voice_speed="slow",thumbnail_only=False):
        self._check_cancel(batch_id)
        self._set(batch_id,status="running")
        for index,title in enumerate(titles):
            self._check_cancel(batch_id)
            with self.lock:self.batches[batch_id]["videos"][index].update(status="processing",started_at=datetime.utcnow().isoformat()+"Z")
            custom_script=custom_scripts[index] if custom_scripts and index<len(custom_scripts) else None
            try:result=self._process_single_video(title,index,duration,niche,batch_id,auto_upload,privacy,custom_script,resolution,voice_speed,thumbnail_only)
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
                upload_state=(result.get("steps") or {}).get("upload")
                if upload_state=="done":videos[index].update(upload_status="done",upload_progress=100,upload_error=None,upload_message="Published to YouTube.")
                elif upload_state=="failed":videos[index].update(upload_status="failed",upload_error=result.get("upload_error") or "YouTube upload failed.",upload_message=result.get("upload_error") or "YouTube upload failed.")
                elif upload_state=="skipped":videos[index].update(upload_status="not_requested",upload_message="Upload skipped.")
                self.batches[batch_id]["completed"]+=1
                if not result.get("success"):self.batches[batch_id]["failed"]+=1
                self.batches[batch_id]["progress"]=round(100*self.batches[batch_id]["completed"]/len(titles))
            if index<len(titles)-1 and not thumbnail_only:
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
    def _process_single_video(self,title,index,duration,niche,batch_id,auto_upload,privacy,custom_script=None,resolution="720p",voice_speed="slow",thumbnail_only=False,retry_from=None):
        self._check_cancel(batch_id)
        key=(batch_id,index)
        with self.lock:
            context=self.retry_contexts.setdefault(key,{})
            saved_video=copy.deepcopy(self.batches[batch_id]["videos"][index]) if retry_from else {}
        if retry_from:
            steps=copy.deepcopy(saved_video.get("steps",{name:"pending" for name in VIDEO_STEP_KEYS}))
            result={name:copy.deepcopy(saved_video.get(name) or context.get(name)) for name in ("script","video_path","subtitle_path","youtube_url","youtube_video_id","seo","thumbnail_path","video_encoder","music_warning","voice_warning","voice_fallback_engine")}
            result.update(title=title,success=False,steps=steps,error=None)
            base=context.get("base")
            serial=context.get("serial")
            script=context.get("script") or saved_video.get("script")
            media=copy.deepcopy(context.get("media",[]))
            audio_paths=list(context.get("audio_paths",[]))
            if not base or serial is None:
                steps[retry_from]="failed";result["error"]="Retry data is unavailable for this step; start a new video batch."
                return result
        else:
            steps={name:"pending" for name in VIDEO_STEP_KEYS}
            base=self._title_video_stem(title);self.sequence+=1;serial=self.sequence
            script=None;media=[];audio_paths=[]
            result={"title":title,"success":False,"steps":steps,"video_path":None,"subtitle_path":None,"youtube_url":None,"seo":None,"error":None}
        context.update(title=title,index=index,duration=duration,niche=niche,batch_id=batch_id,auto_upload=auto_upload,privacy=privacy,custom_script=custom_script,resolution=resolution,voice_speed=voice_speed,thumbnail_only=thumbnail_only,base=base,serial=serial)
        step_position={name:i for i,name in enumerate(VIDEO_STEP_KEYS)}
        retry_position=step_position.get(retry_from,0)
        should_run=lambda step: retry_from is None or step_position[step]>=retry_position
        report=lambda pct,stage,message,step_updates=None:self._update_video_progress(batch_id,index,pct,stage,message,step_updates)
        model_name=getattr(self.scripts,"model","") or "selected model"
        provider_name=getattr(self.scripts,"provider_name","selected provider")
        if should_run("script") or not script:
            if custom_script is not None:script_message=f"Sending your draft to {provider_name} {model_name} for editing and scene planning…"
            else:script_message=f"Asking {provider_name} {model_name} to generate the final narration and scene plan…"
            report(1,"script",script_message)
            try:
                self._check_cancel(batch_id)
                if custom_script is not None:
                    script=self.scripts.generate_custom_script(title,custom_script,duration)
                else:
                    script=self.scripts.generate_script(title,duration)
                self._check_cancel(batch_id)
                context.update(script=script,media=[],audio_paths=[],video_path=None,subtitle_path=None,seo=None,thumbnail_path=None)
                result["script"]=script;steps["script"]="done"
                if retry_from=="script":
                    media=[];audio_paths=[]
                    for name in ("video_path","subtitle_path","seo","thumbnail_path","youtube_url","youtube_video_id"):
                        result[name]=None
                script_source=getattr(self.scripts,"last_source",None)
                ready_message=f"Script and scene plan ready · {script_source}" if script_source else "Script and scene plan ready"
                report(8,"script",ready_message,{"script":"done"})
            except BatchCancelled:
                raise
            except Exception as exc:
                steps["script"]="failed";result["error"]=f"Script generation failed: {exc}"
                report(8,"script",result["error"],{"script":"failed"});return result
        else:
            script=context.get("script") or script
        if not script:
            steps[retry_from or "script"]="failed";result["error"]="The saved script is unavailable; retry the script step first."
            return result
        scenes=script.get("scenes",[])
        if retry_from=="script" or len(media)!=len(scenes):
            media=[None]*len(scenes)
        if len(audio_paths)!=len(scenes):
            audio_paths=(audio_paths+[None]*len(scenes))[:len(scenes)]
        context.update(script=script,media=media,audio_paths=audio_paths)
        stock_video_count=0
        thumbnail_scene_indices=set(range(len(scenes)))
        if thumbnail_only and len(scenes)>5:
            thumbnail_scene_indices={round(i*(len(scenes)-1)/4) for i in range(5)}
        required_visual_indices=thumbnail_scene_indices if thumbnail_only else set(range(len(scenes)))
        run_images=should_run("images") or any(not media[i] or not media[i].get("path") or not os.path.isfile(media[i]["path"]) for i in required_visual_indices)

        # Prefer relevant stock clips, followed by stock stills; use a designed
        # gradient fallback if neither provider has usable stock media.
        for i,scene in enumerate(scenes):
            if not run_images:
                break
            self._check_cancel(batch_id)
            pct=8+int(20*i/max(1,len(scenes)))
            if thumbnail_only and i not in thumbnail_scene_indices:
                report(pct,"images",f"Skipping scene {i+1}; the thumbnail layout uses at most five scene visuals…")
                continue
            if media[i] and media[i].get("path") and os.path.isfile(media[i]["path"]):
                continue
            if thumbnail_only:
                stock_clip=None
                report(pct,"images",f"Finding a thumbnail-ready still for scene {i+1} of {len(scenes)}…")
            elif self.fetcher.api_key or self.fetcher.pixabay.api_key:
                report(pct,"images",f"Searching Pexels and Pixabay for scene {i+1}; choosing the closest available stock clip…")
                stock_clip=self.fetcher.fetch_clip(scene,serial*100+i,min_dur=5)
            else:
                stock_clip=None
                report(pct,"images",f"No stock API key configured; preparing a fallback visual for scene {i+1} of {len(scenes)}…")
            self._check_cancel(batch_id)
            if stock_clip:
                media[i]={"type":"video","path":stock_clip}
                self._save_retry_context(batch_id,index,media=copy.deepcopy(media))
                stock_video_count+=1
                provider=self.fetcher.last_source or "stock"
                report(8+int(20*(i+1)/max(1,len(scenes))),"images",f"Selected the closest available {provider} stock clip for scene {i+1} of {len(scenes)}")
                continue

            if not thumbnail_only:report(pct,"images",f"No usable stock clip found; searching Pexels and Pixabay for a stock still for scene {i+1}…")
            image_path=self.images.generate_scene_image(scene,serial*100+i)
            self._check_cancel(batch_id)
            media[i]={"type":"image","path":image_path}
            self._save_retry_context(batch_id,index,media=copy.deepcopy(media))
            provider=self.images.last_source or "fallback"
            report(8+int(20*(i+1)/max(1,len(scenes))),"images",f"Prepared {provider} still visual for scene {i+1} of {len(scenes)}")

        if run_images:
            visuals_ready=bool(thumbnail_scene_indices and all(media[i] for i in thumbnail_scene_indices)) if thumbnail_only else bool(scenes and all(media))
            steps["images"]="done" if visuals_ready else "failed"
            fallback_count=sum(1 for item in media if item and item["type"]=="image")
            if thumbnail_only:
                report(28,"images",f"Prepared {fallback_count} still visual(s) for thumbnail testing",{"images":steps["images"]})
            else:
                stock_video_count=sum(1 for item in media if item and item["type"]=="video")
                report(28,"images",f"Selected stock footage for {stock_video_count} scene(s); prepared still visuals for {fallback_count}",{"images":steps["images"]})

        if should_run("clips") or run_images:
            if thumbnail_only:
                steps["clips"]="skipped"
                clip_summary="Motion clips and transitions skipped for thumbnail-only mode"
            else:
                for i in range(len(scenes)):
                    pct=28+int(17*i/max(1,len(scenes)))
                    if media[i] and media[i]["type"]=="video":
                        report(pct,"clips",f"Using the selected stock footage for scene {i+1} of {len(scenes)}…")
                    else:
                        report(pct,"clips",f"Using the still fallback for scene {i+1} of {len(scenes)} with a randomized scene transition…")
                    report(28+int(17*(i+1)/max(1,len(scenes))),"clips",f"Scene {i+1} footage and transitions ready")
                steps["clips"]="done" if any(m and m["type"]=="video" for m in media) else "fallback"
                stock_count=sum(1 for item in media if item and item["type"]=="video")
                clip_summary=f"Stock footage ready for {stock_count} scene(s)" if stock_count else "Using still fallbacks with randomized transitions"
            report(45,"clips",clip_summary,{"clips":steps["clips"]})
        if thumbnail_only and retry_from:
            skipped_updates={key:"skipped" for key in ("audio","assembly","subtitles","music","encoding","upload")}
            for key,state in skipped_updates.items():steps[key]=state
            if should_run("seo"):
                report(50,"seo",f"Retrying {self.seo.provider_name} metadata generation with {self.seo.model or 'text model'}…",skipped_updates if should_run("audio") else None)
                try:
                    seo=self.seo.generate_seo_package(title,script,niche)
                    self._check_cancel(batch_id)
                    result["seo"]=seo;context["seo"]=seo;steps["seo"]="done"
                    report(70,"seo",f"Thumbnail text and scene labels ready · {self.seo.last_source}",{"seo":"done"})
                except BatchCancelled:
                    raise
                except Exception as exc:
                    steps["seo"]="failed";result["error"]=f"Thumbnail metadata generation failed: {exc}"
                    report(70,"seo",result["error"],{"seo":"failed"});return result
            else:
                seo=context.get("seo") or result.get("seo")
            if not seo:
                steps["seo"]="failed";result["error"]="Saved metadata is unavailable; retry the metadata step first."
                return result
            if should_run("thumbnail"):
                report(76,"thumbnail","Retrying thumbnail generation from saved scene visuals…")
                thumbnail_background=next((item.get("path") for item in media if item and item["type"]=="image"),None)
                try:
                    thumb=self.thumbnails.generate(seo,title,serial,background_path=thumbnail_background,scenes=scenes,media_items=media,image_generator=self.images,video_fetcher=None)
                    self._check_cancel(batch_id)
                    result["thumbnail_path"]=thumb;context["thumbnail_path"]=thumb;result["thumbnail_only"]=True
                    steps["thumbnail"]="done"
                    report(95,"thumbnail","Thumbnail artwork ready",{"thumbnail":"done"})
                except BatchCancelled:
                    raise
                except Exception as exc:
                    steps["thumbnail"]="failed";result["error"]=f"Thumbnail generation failed: {exc}"
                    report(95,"thumbnail",result["error"],{"thumbnail":"failed"});return result
            result["thumbnail_only"]=True;result["success"]=True
            steps["finalize"]="done"
            report(100,"finalize","Thumbnail ready — voiceover and video rendering were skipped.",{"finalize":"done"})
            return result
        if thumbnail_only:
            skipped_updates={key:"skipped" for key in ("audio","assembly","subtitles","music","encoding","upload")}
            for key,state in skipped_updates.items():steps[key]=state
            seo_message=f"Asking {self.seo.provider_name} {self.seo.model or 'text model'} to prepare thumbnail wording…"
            report(50,"seo",seo_message+" Video rendering is disabled for this batch.",skipped_updates)
            try:
                seo=self.seo.generate_seo_package(title,script,niche)
                self._check_cancel(batch_id)
                result["seo"]=seo;context["seo"]=seo;steps["seo"]="done"
                report(70,"seo",f"Thumbnail text and scene labels ready · {self.seo.last_source}",{"seo":"done"})
            except BatchCancelled:
                raise
            except Exception as exc:
                steps["seo"]="failed";result["error"]=f"Thumbnail metadata generation failed: {exc}"
                report(70,"seo",result["error"],{"seo":"failed"});return result
            report(76,"thumbnail","Building the template-matched thumbnail from script visuals…")
            thumbnail_background=next((item.get("path") for item in media if item and item.get("type")=="image"),None)
            try:
                thumb=self.thumbnails.generate(seo,title,serial,background_path=thumbnail_background,scenes=scenes,media_items=media,image_generator=self.images,video_fetcher=None)
                self._check_cancel(batch_id)
                result["thumbnail_path"]=thumb;context["thumbnail_path"]=thumb;result["thumbnail_only"]=True;result["success"]=True
                steps["thumbnail"]="done";steps["finalize"]="done"
                report(95,"thumbnail","Thumbnail artwork ready",{"thumbnail":"done"})
                report(100,"finalize","Thumbnail ready — voiceover and video rendering were skipped.",{"finalize":"done"})
            except BatchCancelled:
                raise
            except Exception as exc:
                steps["thumbnail"]="failed";result["error"]=f"Thumbnail generation failed: {exc}"
                report(95,"thumbnail",result["error"],{"thumbnail":"failed"});return result
            return result
        run_audio=should_run("audio") or any(not audio_paths[i] or not os.path.isfile(audio_paths[i]) or os.path.getsize(audio_paths[i])==0 for i in range(len(scenes)))
        if run_audio:
            for i,scene in enumerate(scenes):
                if audio_paths[i] and os.path.isfile(audio_paths[i]) and os.path.getsize(audio_paths[i])>0:
                    continue
                self._check_cancel(batch_id)
                pct=45+int(15*i/max(1,len(scenes)))
                pace_label={"slower":"Slower","slow":"Slow","medium":"Medium","faster":"Faster"}[voice_speed]
                report(pct,"audio",f"Generating narration {i+1} of {len(scenes)} with hosted Edge TTS · {pace_label} pace…")
                try:
                    audio_path=self.audio.generate(scene.get("narration",title),serial*100+i,speed=voice_speed,cancel_check=lambda:self._check_cancel(batch_id))
                    self._check_cancel(batch_id)
                    if not os.path.isfile(audio_path) or os.path.getsize(audio_path)==0:
                        raise RuntimeError("Narration output is missing or empty.")
                    audio_paths[i]=audio_path
                    context["audio_paths"]=list(audio_paths)
                    report(45+int(15*(i+1)/max(1,len(scenes))),"audio",f"Narration audio {i+1} of {len(scenes)} ready with {self.audio.last_engine} at {pace_label} pace")
                except NarrationCancelled:
                    raise BatchCancelled("Generation stopped by user.")
                except Exception as exc:
                    if self._is_cancelled(batch_id):
                        raise BatchCancelled("Generation stopped by user.") from exc
                    steps["audio"]="failed"
                    result["error"]=f"Voiceover generation failed in scene {i+1}: {exc}"
                    report(pct,"audio",result["error"],{"audio":"failed"});return result
            self._check_cancel(batch_id)
            steps["audio"]="done" if all(audio_paths) else "failed"
            report(60,"audio",f"Narration ready for {len(scenes)} scene(s)",{"audio":steps["audio"]})

        run_assembly=should_run("assembly") or retry_from in {"subtitles","music","encoding"} or run_images or run_audio
        if run_assembly:
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
                video_path=self.assembler.create(script,media,audio_paths,base,progress_callback=assembly_progress,video_title=title,resolution=resolution,stage_callback=assembly_stage,cancel_check=lambda:self._check_cancel(batch_id));result["video_path"]=video_path
                result["video_encoder"]=self.assembler.last_video_encoder
                subtitle_path=os.path.splitext(video_path)[0]+".srt"
                result["subtitle_path"]=subtitle_path if os.path.isfile(subtitle_path) else None
                context.update(video_path=video_path,subtitle_path=result["subtitle_path"])
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
        video_path=result.get("video_path")
        self._check_cancel(batch_id)
        if should_run("seo"):
            seo_message=f"Asking {self.seo.provider_name} {self.seo.model or 'text model'} to prepare title, description and tags…"
            report(83,"seo",seo_message)
            try:
                seo=self.seo.generate_seo_package(title,script,niche)
                self._check_cancel(batch_id)
                result["seo"]=seo;context["seo"]=seo;steps["seo"]="done"
                report(87,"seo",f"YouTube metadata ready · {self.seo.last_source}",{"seo":"done"})
            except BatchCancelled:
                raise
            except Exception as exc:
                steps["seo"]="failed";result["error"]=f"SEO generation failed: {exc}"
                report(87,"seo",result["error"],{"seo":"failed"});return result
        else:
            seo=context.get("seo") or result.get("seo")
        if not seo:
            steps["seo"]="failed";result["error"]="Saved YouTube metadata is unavailable; retry the metadata step first."
            return result
        if should_run("thumbnail"):
            report(88,"thumbnail","Building a template-matched thumbnail from scene visuals and script subjects…")
            thumbnail_background=next((item.get("path") for item in media if item and item.get("type")=="image"),None)
            try:
                thumb=self.thumbnails.generate(seo,title,serial,background_path=thumbnail_background,scenes=scenes,media_items=media,image_generator=self.images,video_fetcher=self.fetcher)
                self._check_cancel(batch_id)
                result["thumbnail_path"]=thumb;context["thumbnail_path"]=thumb;steps["thumbnail"]="done"
                report(92,"thumbnail","Thumbnail artwork ready",{"thumbnail":"done"})
            except BatchCancelled:
                raise
            except Exception as exc:
                steps["thumbnail"]="failed";result["error"]=f"Thumbnail generation failed: {exc}"
                report(92,"thumbnail",result["error"],{"thumbnail":"failed"});return result
        thumb=result.get("thumbnail_path")
        result["success"]=True
        if should_run("upload") and auto_upload and not self._is_cancelled(batch_id):
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
                if result.get("youtube_url"):
                    context["youtube_url"]=result["youtube_url"]
                    context["youtube_video_id"]=result.get("youtube_video_id")
        elif should_run("upload"):
            steps["upload"]="skipped"
            message="Auto-upload skipped because generation was stopped." if auto_upload else "Auto-upload is off; YouTube upload skipped"
            report(94,"upload",message,{"upload":"skipped"})
        steps["finalize"]="done"
        report(100,"finalize","Video complete and ready to download" if result["success"] else "Video created, but publishing did not complete",{"finalize":"done"})
        return result

    def retry_failed_step(self,batch_id,index,step):
        if step not in VIDEO_STEP_KEYS:
            return False,"Unknown pipeline step."
        manual_publish=False
        with self.lock:
            batch=self.batches.get(batch_id)
            if not batch:
                return False,"Batch not found."
            videos=batch.get("videos",[])
            if index<0 or index>=len(videos):
                return False,"Video not found."
            video=videos[index]
            if batch.get("status") in {"queued","running","stopping"} or video.get("status")=="processing":
                return False,"Wait for the current batch operation to finish before retrying a step."
            steps=video.setdefault("steps",{})
            if steps.get(step)!="failed":
                return False,"Only failed steps can be retried."
            if step=="upload" and not batch.get("auto_upload"):
                manual_publish=True
            else:
                context=self.retry_contexts.get((batch_id,index))
                if not context or not context.get("base") or context.get("serial") is None:
                    return False,"Retry data is unavailable for this video. Start a new batch to regenerate it."
                if step!="script" and not (context.get("script") or video.get("script")):
                    return False,"The saved script is unavailable. Retry the script step first."
                old_status=video.get("status")
                if old_status in {"done","failed"}:
                    batch["completed"]=max(0,int(batch.get("completed",0))-1)
                if old_status=="failed":
                    batch["failed"]=max(0,int(batch.get("failed",0))-1)
                reset_index=VIDEO_STEP_KEYS.index(step)
                if step in {"subtitles","music","encoding"}:
                    reset_index=VIDEO_STEP_KEYS.index("assembly")
                for key in VIDEO_STEP_KEYS[reset_index:]:
                    steps[key]="pending"
                    video.setdefault("step_messages",{})[key]=f"Waiting to rerun after {step} succeeds."
                progress_step="assembly" if step in {"subtitles","music","encoding"} else step
                steps[progress_step]="running"
                video.update(status="processing",error=None,upload_error=None,progress_message=f"Retrying {step} while keeping completed earlier steps…",current_stage=progress_step)
                video.setdefault("step_messages",{})[step]=f"Retrying {step}…"
                batch.update(status="running",error=None)
                batch.pop("finished_at",None)
                batch["progress"]=round(100*(batch["completed"]+int(video.get("progress",0))/100)/max(1,batch.get("total",1)))
                self.cancel_events[batch_id]=threading.Event()
        if manual_publish:
            return self.start_manual_publish(batch_id,index)
        thread=threading.Thread(target=self._retry_failed_step_worker,args=(batch_id,index,step),daemon=True,name=f"retry-{step}-{batch_id}-{index}")
        thread.start()
        return True,f"Retrying {step}. Previously completed steps are being kept."

    def _retry_failed_step_worker(self,batch_id,index,step):
        with self.lock:
            context=dict(self.retry_contexts.get((batch_id,index),{}))
            title=context.get("title","")
        try:
            with self.pipeline_lock:
                with self.lock:self.active_batch_id=batch_id
                try:
                    result=self._process_single_video(title,index,context["duration"],context["niche"],batch_id,context["auto_upload"],context["privacy"],custom_script=context.get("custom_script"),resolution=context["resolution"],voice_speed=context["voice_speed"],thumbnail_only=context["thumbnail_only"],retry_from=step)
                finally:
                    with self.lock:
                        if self.active_batch_id==batch_id:self.active_batch_id=None
        except Exception as exc:
            print(f"[batch] Retry of {step} failed for {batch_id}/{index}: {type(exc).__name__}: {exc}")
            with self.lock:
                video=self.batches[batch_id]["videos"][index]
                steps=video.setdefault("steps",{})
                current=video.get("current_stage")
                failed_step=current if current in steps else step
                steps[failed_step]="failed"
                video.update(error=f"{type(exc).__name__}: {exc}",progress_message=f"{type(exc).__name__}: {exc}")
                result={"success":False,"steps":copy.deepcopy(steps),"error":video["error"]}
        with self.lock:
            batch=self.batches.get(batch_id)
            if not batch or index>=len(batch.get("videos",[])):
                return
            video=batch["videos"][index]
            success=bool(result.get("success"))
            video.update(result,status="done" if success else "failed")
            upload_state=(result.get("steps") or {}).get("upload")
            if upload_state=="done":
                video.update(upload_status="done",upload_progress=100,upload_error=None,upload_message="Published to YouTube.")
            elif upload_state=="failed":
                video.update(upload_status="failed",upload_error=result.get("upload_error") or "YouTube upload failed.",upload_message=result.get("upload_error") or "YouTube upload failed.")
            elif upload_state=="skipped":
                video.update(upload_status="not_requested",upload_message="Upload skipped.")
            batch["completed"]=min(batch.get("total",1),int(batch.get("completed",0))+1)
            if not success:batch["failed"]=int(batch.get("failed",0))+1
            all_finished=batch["completed"]+batch.get("cancelled",0)>=batch.get("total",1)
            if all_finished:
                batch.update(status="failed" if batch.get("failed",0) else "completed",finished_at=datetime.utcnow().isoformat()+"Z",error="One or more videos failed." if batch.get("failed",0) else None)
            else:
                batch.update(status="running",error=None)
            batch["progress"]=round(100*(batch["completed"]+batch.get("cancelled",0))/max(1,batch.get("total",1)))

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

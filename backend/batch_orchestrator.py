"""Background, sequential batch controller for the complete video pipeline."""
import gc, threading, time, uuid
from datetime import datetime
from common import DIRS
from script_generator import ScriptGenerator
from audio_generator import AudioGenerator
from image_generator import ImageGenerator
from video_fetcher import VideoFetcher
from local_ai_generator import LocalAIGenerator
from video_assembler import VideoAssembler
from thumbnail_generator import ThumbnailGenerator
from youtube_seo import YouTubeSEO
from youtube_uploader import YouTubeUploader

class BatchOrchestrator:
    def __init__(self):
        self.scripts=ScriptGenerator();self.audio=AudioGenerator();self.images=ImageGenerator();self.fetcher=VideoFetcher();self.local=LocalAIGenerator();self.assembler=VideoAssembler();self.thumbnails=ThumbnailGenerator(self.local);self.seo=YouTubeSEO();self.youtube=YouTubeUploader()
        self.batches={};self.lock=threading.RLock();self.pipeline_lock=threading.Lock();self.sequence=int(time.time())
    def start_batch(self,titles:list,duration:int,niche:str,auto_upload:bool,privacy:str)->str:
        batch_id=datetime.utcnow().strftime("%Y%m%dT%H%M%S")+"-"+uuid.uuid4().hex[:6]
        data={"batch_id":batch_id,"created_at":datetime.utcnow().isoformat()+"Z","status":"queued","total":len(titles),"completed":0,"failed":0,"progress":0,"videos":[{"title":t,"status":"waiting","steps":{}} for t in titles],"error":None}
        with self.lock:self.batches[batch_id]=data
        threading.Thread(target=self._process_batch,args=(batch_id,titles,duration,niche,auto_upload,privacy),daemon=True,name=f"batch-{batch_id}").start()
        return batch_id
    def get_status(self,batch_id):
        with self.lock:
            item=self.batches.get(batch_id)
            return dict(item) if item else None
    def list_batches(self):
        with self.lock:return [dict(v) for v in sorted(self.batches.values(),key=lambda x:x["created_at"],reverse=True)]
    def _set(self,batch_id,**kwargs):
        with self.lock:self.batches[batch_id].update(kwargs)
    def _process_batch(self,batch_id,titles,duration,niche,auto_upload,privacy):
        # Serialize independent batches too: never overlap GPU model loads or media assembly.
        with self.pipeline_lock:
            self._run_batch_locked(batch_id,titles,duration,niche,auto_upload,privacy)
    def _run_batch_locked(self,batch_id,titles,duration,niche,auto_upload,privacy):
        self._set(batch_id,status="running")
        for index,title in enumerate(titles):
            with self.lock:self.batches[batch_id]["videos"][index].update(status="processing",started_at=datetime.utcnow().isoformat()+"Z")
            try:result=self._process_single_video(title,index,duration,niche,batch_id,auto_upload,privacy)
            except Exception as exc:result={"title":title,"success":False,"steps":{},"error":str(exc)}
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
    def _process_single_video(self,title,index,duration,niche,batch_id,auto_upload,privacy):
        steps={name:"pending" for name in ("script","images","clips","audio","assembly","thumbnail","seo","upload")}
        base=f"{batch_id}_{index+1}";self.sequence+=1;serial=self.sequence
        result={"title":title,"success":False,"steps":steps,"video_path":None,"youtube_url":None,"seo":None,"error":None}
        try:
            script=self.scripts.generate_script(title,duration);result["script"]=script;steps["script"]="done"
        except Exception as exc:steps["script"]="failed";result["error"]=f"Script generation failed: {exc}";return result
        media=[];scene_images=[]
        for i,scene in enumerate(script.get("scenes",[])):
            path=self.local.generate_image(scene.get("visual_description",title),serial*100+i)
            if not path:path=self.images.generate_scene_image(scene,serial*100+i)
            scene_images.append(path);media.append({"type":"image","path":path})
        steps["images"]="done" if scene_images else "failed"
        # Try image-to-video in sequence; failed scenes retain their still image.
        for i,scene in enumerate(script.get("scenes",[])):
            clip=self.local.generate_video_clip(scene_images[i],scene.get("visual_description",title),serial*100+i)
            if not clip:clip=self.fetcher.fetch_clip(scene.get("search_keywords",title),serial*100+i,min_dur=5)
            if clip:media[i]={"type":"video","path":clip}
        steps["clips"]="done" if any(m["type"]=="video" for m in media) else "fallback"
        audio_paths=[]
        for i,scene in enumerate(script.get("scenes",[])):
            try:audio_paths.append(self.audio.generate(scene.get("narration",title),serial*100+i))
            except Exception as exc:print(f"[audio] Scene narration failed: {exc}");audio_paths.append("")
        steps["audio"]="done" if audio_paths else "failed"
        try:
            video_path=self.assembler.create(script,media,audio_paths,base);result["video_path"]=video_path;steps["assembly"]="done"
        except Exception as exc:steps["assembly"]="failed";result["error"]=f"Video assembly failed: {exc}";return result
        try:
            seo=self.seo.generate_seo_package(title,script,niche);result["seo"]=seo;steps["seo"]="done"
            thumb=self.thumbnails.generate(seo,title,serial);result["thumbnail_path"]=thumb;steps["thumbnail"]="done"
        except Exception as exc:steps["thumbnail"]="failed";result["error"]=str(exc);return result
        result["success"]=True
        if auto_upload:
            upload=self.youtube.upload_video(video_path,seo,thumb,privacy)
            if upload.get("success"):result["youtube_url"]=upload.get("url");result["youtube_video_id"]=upload.get("video_id");steps["upload"]="done"
            else:steps["upload"]="failed";result["upload_error"]=upload.get("error");result["success"]=False
        else:steps["upload"]="skipped"
        return result
    @staticmethod
    def _clear_gpu():
        try:
            import torch
            if torch.cuda.is_available():torch.cuda.empty_cache()
        except Exception:pass
        gc.collect()

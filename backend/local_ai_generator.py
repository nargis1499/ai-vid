"""Optional sequential SDXL Turbo and Stable Video Diffusion generation."""
import gc, os, threading
from common import DIRS, env_bool
class LocalAIGenerator:
    def __init__(self):
        self.enabled=env_bool("USE_LOCAL_IMAGES",False) or env_bool("USE_LOCAL_VIDEO",False)
        self.available=False;self.device_name=None
        self._gpu_lock=threading.Lock();self._gpu_in_use=False;self._gpu_operation=None
        try:
            import torch
            self.torch=torch;self.available=torch.cuda.is_available()
            if self.available:
                self.device_name=torch.cuda.get_device_name(0)
                print(f"[gpu] {self.device_name}")
            else:print("[gpu] CUDA unavailable; local AI generation disabled")
        except Exception as exc: self.torch=None;print(f"[gpu] PyTorch not available: {exc}")
    def gpu_status(self):
        enabled_images=env_bool("USE_LOCAL_IMAGES",False);enabled_video=env_bool("USE_LOCAL_VIDEO",False)
        with self._gpu_lock:
            using=self._gpu_in_use;operation=self._gpu_operation
        vram_total=vram_used=None
        if self.available:
            try:
                free,total=self.torch.cuda.mem_get_info(0);vram_total=round(total/(1024**3),1);vram_used=round((total-free)/(1024**3),1)
            except Exception:pass
        if not self.available:
            state="unavailable";message="NVIDIA CUDA GPU not detected by PyTorch; local AI generation is unavailable."
        elif using:
            state="active";message=f"NVIDIA GPU in use · {operation}"
        elif not (enabled_images or enabled_video):
            state="disabled";message=f"{self.device_name} detected · local AI generation is disabled in Settings."
        else:
            state="ready";message=f"{self.device_name} ready · not currently processing a GPU step."
        return {"state":state,"available":self.available,"enabled":enabled_images or enabled_video,"in_use":using,"device":self.device_name,"operation":operation,"vram_used_gb":vram_used,"vram_total_gb":vram_total,"message":message}
    def _set_gpu_in_use(self,operation):
        with self._gpu_lock:self._gpu_in_use=True;self._gpu_operation=operation
    def _clear_gpu_in_use(self):
        with self._gpu_lock:self._gpu_in_use=False;self._gpu_operation=None
    def generate_image(self,prompt:str,index:int)->str|None:
        if not self.available or not env_bool("USE_LOCAL_IMAGES",False):return None
        pipe=None
        try:
            from diffusers import AutoPipelineForText2Image
            pipe=AutoPipelineForText2Image.from_pretrained("stabilityai/sdxl-turbo",torch_dtype=self.torch.float16,variant="fp16")
            try: memory_budget=float(os.getenv("GPU_MEMORY_LIMIT_GB", "7"))
            except ValueError: memory_budget=7.0
            if memory_budget <= 5.0:
                pipe.enable_sequential_cpu_offload()
            else:
                pipe.enable_model_cpu_offload()
            self._set_gpu_in_use("SDXL image generation")
            try:image=pipe("cinematic, high quality, " + prompt,width=1280,height=720,num_inference_steps=4,guidance_scale=0.0).images[0]
            finally:self._clear_gpu_in_use()
            path=DIRS["local_ai"]/f"ai_image_{index}.png";image.save(path);return str(path)
        except Exception as exc:print(f"[gpu] SDXL failed: {exc}");return None
        finally:
            self._clear_gpu_in_use();self._release(pipe)
    def generate_video_clip(self,image_path:str,prompt:str,index:int)->str|None:
        if not self.available or not env_bool("USE_LOCAL_VIDEO",False):return None
        pipe=None
        try:
            from diffusers import StableVideoDiffusionPipeline
            from diffusers.utils import export_to_video
            from PIL import Image
            pipe=StableVideoDiffusionPipeline.from_pretrained("stabilityai/stable-video-diffusion-img2vid-xt",torch_dtype=self.torch.float16,variant="fp16");pipe.enable_model_cpu_offload()
            image=Image.open(image_path).convert("RGB").resize((1024,576))
            self._set_gpu_in_use("Stable Video Diffusion animation")
            try:result=pipe(image, num_frames=25,motion_bucket_id=127,fps=6,noise_aug_strength=.02,decode_chunk_size=8)
            finally:self._clear_gpu_in_use()
            path=DIRS["local_ai"]/f"ai_clip_{index}.mp4";export_to_video(result.frames[0],str(path),fps=6);return str(path)
        except Exception as exc:print(f"[gpu] SVD failed: {exc}");return None
        finally:
            self._clear_gpu_in_use();self._release(pipe)
    def _release(self,pipe):
        if pipe is not None:del pipe
        if self.torch is not None and self.available:
            try:self.torch.cuda.empty_cache()
            except Exception:pass
        gc.collect()

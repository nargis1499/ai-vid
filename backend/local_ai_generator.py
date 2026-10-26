"""Optional sequential SDXL Turbo and Stable Video Diffusion generation."""
import gc, os
from common import DIRS, env_bool
class LocalAIGenerator:
    def __init__(self):
        self.enabled=env_bool("USE_LOCAL_IMAGES",False) or env_bool("USE_LOCAL_VIDEO",False)
        self.available=False
        try:
            import torch
            self.torch=torch;self.available=torch.cuda.is_available()
            if self.available:print(f"[gpu] {torch.cuda.get_device_name(0)}")
            else:print("[gpu] CUDA unavailable; local AI generation disabled")
        except Exception as exc: self.torch=None;print(f"[gpu] PyTorch not available: {exc}")
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
            image=pipe("cinematic, high quality, " + prompt,width=1280,height=720,num_inference_steps=4,guidance_scale=0.0).images[0]
            path=DIRS["local_ai"]/f"ai_image_{index}.png";image.save(path);return str(path)
        except Exception as exc:print(f"[gpu] SDXL failed: {exc}");return None
        finally:self._release(pipe)
    def generate_video_clip(self,image_path:str,prompt:str,index:int)->str|None:
        if not self.available or not env_bool("USE_LOCAL_VIDEO",False):return None
        pipe=None
        try:
            from diffusers import StableVideoDiffusionPipeline
            from diffusers.utils import export_to_video
            from PIL import Image
            pipe=StableVideoDiffusionPipeline.from_pretrained("stabilityai/stable-video-diffusion-img2vid-xt",torch_dtype=self.torch.float16,variant="fp16");pipe.enable_model_cpu_offload()
            image=Image.open(image_path).convert("RGB").resize((1024,576))
            result=pipe(image, num_frames=25,motion_bucket_id=127,fps=6,noise_aug_strength=.02,decode_chunk_size=8)
            path=DIRS["local_ai"]/f"ai_clip_{index}.mp4";export_to_video(result.frames[0],str(path),fps=6);return str(path)
        except Exception as exc:print(f"[gpu] SVD failed: {exc}");return None
        finally:self._release(pipe)
    def _release(self,pipe):
        if pipe is not None:del pipe
        if self.torch is not None and self.available:
            try:self.torch.cuda.empty_cache()
            except Exception:pass
        gc.collect()

"""Assemble stills/clips and narration into an MP4 using MoviePy."""
import os
from common import DIRS

class VideoAssembler:
    def create(self,script:dict,media_items:list,audio_paths:list,video_id:str)->str:
        try:
            from PIL import Image
            # MoviePy 1.x still references Image.ANTIALIAS, removed in Pillow 10.
            if not hasattr(Image, "ANTIALIAS"):
                Image.ANTIALIAS = Image.Resampling.LANCZOS
        except Exception as exc:
            raise RuntimeError(f"Pillow could not be loaded ({type(exc).__name__}: {exc}). Reinstall the app's media dependencies.") from exc
        try:
            from moviepy.editor import (AudioFileClip, ImageClip, VideoFileClip, concatenate_videoclips)
            from moviepy.config import FFMPEG_BINARY
        except Exception as exc:
            details=str(exc)
            if "cannot load module more than once per process" in details.lower():
                hint="NumPy was initialized more than once. This commonly happens when a CUDA build upgrades NumPy; rebuild with the latest build-desktop-windows-cuda.bat, which pins NumPy 1.26.4."
            else:
                hint="The desktop build may be missing MoviePy; rebuild it with the latest packaging script or reinstall moviepy==1.0.3."
            raise RuntimeError(f"MoviePy could not be loaded ({type(exc).__name__}: {details}). {hint}") from exc
        try:
            import subprocess
            check=subprocess.run([FFMPEG_BINARY, "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10, check=False)
            if check.returncode:
                detail=check.stderr.decode(errors="replace")[-400:]
                raise RuntimeError(detail or f"FFmpeg exited with code {check.returncode}")
        except Exception as exc:
            raise RuntimeError(f"FFmpeg could not be started from {FFMPEG_BINARY!r} ({type(exc).__name__}: {exc}). Set a valid ffmpeg.exe in CreatorFlow Settings or rebuild with imageio-ffmpeg bundled.") from exc
        clips=[];audios=[]
        try:
            for i,item in enumerate(media_items):
                audio=None
                if i<len(audio_paths) and audio_paths[i] and os.path.getsize(audio_paths[i])>0:
                    try:audio=AudioFileClip(audio_paths[i]);audios.append(audio)
                    except Exception:audio=None
                duration=(audio.duration if audio else float(script.get("scenes",[{}])[i].get("duration",8)))+.5
                duration=max(2.0,duration)
                if item.get("type")=="video":
                    clip=VideoFileClip(item["path"],audio=False)
                    if clip.duration<duration:
                        from moviepy.editor import concatenate_videoclips as concat
                        loops=max(2,int(duration/clip.duration)+1);clip=concat([clip]*loops).subclip(0,duration)
                    else:
                        start=max(0,(clip.duration-duration)/2);clip=clip.subclip(start,start+duration)
                    clip=clip.resize(height=720)
                    if clip.w<1280:clip=clip.resize(width=1280)
                    clip=clip.crop(x_center=clip.w/2,y_center=clip.h/2,width=1280,height=720)
                else:
                    clip=ImageClip(item["path"]).set_duration(duration).resize(height=720)
                    if clip.w<1280:clip=clip.resize(width=1280)
                    clip=clip.crop(x_center=clip.w/2,y_center=clip.h/2,width=1280,height=720)
                    # Subtle scale-up creates a gentle Ken Burns move without ImageMagick.
                    clip=clip.resize(lambda t:1+0.08*(t/max(duration,.001))).crop(x_center=clip.w/2,y_center=clip.h/2,width=1280,height=720)
                if audio:clip=clip.set_audio(audio.subclip(0,min(duration,audio.duration)))
                clip=clip.fadein(.4).fadeout(.4);clips.append(clip)
            if not clips:raise RuntimeError("No usable scene media was generated")
            final=concatenate_videoclips(clips,method="compose",padding=-.3)
            path=DIRS["videos"]/f"{video_id}.mp4"
            final.write_videofile(str(path),fps=30,codec="libx264",audio_codec="aac",preset="medium",threads=4,logger=None)
            final.close()
            return str(path)
        finally:
            for clip in clips:
                try:clip.close()
                except Exception:pass
            for audio in audios:
                try:audio.close()
                except Exception:pass

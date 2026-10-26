"""Assemble scenes, narration, burned-in subtitles, and title-matched music."""
import os
import re
from pathlib import Path

from common import DIRS


class VideoAssembler:
    TRANSITION_DURATION = 0.5
    TRANSITION_STYLES = ("crossfade", "slide-left", "zoom-push", "slide-up")

    def create(self, script: dict, media_items: list, audio_paths: list, video_id: str,
               progress_callback=None, video_title: str | None = None) -> str:
        self.last_music_style = None
        self.last_music_error = None
        try:
            from PIL import Image
            # MoviePy 1.x still references Image.ANTIALIAS, removed by Pillow 10.
            if not hasattr(Image, "ANTIALIAS"):
                Image.ANTIALIAS = Image.Resampling.LANCZOS
        except Exception as exc:
            raise RuntimeError(f"Pillow could not be loaded ({type(exc).__name__}: {exc}). Reinstall the app's media dependencies.") from exc
        try:
            from moviepy.editor import (AudioFileClip, ImageClip, VideoFileClip,
                                        concatenate_videoclips, CompositeVideoClip)
            from moviepy.audio.AudioClip import CompositeAudioClip
            from moviepy.audio.fx.all import audio_fadein, audio_fadeout, audio_loop
            from moviepy.config import FFMPEG_BINARY
        except Exception as exc:
            details = str(exc)
            if "cannot load module more than once per process" in details.lower():
                hint = "NumPy was initialized more than once. This commonly happens when a CUDA build upgrades NumPy; rebuild with the latest build-desktop-windows-cuda.bat, which pins NumPy 1.26.4."
            else:
                hint = "MoviePy could not be loaded. Reinstall the backend media dependencies."
            raise RuntimeError(f"MoviePy could not be loaded ({type(exc).__name__}: {details}). {hint}") from exc
        try:
            import subprocess
            check = subprocess.run([FFMPEG_BINARY, "-version"], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE, timeout=10, check=False)
            if check.returncode:
                detail = check.stderr.decode(errors="replace")[-400:]
                raise RuntimeError(detail or f"FFmpeg exited with code {check.returncode}")
        except Exception as exc:
            raise RuntimeError(f"FFmpeg could not be started from {FFMPEG_BINARY!r} ({type(exc).__name__}: {exc}). Set a valid FFmpeg path in Settings or ensure imageio-ffmpeg is installed.") from exc

        moviepy_logger = None
        if progress_callback:
            try:
                from proglog import ProgressBarLogger

                class AssemblyProgressLogger(ProgressBarLogger):
                    def bars_callback(self, bar, attr, value, old_value=None):
                        if bar == "t" and attr == "index":
                            total = self.bars.get(bar, {}).get("total", 0)
                            if total:
                                progress_callback(max(0, min(1, float(value) / total)))
                moviepy_logger = AssemblyProgressLogger()
            except Exception:
                moviepy_logger = None

        clips = []
        audios = []
        subtitle_clips = []
        music_clip = music_loop = mixed_audio = voice_track = None
        voice_parts = []
        final = None
        try:
            speech_durations = []
            scene_durations = []
            scene_starts = []
            scene_cursor = 0.0
            scenes = script.get("scenes", [])
            for i, item in enumerate(media_items):
                if i >= len(audio_paths) or not audio_paths[i]:
                    raise RuntimeError(f"Narration audio is missing for scene {i+1}; refusing to create a silent video.")
                if not os.path.isfile(audio_paths[i]) or os.path.getsize(audio_paths[i]) == 0:
                    raise RuntimeError(f"Narration audio for scene {i+1} is missing or empty: {audio_paths[i]}")
                try:
                    audio = AudioFileClip(audio_paths[i])
                    audios.append(audio)
                except Exception as exc:
                    raise RuntimeError(f"Could not decode narration audio for scene {i+1}: {exc}") from exc

                speech_duration = max(0.1, float(audio.duration or 0.1))
                duration = max(2.0, speech_duration + 0.5)
                speech_durations.append(speech_duration)
                scene_durations.append(duration)
                scene_starts.append(scene_cursor)
                voice_parts.append(audio.subclip(0, min(duration, speech_duration)).set_start(scene_cursor))
                scene_cursor += duration - self.TRANSITION_DURATION
                if item.get("type") == "video":
                    clip = VideoFileClip(item["path"], audio=False)
                    if clip.duration < duration:
                        from moviepy.editor import concatenate_videoclips as concat
                        loops = max(2, int(duration / max(clip.duration, 0.1)) + 1)
                        clip = concat([clip] * loops).subclip(0, duration)
                    else:
                        start = max(0, (clip.duration - duration) / 2)
                        clip = clip.subclip(start, start + duration)
                    clip = clip.resize(height=720)
                    if clip.w < 1280:
                        clip = clip.resize(width=1280)
                    clip = clip.crop(x_center=clip.w/2, y_center=clip.h/2, width=1280, height=720)
                else:
                    clip = ImageClip(item["path"]).set_duration(duration).resize(height=720)
                    if clip.w < 1280:
                        clip = clip.resize(width=1280)
                    clip = clip.crop(x_center=clip.w/2, y_center=clip.h/2, width=1280, height=720)
                    # Subtle scale-up creates a gentle Ken Burns move without ImageMagick.
                    clip = clip.resize(lambda t: 1 + 0.08 * (t / max(duration, 0.001))).crop(
                        x_center=clip.w/2, y_center=clip.h/2, width=1280, height=720)
                clips.append(clip)

            if not clips:
                raise RuntimeError("No usable scene media was generated")
            for index in range(1, len(clips)):
                style = self.TRANSITION_STYLES[(index - 1) % len(self.TRANSITION_STYLES)]
                clips[index] = self._apply_scene_transition(
                    clips[index], style, self.TRANSITION_DURATION, (1280, 720)
                )
            final = concatenate_videoclips(
                clips, method="compose", padding=-self.TRANSITION_DURATION
            )
            voice_track = CompositeAudioClip(voice_parts).set_duration(final.duration)
            title = video_title or script.get("title") or "Video"

            # Captions use the exact revised narration and follow each scene's transition-aware start time.
            subtitle_clips, subtitle_entries = self._make_subtitles(
                scenes, speech_durations, scene_starts, final.duration, final.size
            )
            subtitle_path = DIRS["videos"] / f"{video_id}.srt"
            subtitle_path.write_text(self._format_srt(subtitle_entries), encoding="utf-8")
            if subtitle_clips:
                captioned = CompositeVideoClip([final] + subtitle_clips, size=final.size)
                final = captioned.set_duration(final.duration)

            # A deterministic, original instrumental loop is selected by title keywords.
            try:
                from background_music import BackgroundMusicGenerator
                music_path, music_style = BackgroundMusicGenerator().generate(title)
                self.last_music_style = music_style
                music_clip = AudioFileClip(music_path)
                music_loop = audio_loop(music_clip, duration=final.duration).volumex(0.10)
                music_loop = music_loop.set_duration(final.duration)
                mixed_audio = CompositeAudioClip([voice_track, music_loop]).set_duration(final.duration)
                final = final.set_audio(mixed_audio)
                print(f"[music] Selected {music_style} instrumental bed for: {title}")
            except Exception as exc:
                # Keep the narration/video usable if optional instrumental synthesis fails.
                self.last_music_error = str(exc)
                final = final.set_audio(voice_track)
                print(f"[music] Could not add background music; continuing with narration only: {exc}")

            # Finish with a polished fade from black and fade to black, plus matching audio fades.
            fade_in = min(1.1, final.duration / 3.0)
            fade_out = min(1.6, final.duration / 3.0)
            final = final.fadein(fade_in).fadeout(fade_out)
            if final.audio is not None:
                faded_audio = audio_fadein(final.audio, min(0.8, final.duration / 3.0))
                faded_audio = audio_fadeout(faded_audio, fade_out)
                final = final.set_audio(faded_audio)

            path = DIRS["videos"] / f"{video_id}.mp4"
            final.write_videofile(str(path), fps=30, codec="libx264", audio_codec="aac",
                                  preset="medium", threads=4, logger=moviepy_logger)
            if progress_callback:
                progress_callback(1.0)
            return str(path)
        finally:
            for clip in (final, music_loop, mixed_audio, music_clip, voice_track):
                try:
                    if clip is not None:
                        clip.close()
                except Exception:
                    pass
            for clip in subtitle_clips + clips:
                try:
                    clip.close()
                except Exception:
                    pass
            for audio in voice_parts + audios:
                try:
                    audio.close()
                except Exception:
                    pass

    @staticmethod
    def _apply_scene_transition(clip, style, duration, frame_size):
        width, height = frame_size
        if style == "slide-left":
            return clip.crossfadein(min(0.14, duration)).set_position(
                lambda t: (int(width * (1 - min(1.0, max(0.0, t) / duration))), 0)
            )
        if style == "slide-up":
            return clip.crossfadein(min(0.14, duration)).set_position(
                lambda t: (0, int(height * (1 - min(1.0, max(0.0, t) / duration))))
            )
        if style == "zoom-push":
            zoomed = clip.resize(lambda t: 1.10 - 0.10 * min(1.0, max(0.0, t) / duration))
            zoomed = zoomed.crop(x_center=zoomed.w / 2, y_center=zoomed.h / 2,
                                 width=width, height=height)
            return zoomed.crossfadein(min(0.18, duration))
        return clip.crossfadein(duration)

    @classmethod
    def _make_subtitles(cls, scenes, speech_durations, scene_starts, video_duration, frame_size):
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont
        from moviepy.editor import ImageClip

        frame_w, frame_h = int(frame_size[0]), int(frame_size[1])
        font = cls._subtitle_font(ImageFont, max(32, int(frame_h * 0.058)))
        cue_clips = []
        entries = []
        for i, scene in enumerate(scenes):
            if i >= len(speech_durations) or i >= len(scene_starts):
                break
            narration = str(scene.get("narration") or "").strip()
            chunks = cls._caption_chunks(narration)
            total_words = sum(len(chunk.split()) for chunk in chunks)
            speech_duration = speech_durations[i]
            if chunks and total_words:
                cue_cursor = 0.0
                for chunk in chunks:
                    cue_duration = speech_duration * len(chunk.split()) / total_words
                    start = min(video_duration, scene_starts[i] + cue_cursor)
                    end = min(video_duration, start + cue_duration)
                    if end - start > 0.08:
                        image = cls._caption_image(chunk, Image, ImageDraw, ImageFont, font, frame_w)
                        x = max(0, (frame_w - image.width) // 2)
                        y = max(0, frame_h - image.height - int(frame_h * 0.055))
                        overlay = ImageClip(np.asarray(image), transparent=True)
                        cue_clips.append(overlay.set_start(start).set_duration(end - start).set_position((x, y)))
                        entries.append((start, end, chunk))
                    cue_cursor += cue_duration
        return cue_clips, entries

    @staticmethod
    def _caption_chunks(text, max_words=8, max_chars=60):
        words = str(text or "").split()
        chunks = []
        current = []
        for word in words:
            candidate = " ".join(current + [word])
            if current and (len(current) >= max_words or len(candidate) > max_chars):
                chunks.append(" ".join(current))
                current = []
            current.append(word)
            if len(current) >= 4 and re.search(r"[.!?][\"')\]]?$", word):
                chunks.append(" ".join(current))
                current = []
        if current:
            chunks.append(" ".join(current))
        if len(chunks) > 1 and len(chunks[-1].split()) < 3:
            combined = chunks[-2] + " " + chunks[-1]
            if len(combined) <= max_chars + 18:
                chunks[-2:] = [combined]
        return chunks

    @staticmethod
    def _subtitle_font(ImageFont, size):
        for path in (
            r"C:\Windows\Fonts\arialbd.ttf",
            r"C:\Windows\Fonts\segoeuib.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
            r"C:\Windows\Fonts\arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ):
            if Path(path).is_file():
                try:
                    return ImageFont.truetype(path, size=size)
                except OSError:
                    pass
        return ImageFont.load_default()

    @staticmethod
    def _caption_image(text, Image, ImageDraw, ImageFont, font, frame_w):
        max_line_width = int(frame_w * 0.90)
        helper = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        measure = ImageDraw.Draw(helper)
        lines = []
        line = ""
        for word in str(text).split():
            candidate = f"{line} {word}".strip()
            if line and measure.textbbox((0, 0), candidate, font=font, stroke_width=3)[2] > max_line_width:
                lines.append(line)
                line = word
            else:
                line = candidate
        if line:
            lines.append(line)
        lines = lines[:2]
        padding_x, padding_y, gap = 18, 16, 8
        line_boxes = [measure.textbbox((0, 0), line, font=font, stroke_width=3) for line in lines]
        text_w = max((box[2] - box[0] for box in line_boxes), default=1)
        text_h = sum(box[3] - box[1] for box in line_boxes) + gap * max(0, len(lines) - 1)
        width = min(frame_w, text_w + padding_x * 2 + 8)
        height = text_h + padding_y * 2 + 8
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        y = padding_y
        positions = []
        for line, box in zip(lines, line_boxes):
            line_h = box[3] - box[1]
            line_w = box[2] - box[0]
            position = ((width - line_w) / 2, y - box[1])
            positions.append((line, position))
            shadow_draw.text((position[0] + 2, position[1] + 3), line, font=font,
                             fill=(0, 0, 0, 225), stroke_width=5, stroke_fill=(0, 0, 0, 235))
            y += line_h + gap
        from PIL import ImageFilter
        image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=2.4)))
        draw = ImageDraw.Draw(image)
        for line, position in positions:
            draw.text(position, line, font=font, fill=(255, 255, 255, 255),
                      stroke_width=3, stroke_fill=(5, 7, 12, 255))
        return image

    @staticmethod
    def _format_srt(entries):
        def timestamp(seconds):
            millis = max(0, int(round(seconds * 1000)))
            hours, millis = divmod(millis, 3_600_000)
            minutes, millis = divmod(millis, 60_000)
            secs, millis = divmod(millis, 1000)
            return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"
        blocks = []
        for index, (start, end, text) in enumerate(entries, 1):
            blocks.append(f"{index}\n{timestamp(start)} --> {timestamp(end)}\n{text}")
        return "\n\n".join(blocks) + ("\n" if blocks else "")

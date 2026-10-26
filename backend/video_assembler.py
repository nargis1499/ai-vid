"""Assemble scenes, narration, burned-in subtitles, and title-matched music."""
import os
import re
from pathlib import Path

from common import DIRS, ROOT_DIR


class VideoAssembler:
    TRANSITION_DURATION = 0.5
    TRANSITION_STYLES = ("crossfade", "slide-left", "zoom-push", "slide-up")

    def __init__(self):
        self.last_video_encoder = None
        self.encoding_active = False
        self._nvenc_unavailable_binary = None

    def _encode_video(self, final, path, ffmpeg_binary, allow_nvenc, logger, stage_callback=None):
        path = Path(path)
        ffmpeg_key = str(ffmpeg_binary)
        use_nvenc = allow_nvenc and self._nvenc_unavailable_binary != ffmpeg_key
        attempts = []
        if use_nvenc:
            attempts.append(("NVIDIA NVENC", "h264_nvenc", "p5",
                             ["-rc", "vbr", "-cq", "19", "-b:v", "0"],
                             path.with_name(f"{path.stem}.nvenc-temp.mp4"),
                             path.with_name(f"{path.stem}.nvenc-audio.m4a")))
        attempts.append(("CPU libx264", "libx264", "medium", [],
                         path.with_name(f"{path.stem}.x264-temp.mp4"),
                         path.with_name(f"{path.stem}.x264-audio.m4a")))

        nvenc_error = None
        for label, codec, preset, ffmpeg_params, temp_path, temp_audio in attempts:
            self.last_video_encoder = label
            self.encoding_active = True
            if stage_callback:stage_callback("encoding",f"Encoding MP4 with {label} at 30 fps…")
            try:
                for stale in (temp_path, temp_audio):
                    try:
                        stale.unlink()
                    except OSError:
                        pass
                final.write_videofile(
                    str(temp_path), fps=30, codec=codec, audio_codec="aac",
                    preset=preset, threads=4, logger=logger,
                    temp_audiofile=str(temp_audio), ffmpeg_params=ffmpeg_params)
                if not temp_path.is_file() or temp_path.stat().st_size == 0:
                    raise RuntimeError(f"{label} did not produce a video file")
                os.replace(temp_path, path)
                self.last_video_encoder = label
                print(f"[video] Encoded {path.name} using {label}")
                if stage_callback:stage_callback("encoding",f"MP4 encoding complete using {label}.")
                return
            except Exception as exc:
                if codec == "h264_nvenc":
                    nvenc_error = exc
                    self._nvenc_unavailable_binary = ffmpeg_key
                    print(f"[video] NVENC unavailable; falling back to CPU libx264: {exc}")
                    if stage_callback:stage_callback("encoding","NVENC unavailable; retrying the MP4 encode on the CPU…")
                else:
                    if nvenc_error:
                        raise RuntimeError(
                            f"NVENC encoding failed ({nvenc_error}); CPU libx264 fallback failed ({exc})."
                        ) from exc
                    raise
            finally:
                self.encoding_active = False
                for stale in (temp_path, temp_audio):
                    try:
                        stale.unlink()
                    except OSError:
                        pass

    def create(self, script: dict, media_items: list, audio_paths: list, video_id: str,
               progress_callback=None, video_title: str | None = None,
               resolution: str = "720p", use_nvenc: bool = True,
               stage_callback=None) -> str:
        output_size = {"720p": (1280, 720), "1080p": (1920, 1080)}.get(str(resolution).lower())
        if output_size is None:
            raise ValueError("resolution must be 720p or 1080p")
        target_width, target_height = output_size
        self.last_music_style = None
        self.last_music_error = None
        self.last_video_encoder = None
        self.encoding_active = False
        try:
            from PIL import Image
            # MoviePy 1.x still references Image.ANTIALIAS, removed by Pillow 10.
            if not hasattr(Image, "ANTIALIAS"):
                Image.ANTIALIAS = Image.Resampling.LANCZOS
        except Exception as exc:
            raise RuntimeError(f"Pillow could not be loaded ({type(exc).__name__}: {exc}). Reinstall the app's media dependencies.") from exc
        try:
            from moviepy.editor import (AudioFileClip, ImageClip, VideoClip, VideoFileClip,
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
                    clip = clip.resize(height=target_height)
                    if clip.w < target_width:
                        clip = clip.resize(width=target_width)
                    clip = clip.crop(x_center=clip.w/2, y_center=clip.h/2,
                                     width=target_width, height=target_height)
                else:
                    clip = ImageClip(item["path"]).set_duration(duration).resize(height=target_height)
                    if clip.w < target_width:
                        clip = clip.resize(width=target_width)
                    clip = clip.crop(x_center=clip.w/2, y_center=clip.h/2,
                                     width=target_width, height=target_height)
                    # Subtle scale-up creates a gentle Ken Burns move without ImageMagick.
                    clip = clip.resize(lambda t: 1 + 0.08 * (t / max(duration, 0.001))).crop(
                        x_center=clip.w/2, y_center=clip.h/2,
                        width=target_width, height=target_height)
                clips.append(clip)

            if not clips:
                raise RuntimeError("No usable scene media was generated")
            for index in range(1, len(clips)):
                style = self.TRANSITION_STYLES[(index - 1) % len(self.TRANSITION_STYLES)]
                clips[index] = self._apply_scene_transition(
                    clips[index], style, self.TRANSITION_DURATION, output_size
                )
            final = concatenate_videoclips(
                clips, method="compose", padding=-self.TRANSITION_DURATION
            )
            voice_track = CompositeAudioClip(voice_parts).set_duration(final.duration)
            title = video_title or script.get("title") or "Video"

            # Captions use the exact revised narration and follow each scene's transition-aware start time.
            if stage_callback:stage_callback("subtitles","Building word-by-word voice-synced captions and the downloadable SRT file…")
            subtitle_clips, subtitle_entries = self._make_subtitles(
                scenes, speech_durations, scene_starts, final.duration, final.size
            )
            subtitle_path = DIRS["videos"] / f"{video_id}.srt"
            subtitle_path.write_text(self._format_srt(subtitle_entries), encoding="utf-8")
            if subtitle_clips:
                captioned = CompositeVideoClip([final] + subtitle_clips, size=final.size)
                final = captioned.set_duration(final.duration)
            if stage_callback:stage_callback("subtitles",f"Word-highlight captions ready ({len(subtitle_entries)} phrase cue(s)); SRT saved.")

            # A deterministic, original instrumental loop is selected by title keywords.
            if stage_callback:stage_callback("music","Choosing and mixing a title-matched instrumental soundtrack…")
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
            if stage_callback:
                music_message=f"Mixed {self.last_music_style} soundtrack; finalizing audio and visual fades…" if self.last_music_style else "Continuing with narration only; finalizing video and audio fades…"
                stage_callback("music",music_message)

            # Finish with a polished fade from black and fade to black, plus matching audio fades.
            fade_in = min(1.1, final.duration / 3.0)
            fade_out = min(1.6, final.duration / 3.0)
            final = final.fadein(fade_in).fadeout(fade_out)
            if final.audio is not None:
                faded_audio = audio_fadein(final.audio, min(0.8, final.duration / 3.0))
                faded_audio = audio_fadeout(faded_audio, fade_out)
                final = final.set_audio(faded_audio)

            path = DIRS["videos"] / f"{video_id}.mp4"
            self._encode_video(final, path, FFMPEG_BINARY, use_nvenc, moviepy_logger, stage_callback)
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
        import bisect
        import numpy as np
        from functools import lru_cache
        from PIL import Image, ImageDraw, ImageFont
        from moviepy.editor import VideoClip

        frame_w, frame_h = int(frame_size[0]), int(frame_size[1])
        font = cls._subtitle_font(ImageFont, max(32, int(frame_h * 0.058)))
        entries = []
        chunks_by_key = {}
        word_states = []
        max_overlay_w = 1
        max_overlay_h = 1

        for scene_index, scene in enumerate(scenes):
            if scene_index >= len(speech_durations) or scene_index >= len(scene_starts):
                break
            narration = str(scene.get("narration") or "").strip()
            words = narration.split()
            if not words:
                continue
            word_durations = cls._word_durations(words, max(0.01, float(speech_durations[scene_index])))
            chunks = cls._caption_chunks(narration)
            word_index = 0
            word_cursor = 0.0
            speech_start = float(scene_starts[scene_index])

            for chunk_index, chunk in enumerate(chunks):
                chunk_words = chunk.split()
                if not chunk_words:
                    continue
                chunk_start = min(float(video_duration), speech_start + word_cursor)
                chunks_by_key[(scene_index, chunk_index)] = chunk
                for local_word_index in range(len(chunk_words)):
                    word_duration = word_durations[word_index + local_word_index]
                    word_start = min(float(video_duration), speech_start + word_cursor)
                    word_cursor += word_duration
                    word_end = min(float(video_duration), speech_start + word_cursor)
                    if word_end > word_start:
                        word_states.append((word_start, word_end, scene_index, chunk_index, local_word_index))
                chunk_end = min(float(video_duration), speech_start + word_cursor)
                if chunk_end - chunk_start > 0.04:
                    entries.append((chunk_start, chunk_end, chunk))
                layout = cls._caption_image(chunk, Image, ImageDraw, ImageFont, font, frame_w, highlight_index=0)
                max_overlay_w = max(max_overlay_w, layout.width)
                max_overlay_h = max(max_overlay_h, layout.height)
                layout.close()
                word_index += len(chunk_words)

        if not word_states:
            return [], entries

        overlay_size = (max_overlay_w, max_overlay_h)
        overlay_x = max(0, (frame_w - max_overlay_w) // 2)
        overlay_y = max(0, frame_h - max_overlay_h - int(frame_h * 0.055))
        blank_rgb = np.zeros((max_overlay_h, max_overlay_w, 3), dtype=np.uint8)
        blank_mask = np.zeros((max_overlay_h, max_overlay_w), dtype=np.float32)
        word_starts = [state[0] for state in word_states]
        overlay_start = max(0.0, word_states[0][0])
        overlay_end = min(float(video_duration), word_states[-1][1])
        overlay_duration = max(0.01, overlay_end - overlay_start)

        @lru_cache(maxsize=8)
        def rendered_word_state(scene_index, chunk_index, active_word_index):
            text = chunks_by_key[(scene_index, chunk_index)]
            image = cls._caption_image(text, Image, ImageDraw, ImageFont, font,
                                       frame_w, highlight_index=active_word_index)
            canvas = Image.new("RGBA", overlay_size, (0, 0, 0, 0))
            canvas.alpha_composite(image, ((max_overlay_w - image.width) // 2,
                                           max_overlay_h - image.height))
            rgb = np.asarray(canvas.convert("RGB"), dtype=np.uint8).copy()
            alpha = np.asarray(canvas.getchannel("A"), dtype=np.float32).copy() / 255.0
            return rgb, alpha

        def state_at(local_time):
            absolute_time = overlay_start + float(local_time)
            state_index = bisect.bisect_right(word_starts, absolute_time) - 1
            if state_index < 0:
                return blank_rgb, blank_mask
            start, end, scene_index, chunk_index, active_word_index = word_states[state_index]
            if absolute_time >= end:
                return blank_rgb, blank_mask
            return rendered_word_state(scene_index, chunk_index, active_word_index)

        rgb_clip = VideoClip(lambda t: state_at(t)[0], duration=overlay_duration)
        alpha_clip = VideoClip(lambda t: state_at(t)[1], ismask=True, duration=overlay_duration)
        subtitle = (rgb_clip.set_mask(alpha_clip).set_start(overlay_start)
                    .set_duration(overlay_duration).set_position((overlay_x, overlay_y)))
        return [subtitle], entries

    @staticmethod
    def _word_durations(words, speech_duration):
        """Estimate word boundaries from the actual voice clip duration and spoken-word shape."""
        if not words:
            return []
        weights = []
        for word in words:
            letters = re.sub(r"[^\w]", "", word, flags=re.UNICODE)
            syllables = max(1, len(re.findall(r"[aeiouy]+", letters.lower())))
            weight = 0.6 + len(letters) * 0.11 + max(0, syllables - 1) * 0.16
            if re.search(r"[,;:]$", word):
                weight += 0.22
            elif re.search(r"[.!?][\"')\]]?$", word):
                weight += 0.42
            elif re.search(r"[-—–]$", word):
                weight += 0.14
            weights.append(weight)
        total = sum(weights) or len(words)
        return [float(speech_duration) * weight / total for weight in weights]

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
        """Load bundled Montserrat SemiBold for burned-in captions, with safe system fallbacks."""
        montserrat_path = ROOT_DIR / "frontend" / "assets" / "fonts" / "Montserrat-Variable.ttf"
        if montserrat_path.is_file():
            try:
                font = ImageFont.truetype(str(montserrat_path), size=size)
                axes = font.get_variation_axes()
                if axes:
                    values = []
                    for axis in axes:
                        name = str(axis.get("name", "")).lower()
                        if "weight" in name or axis.get("tag") == "wght":
                            values.append(min(max(600, axis.get("minimum", 600)), axis.get("maximum", 600)))
                        else:
                            values.append(axis.get("default", 0))
                    font.set_variation_by_axes(values)
                    return font
                font.set_variation_by_name("SemiBold")
                return font
            except (AttributeError, OSError, ValueError):
                try:
                    font = ImageFont.truetype(str(montserrat_path), size=size)
                    font.set_variation_by_name("SemiBold")
                    return font
                except (AttributeError, OSError, ValueError):
                    pass
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
    def _caption_image(text, Image, ImageDraw, ImageFont, font, frame_w, highlight_index=None):
        """Render clean word-highlight captions with text shadow and no backing box."""
        max_line_width = int(frame_w * 0.90)
        helper = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        measure = ImageDraw.Draw(helper)
        lines = []
        line_words = []
        for word in str(text).split():
            candidate = " ".join(line_words + [word])
            if line_words and measure.textbbox((0, 0), candidate, font=font, stroke_width=3)[2] > max_line_width:
                lines.append(line_words)
                line_words = []
            line_words.append(word)
        if line_words:
            lines.append(line_words)
        lines = lines[:2]
        line_texts = [" ".join(words) for words in lines]
        padding_y, gap = 16, 8
        line_boxes = [measure.textbbox((0, 0), line, font=font, stroke_width=3) for line in line_texts]
        text_w = max((box[2] - box[0] for box in line_boxes), default=1)
        text_h = sum(box[3] - box[1] for box in line_boxes) + gap * max(0, len(lines) - 1)
        width = min(frame_w, text_w + 16)
        height = text_h + padding_y * 2 + 8
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        y = padding_y
        positions = []
        for line, box in zip(line_texts, line_boxes):
            line_h = box[3] - box[1]
            line_w = box[2] - box[0]
            position = ((width - line_w) / 2, y - box[1])
            positions.append((position, y))
            shadow_draw.text((position[0] + 2, position[1] + 3), line, font=font,
                             fill=(0, 0, 0, 225), stroke_width=5, stroke_fill=(0, 0, 0, 235))
            y += line_h + gap
        from PIL import ImageFilter
        image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=2.4)))
        draw = ImageDraw.Draw(image)
        active_index = 0
        space_width = measure.textlength(" ", font=font)
        for words, (position, line_y), box in zip(lines, positions, line_boxes):
            x = position[0] - box[0]
            for word in words:
                color = (255, 220, 92, 255) if active_index == highlight_index else (255, 255, 255, 255)
                draw.text((x, line_y - box[1]), word, font=font, fill=color,
                          stroke_width=3, stroke_fill=(5, 7, 12, 255))
                x += measure.textlength(word, font=font) + space_width
                active_index += 1
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

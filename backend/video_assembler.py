"""Assemble scenes, narration, burned-in subtitles, and title-matched music."""
import os
import random
import re
from pathlib import Path

from common import DIRS, ROOT_DIR


class VideoAssembler:
    TRANSITION_DURATION = 0.5
    # Keep speech crossfades short so they smooth joins without layering full words.
    AUDIO_CROSSFADE_DURATION = 0.05
    # Randomly choose among the retained and newly added transitions at each scene boundary.
    TRANSITION_STYLES = (
        "panel-slice", "shattered-glass", "swish-pan", "light-leak",
        "split-wipe", "luma-dissolve",
    )

    def __init__(self):
        self.last_video_encoder = None

    def _encode_video(self, final, path, logger, stage_callback=None, cancel_check=None):
        import subprocess
        from moviepy.config import FFMPEG_BINARY

        path = Path(path)
        label = "CPU libx264"
        temp_path = path.with_name(f"{path.stem}.x264-temp.mp4")
        temp_audio = path.with_name(f"{path.stem}.x264-audio.wav")
        video_only = None
        self.last_video_encoder = label
        if stage_callback:
            stage_callback("encoding", "Encoding video on the CPU with libx264 and preparing continuous narration audio…")
        for stale in (temp_path, temp_audio):
            try:
                stale.unlink()
            except OSError:
                pass
        try:
            if final.audio is not None:
                final.audio.write_audiofile(
                    str(temp_audio), fps=44100, nbytes=2, codec="pcm_s16le", logger=logger
                )
                if not temp_audio.is_file() or temp_audio.stat().st_size == 0:
                    raise RuntimeError("The continuous narration audio track could not be written.")

            # Encode video and audio separately, then encode audio once while muxing.
            # MoviePy's AAC packet-copy mux can introduce a silent gap at scene boundaries.
            video_only = final.set_audio(None)
            video_only.write_videofile(
                str(temp_path), fps=30, codec="libx264", audio=False,
                preset="medium", threads=4, logger=logger,
            )
            if not temp_path.is_file() or temp_path.stat().st_size == 0:
                raise RuntimeError("CPU libx264 did not produce a video file.")

            if temp_audio.is_file():
                command = [
                    FFMPEG_BINARY, "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(temp_path), "-i", str(temp_audio),
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-t", f"{float(final.duration):.6f}", "-movflags", "+faststart",
                    str(path),
                ]
                completed = subprocess.run(command, capture_output=True, text=True, check=False)
                if completed.returncode:
                    detail = (completed.stderr or f"FFmpeg exited with code {completed.returncode}").strip()[-700:]
                    raise RuntimeError(f"Could not mux the continuous audio track into the CPU-encoded video: {detail}")
            else:
                os.replace(temp_path, path)
            if not path.is_file() or path.stat().st_size == 0:
                raise RuntimeError("FFmpeg did not produce a final MP4 file.")
            print(f"[video] Encoded {path.name} using {label}; audio muxed as a continuous track")
            if stage_callback:
                stage_callback("encoding", "CPU MP4 encoding and continuous audio mux complete.")
        except Exception:
            if cancel_check:
                cancel_check()
            raise
        finally:
            if video_only is not None:
                try:
                    video_only.close()
                except Exception:
                    pass
            for stale in (temp_path, temp_audio):
                try:
                    stale.unlink()
                except OSError:
                    pass

    def create(self, script: dict, media_items: list, audio_paths: list, video_id: str,
               progress_callback=None, video_title: str | None = None,
               resolution: str = "720p",
               stage_callback=None, cancel_check=None) -> str:
        output_size = {"720x480": (720, 480), "720p": (1280, 720), "1080p": (1920, 1080)}.get(str(resolution).lower())
        if output_size is None:
            raise ValueError("resolution must be 720x480, 720p, or 1080p")
        target_width, target_height = output_size
        self.last_music_style = None
        self.last_music_error = None
        self.last_video_encoder = None
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
                hint = "NumPy was initialized more than once. Reinstall the pinned backend media dependencies and restart the app."
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
                        if cancel_check:
                            cancel_check()
                        if bar == "t" and attr == "index":
                            total = self.bars.get(bar, {}).get("total", 0)
                            if total:
                                progress_callback(max(0, min(1, float(value) / total)))
                moviepy_logger = AssemblyProgressLogger()
            except Exception:
                moviepy_logger = None

        clips = []
        audios = []
        voice_parts = []
        positioned_voice_parts = []
        voice_crossfades = []
        subtitle_clips = []
        music_clip = music_loop = mixed_audio = voice_track = None
        final = None
        try:
            speech_durations = []
            scene_durations = []
            scene_starts = []
            scene_cursor = 0.0
            scenes = script.get("scenes", [])
            for i, item in enumerate(media_items):
                if cancel_check:
                    cancel_check()
                if i >= len(audio_paths) or not audio_paths[i]:
                    raise RuntimeError(f"Narration audio is missing for scene {i+1}; refusing to create a silent video.")
                if not os.path.isfile(audio_paths[i]) or os.path.getsize(audio_paths[i]) == 0:
                    raise RuntimeError(f"Narration audio for scene {i+1} is missing or empty: {audio_paths[i]}")
                try:
                    audio = AudioFileClip(audio_paths[i])
                    audios.append(audio)
                except Exception as exc:
                    raise RuntimeError(f"Could not decode narration audio for scene {i+1}: {exc}") from exc

                voice_part = self._trim_scene_edge_silence(
                    audio, trim_end=i < len(media_items) - 1
                )
                voice_parts.append(voice_part)
                speech_duration = max(0.1, float(voice_part.duration or 0.1))
                has_next_scene = i < len(media_items) - 1
                crossfade = min(
                    self.AUDIO_CROSSFADE_DURATION,
                    self.TRANSITION_DURATION,
                    speech_duration * 0.25,
                ) if has_next_scene else 0.0
                voice_crossfades.append(crossfade)
                # Video overlaps the next scene by the visual transition duration; audio
                # overlaps only briefly, keeping words intelligible and starts synchronized.
                duration = speech_duration - crossfade + self.TRANSITION_DURATION if has_next_scene else speech_duration + self.TRANSITION_DURATION
                speech_durations.append(speech_duration)
                scene_durations.append(duration)
                scene_starts.append(scene_cursor)
                scene_cursor += speech_duration - crossfade
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
            previous_style = None
            for index in range(1, len(clips)):
                choices = [style for style in self.TRANSITION_STYLES if style != previous_style]
                style = random.choice(choices or self.TRANSITION_STYLES)
                previous_style = style
                clips[index] = self._apply_scene_transition(
                    clips[index], style, self.TRANSITION_DURATION, output_size
                )
            final = concatenate_videoclips(
                clips, method="compose", padding=-self.TRANSITION_DURATION
            )
            # Apply a short audio crossfade at each narration join. The matching scene starts
            # use the same overlap so video, voiceover, and captions stay on one timeline.
            for index, voice_part in enumerate(voice_parts):
                incoming_fade = voice_crossfades[index - 1] if index > 0 else 0.0
                outgoing_fade = voice_crossfades[index] if index < len(voice_parts) - 1 else 0.0
                if incoming_fade > 0:
                    voice_part = audio_fadein(voice_part, incoming_fade)
                if outgoing_fade > 0:
                    voice_part = audio_fadeout(voice_part, outgoing_fade)
                positioned_voice_parts.append(voice_part.set_start(scene_starts[index]))
            voice_track = CompositeAudioClip(positioned_voice_parts).set_duration(final.duration)
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

            # Fade the picture at the video endpoints. Only fade audio at the file's
            # opening; a composite-track fade-out can attenuate narration at scene joins.
            fade_in = min(1.1, final.duration / 3.0)
            fade_out = min(1.6, final.duration / 3.0)
            final = final.fadein(fade_in).fadeout(fade_out)
            if final.audio is not None:
                faded_audio = audio_fadein(final.audio, min(0.8, final.duration / 3.0))
                final = final.set_audio(faded_audio)

            path = DIRS["videos"] / f"{video_id}.mp4"
            if cancel_check:
                cancel_check()
            self._encode_video(final, path, moviepy_logger, stage_callback, cancel_check)
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
                    source_clip = getattr(clip, "_transition_source", None)
                    if source_clip is not None:
                        source_clip.close()
                    clip.close()
                except Exception:
                    pass
            closed_audio_ids = set()
            for audio in voice_parts + positioned_voice_parts + audios:
                if id(audio) in closed_audio_ids:
                    continue
                closed_audio_ids.add(id(audio))
                try:
                    audio.close()
                except Exception:
                    pass

    @staticmethod
    def _trim_scene_edge_silence(audio, trim_end=True):
        """Compact inter-scene TTS padding while retaining the final clip's natural ending."""
        import numpy as np

        duration = float(audio.duration or 0.0)
        if duration <= 0.08:
            return audio
        sample_rate = 4000
        frame_samples = 40  # 10 ms RMS windows
        chunk_samples = sample_rate  # One-second reads stay within MoviePy's audio-reader buffer.
        sample_count = int(duration * sample_rate)
        rms_chunks = []
        for offset in range(0, sample_count, chunk_samples):
            end_sample = min(sample_count, offset + chunk_samples)
            times = np.arange(offset, end_sample, dtype=np.float64) / sample_rate
            try:
                samples = np.asarray(audio.to_soundarray(tt=times, fps=sample_rate), dtype=np.float32)
            except IndexError as exc:
                # Silence trimming is an optimization; never fail video assembly because a
                # decoder cannot sample a particular audio chunk. Keep the original clip.
                print(f"[audio] Could not inspect a TTS edge chunk; preserving it unchanged: {exc}")
                return audio
            if samples.ndim > 1:
                samples = np.max(np.abs(samples), axis=1)
            else:
                samples = np.abs(samples)
            usable = (len(samples) // frame_samples) * frame_samples
            if usable:
                windows = samples[:usable].reshape(-1, frame_samples)
                rms_chunks.append(np.sqrt(np.mean(windows ** 2, axis=1)))
        if not rms_chunks:
            return audio
        frame_rms = np.concatenate(rms_chunks)
        peak = float(frame_rms.max(initial=0.0))
        if peak <= 1e-5:
            return audio
        active = np.flatnonzero(frame_rms >= max(1e-4, peak * 0.01))
        if not len(active):
            return audio

        # Retain a small handle around active speech so crossfades never shave consonants.
        start = max(0.0, float(active[0]) * 0.01 - 0.02)
        end = min(duration, float(active[-1] + 1) * 0.01 + 0.03) if trim_end else duration
        if end - start < 0.08 or duration - (end - start) < 0.015:
            return audio
        return audio.subclip(start, end)

    @staticmethod
    def _apply_scene_transition(clip, style, duration, frame_size):
        """Animate the incoming scene over the outgoing scene with a per-pixel alpha mask."""
        from functools import lru_cache
        import numpy as np
        from PIL import Image, ImageFilter
        from moviepy.editor import VideoClip

        width, height = (int(frame_size[0]), int(frame_size[1]))
        duration = max(0.001, float(duration))
        def smoothstep(value):
            value = min(1.0, max(0.0, float(value)))
            return value * value * (3.0 - 2.0 * value)

        def progress_at(t):
            return min(1.0, max(0.0, float(t) / duration))

        if style == "panel-slice":
            def render(t):
                progress = progress_at(t)
                frame = Image.fromarray(np.asarray(clip.get_frame(t), dtype=np.uint8))
                canvas = Image.new("RGB", (width, height), (0, 0, 0))
                coverage = Image.new("L", (width, height), 0)
                panel_count = 8
                for panel in range(panel_count):
                    x0 = round(panel * width / panel_count)
                    x1 = round((panel + 1) * width / panel_count)
                    delay = 0.045 * (panel % 3)
                    panel_progress = smoothstep((progress - delay) / max(0.001, 1.0 - delay))
                    direction = 1 if panel % 2 == 0 else -1
                    shift = round(direction * (1.0 - panel_progress) * width)
                    panel_image = frame.crop((x0, 0, x1, height))
                    destination_x = x0 + shift
                    canvas.paste(panel_image, (destination_x, 0))
                    coverage.paste(Image.new("L", (x1 - x0, height), 255), (destination_x, 0))
                # Feather the moving edges to give the opposing strips a short motion-blur trail.
                radius = max(0.7, (1.0 - progress) * 5.0)
                blurred_coverage = coverage.filter(ImageFilter.GaussianBlur(radius=radius))
                blurred_rgb = canvas.filter(ImageFilter.GaussianBlur(radius=radius))
                alpha = np.asarray(blurred_coverage, dtype=np.float32) / 255.0
                premultiplied = np.asarray(blurred_rgb, dtype=np.float32)
                nonzero = alpha > 1e-5
                premultiplied[nonzero] /= alpha[nonzero, None]
                premultiplied[~nonzero] = 0
                return np.clip(premultiplied, 0, 255).astype(np.uint8), alpha

        elif style == "shattered-glass":
            rng = np.random.default_rng()
            rows, columns = 6, 10
            vertices = np.zeros((rows + 1, columns + 1, 2), dtype=np.float32)
            for row in range(rows + 1):
                for column in range(columns + 1):
                    x = column * width / columns
                    y = row * height / rows
                    if 0 < row < rows:
                        x += rng.uniform(-0.17, 0.17) * width / columns
                    if 0 < column < columns:
                        y += rng.uniform(-0.17, 0.17) * height / rows
                    vertices[row, column] = (x, y)
            triangles = []
            for row in range(rows):
                for column in range(columns):
                    a, b = vertices[row, column], vertices[row, column + 1]
                    c, d = vertices[row + 1, column], vertices[row + 1, column + 1]
                    if rng.integers(0, 2):
                        triangles.extend(((a, b, c), (b, d, c)))
                    else:
                        triangles.extend(((a, b, d), (a, d, c)))
            shard_ids = np.full((height, width), 255, dtype=np.uint8)
            centers = np.empty((len(triangles), 2), dtype=np.float32)
            for shard_id, triangle in enumerate(triangles):
                points = np.asarray(triangle, dtype=np.float32)
                centers[shard_id] = points.mean(axis=0)
                x_min = max(0, int(np.floor(points[:, 0].min())))
                x_max = min(width, int(np.ceil(points[:, 0].max())) + 1)
                y_min = max(0, int(np.floor(points[:, 1].min())))
                y_max = min(height, int(np.ceil(points[:, 1].max())) + 1)
                if x_min >= x_max or y_min >= y_max:
                    continue
                px = np.arange(x_min, x_max, dtype=np.float32)[None, :] + 0.5
                py = np.arange(y_min, y_max, dtype=np.float32)[:, None] + 0.5
                ax, ay = points[0]
                bx, by = points[1]
                cx, cy = points[2]
                denominator = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
                if abs(float(denominator)) < 1e-6:
                    continue
                wa = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / denominator
                wb = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / denominator
                inside = (wa >= -1e-4) & (wb >= -1e-4) & ((1.0 - wa - wb) >= -1e-4)
                region = shard_ids[y_min:y_max, x_min:x_max]
                region[inside] = shard_id
            shard_ids[shard_ids == 255] = 0
            shard_count = len(triangles)
            delays = rng.uniform(0.0, 0.30, size=shard_count).astype(np.float32)
            directions = centers - np.array([width / 2.0, height / 2.0], dtype=np.float32)
            lengths = np.linalg.norm(directions, axis=1)
            lengths[lengths < 1e-3] = 1.0
            directions /= lengths[:, None]
            travel = rng.uniform(0.05, 0.13, size=shard_count).astype(np.float32) * max(width, height)
            directions *= travel[:, None]
            x_grid = np.arange(width, dtype=np.float32)[None, :]
            y_grid = np.arange(height, dtype=np.float32)[:, None]

            def render(t):
                progress = progress_at(t)
                shard_progress = np.clip((progress - delays) / (1.0 - delays), 0.0, 1.0)
                shard_progress = shard_progress * shard_progress * (3.0 - 2.0 * shard_progress)
                per_pixel_progress = shard_progress[shard_ids]
                displacement = (1.0 - shard_progress)[:, None] * directions
                source_x = x_grid - displacement[shard_ids, 0]
                source_y = y_grid - displacement[shard_ids, 1]
                frame = np.asarray(clip.get_frame(t), dtype=np.uint8)
                transformed = VideoAssembler._sample_bilinear(frame, source_x, source_y)
                alpha = per_pixel_progress.astype(np.float32)
                return transformed, alpha

        elif style == "swish-pan":
            direction = random.choice(("left", "right", "up", "down"))
            motion_x = 1 if direction == "left" else -1 if direction == "right" else 0
            motion_y = 1 if direction == "up" else -1 if direction == "down" else 0
            x_grid = np.arange(width, dtype=np.float32)[None, :]
            y_grid = np.arange(height, dtype=np.float32)[:, None]

            def render(t):
                progress = progress_at(t)
                source = np.asarray(clip.get_frame(t), dtype=np.uint8)
                accumulated_rgb = np.zeros((height, width, 3), dtype=np.float32)
                accumulated_alpha = np.zeros((height, width), dtype=np.float32)
                sample_count = 5
                for sample_index in range(sample_count):
                    # Neighboring motion samples form a directional blur kernel;
                    # smoothstep accelerates into and decelerates out of the whip.
                    sample_progress = min(1.0, max(0.0, progress + (sample_index - 2) * 0.045))
                    eased = smoothstep(sample_progress)
                    offset_x = motion_x * (1.0 - eased) * width
                    offset_y = motion_y * (1.0 - eased) * height
                    source_x = x_grid - offset_x
                    source_y = y_grid - offset_y
                    valid = ((source_x >= 0) & (source_x <= width - 1) &
                             (source_y >= 0) & (source_y <= height - 1))
                    sample = VideoAssembler._sample_bilinear(source, source_x, source_y)
                    accumulated_rgb += sample.astype(np.float32) * valid[..., None]
                    accumulated_alpha += valid.astype(np.float32)
                valid_pixels = accumulated_alpha > 1e-5
                accumulated_rgb[valid_pixels] /= accumulated_alpha[valid_pixels, None]
                accumulated_rgb[~valid_pixels] = 0
                alpha = accumulated_alpha / sample_count
                alpha *= smoothstep(progress)
                return np.clip(accumulated_rgb, 0, 255).astype(np.uint8), alpha

        elif style == "light-leak":
            flare_direction = random.choice(("left-to-right", "right-to-left"))
            flare_y = random.uniform(0.22, 0.78) * height
            x_grid = np.arange(width, dtype=np.float32)[None, :]
            y_grid = np.arange(height, dtype=np.float32)[:, None]
            warm_tint = np.array([255.0, 142.0, 54.0], dtype=np.float32)

            def render(t):
                progress = progress_at(t)
                source = np.asarray(clip.get_frame(t), dtype=np.uint8).astype(np.float32)
                peak = float(np.sin(np.pi * progress) ** 1.4)
                travel = progress if flare_direction == "left-to-right" else 1.0 - progress
                center_x = (-0.08 + 1.16 * travel) * width
                radial = np.exp(-(((x_grid - center_x) / max(1.0, width * 0.24)) ** 2 +
                                  ((y_grid - flare_y) / max(1.0, height * 0.40)) ** 2) * 2.2)
                streak = np.exp(-((y_grid - flare_y) / max(1.0, height * 0.018)) ** 2)
                strength = np.clip(peak * (0.46 + 0.54 * radial + 0.15 * streak), 0.0, 0.98)
                tint = warm_tint[None, None, :] + (255.0 - warm_tint[None, None, :]) * radial[..., None]
                output = source * (1.0 - strength[..., None]) + tint * strength[..., None]
                opacity = max(smoothstep(progress), peak * 0.96)
                alpha = np.full((height, width), opacity, dtype=np.float32)
                return np.clip(output, 0, 255).astype(np.uint8), alpha

        elif style == "split-wipe":
            orientation = random.choice(("vertical", "horizontal", "diagonal"))
            reverse = random.choice((False, True))
            x_grid = np.arange(width, dtype=np.float32)[None, :] / max(1, width)
            y_grid = np.arange(height, dtype=np.float32)[:, None] / max(1, height)
            if orientation == "vertical":
                softness = max(0.006, 3.0 / width)
            elif orientation == "horizontal":
                softness = max(0.006, 3.0 / height)
            else:
                softness = max(0.006, 4.0 / max(width, height))

            def render(t):
                progress = progress_at(t)
                if orientation == "vertical":
                    coordinate = np.broadcast_to(x_grid, (height, width))
                elif orientation == "horizontal":
                    coordinate = np.broadcast_to(y_grid, (height, width))
                else:
                    coordinate = np.broadcast_to((x_grid + y_grid) * 0.5, (height, width))
                if reverse:
                    coordinate = 1.0 - coordinate
                edge_progress = np.clip((progress - coordinate) / softness + 0.5, 0.0, 1.0)
                alpha = edge_progress * edge_progress * (3.0 - 2.0 * edge_progress)
                frame = np.asarray(clip.get_frame(t), dtype=np.uint8)
                return frame, alpha.astype(np.float32)

        elif style == "luma-dissolve":
            bright_first = random.choice((True, False))
            gradient_axis = random.choice(("horizontal", "vertical"))
            reverse_gradient = random.choice((True, False))
            x_grid = np.arange(width, dtype=np.float32)[None, :] / max(1, width - 1)
            y_grid = np.arange(height, dtype=np.float32)[:, None] / max(1, height - 1)
            def render(t):
                progress = progress_at(t)
                frame = np.asarray(clip.get_frame(t), dtype=np.uint8)
                luma = (0.2126 * frame[..., 0] + 0.7152 * frame[..., 1] +
                        0.0722 * frame[..., 2]) / 255.0
                brightness_delay = (1.0 - luma) if bright_first else luma
                gradient = x_grid if gradient_axis == "horizontal" else y_grid
                if reverse_gradient:
                    gradient = 1.0 - gradient
                delay = 0.78 * brightness_delay + 0.18 * gradient
                threshold = np.clip((progress - delay) / 0.16, 0.0, 1.0)
                alpha = threshold * threshold * (3.0 - 2.0 * threshold)
                return frame, alpha.astype(np.float32)
        else:
            raise ValueError(f"Unknown scene transition: {style}")

        @lru_cache(maxsize=1)
        def cached_render(time_value):
            return render(time_value)

        def frame_at(t):
            return cached_render(round(float(t), 5))[0]

        def mask_at(t):
            return cached_render(round(float(t), 5))[1]

        transitioned = VideoClip(make_frame=frame_at, duration=clip.duration)
        mask_clip = VideoClip(make_frame=mask_at, ismask=True, duration=clip.duration)
        transitioned = transitioned.set_mask(mask_clip)
        transitioned._transition_source = clip
        if getattr(clip, "fps", None):
            transitioned = transitioned.set_fps(clip.fps)
        return transitioned

    @staticmethod
    def _sample_bilinear(frame, source_x, source_y):
        """Bilinearly sample RGB pixels, reflecting out-of-range coordinates at frame edges."""
        import numpy as np

        height, width = frame.shape[:2]
        if width > 1:
            period_x = 2.0 * (width - 1)
            source_x = np.mod(source_x, period_x)
            source_x = np.where(source_x > width - 1, period_x - source_x, source_x)
        else:
            source_x = np.zeros_like(source_x)
        if height > 1:
            period_y = 2.0 * (height - 1)
            source_y = np.mod(source_y, period_y)
            source_y = np.where(source_y > height - 1, period_y - source_y, source_y)
        else:
            source_y = np.zeros_like(source_y)
        x0 = np.floor(source_x).astype(np.int32)
        y0 = np.floor(source_y).astype(np.int32)
        x1 = np.minimum(x0 + 1, width - 1)
        y1 = np.minimum(y0 + 1, height - 1)
        wx = (source_x - x0)[..., None].astype(np.float32)
        wy = (source_y - y0)[..., None].astype(np.float32)
        top = frame[y0, x0].astype(np.float32) * (1.0 - wx) + frame[y0, x1].astype(np.float32) * wx
        bottom = frame[y1, x0].astype(np.float32) * (1.0 - wx) + frame[y1, x1].astype(np.float32) * wx
        return np.clip(top * (1.0 - wy) + bottom * wy, 0, 255).astype(np.uint8)

    @classmethod
    def _make_subtitles(cls, scenes, speech_durations, scene_starts, video_duration, frame_size):
        import bisect
        import numpy as np
        from functools import lru_cache
        from PIL import Image, ImageDraw, ImageFont
        from moviepy.editor import VideoClip

        frame_w, frame_h = int(frame_size[0]), int(frame_size[1])
        font = cls._subtitle_font(ImageFont, max(28, int(frame_h * 0.048)))
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
        """Render smaller, word-highlight captions with lighter shadow and no backing box."""
        max_line_width = int(frame_w * 0.90)
        helper = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        measure = ImageDraw.Draw(helper)
        lines = []
        line_words = []
        for word in str(text).split():
            candidate = " ".join(line_words + [word])
            if line_words and measure.textbbox((0, 0), candidate, font=font, stroke_width=2)[2] > max_line_width:
                lines.append(line_words)
                line_words = []
            line_words.append(word)
        if line_words:
            lines.append(line_words)
        lines = lines[:2]
        line_texts = [" ".join(words) for words in lines]
        padding_y = 14
        line_boxes = [measure.textbbox((0, 0), line, font=font, stroke_width=2) for line in line_texts]
        line_heights = [box[3] - box[1] for box in line_boxes]
        font_size = getattr(font, "size", max(line_heights, default=1))
        line_advance = max(max(line_heights, default=1), int(round(font_size * 1.5)))
        text_w = max((box[2] - box[0] for box in line_boxes), default=1)
        text_h = line_advance * len(lines)
        width = min(frame_w, text_w + 16)
        height = text_h + padding_y * 2 + 8
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        positions = []
        for index, (line, box, line_h) in enumerate(zip(line_texts, line_boxes, line_heights)):
            line_w = box[2] - box[0]
            row_y = padding_y + index * line_advance
            glyph_y = row_y + (line_advance - line_h) // 2
            position = ((width - line_w) / 2, glyph_y - box[1])
            positions.append((position, glyph_y))
            shadow_draw.text((position[0] + 1, position[1] + 2), line, font=font,
                             fill=(0, 0, 0, 145), stroke_width=3, stroke_fill=(0, 0, 0, 165))
        from PIL import ImageFilter
        image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=1.5)))
        draw = ImageDraw.Draw(image)
        active_index = 0
        space_width = measure.textlength(" ", font=font)
        for words, (position, line_y), box in zip(lines, positions, line_boxes):
            x = position[0] - box[0]
            for word in words:
                color = (255, 220, 92, 255) if active_index == highlight_index else (255, 255, 255, 255)
                draw.text((x, line_y - box[1]), word, font=font, fill=color,
                          stroke_width=2, stroke_fill=(5, 7, 12, 220))
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

"""Create small, original instrumental beds matched to a video's title."""
import hashlib
import math
import re
import wave

import numpy as np
from common import DIRS


class BackgroundMusicGenerator:
    """A lightweight deterministic synth; no external tracks or service required."""

    SAMPLE_RATE = 22050
    BARS = 16
    STYLES = {
        "calm": {"bpm": 72, "mode": "major", "progression": (0, 5, 3, 4), "pad": 0.82, "pluck": 0.45},
        "cinematic": {"bpm": 78, "mode": "minor", "progression": (0, 5, 2, 6), "pad": 0.76, "pluck": 0.35},
        "upbeat": {"bpm": 106, "mode": "major", "progression": (0, 3, 5, 4), "pad": 0.62, "pluck": 0.78},
        "inspiring": {"bpm": 90, "mode": "major", "progression": (0, 4, 5, 3), "pad": 0.78, "pluck": 0.60},
    }
    KEYWORDS = {
        "cinematic": {"mystery", "mysterious", "horror", "crime", "dark", "ghost", "thriller", "suspense", "haunted", "detective", "war", "space", "alien", "secret"},
        "calm": {"calm", "relax", "relaxing", "meditation", "sleep", "mindful", "peaceful", "nature", "ocean", "forest", "healing", "yoga", "travel", "journey"},
        "upbeat": {"fitness", "workout", "sports", "sport", "race", "racing", "action", "adventure", "challenge", "gaming", "game", "comedy", "funny", "dance"},
        "inspiring": {"success", "business", "motivation", "motivational", "inspiration", "science", "technology", "education", "history", "discovery", "future", "documentary", "ai", "robot", "psychology", "health", "cooking", "guide", "facts"},
    }
    SCALES = {
        "major": (0, 2, 4, 5, 7, 9, 11),
        "minor": (0, 2, 3, 5, 7, 8, 10),
    }

    @classmethod
    def choose_style(cls, title):
        words = set(re.findall(r"[a-z0-9]+", str(title or "").lower()))
        for style in ("cinematic", "calm", "upbeat", "inspiring"):
            if words & cls.KEYWORDS[style]:
                return style
        return "inspiring"

    def generate(self, title):
        title = str(title or "Video")
        style = self.choose_style(title)
        digest = hashlib.sha256(("creatorflow-bgm-v1:" + style + ":" + title.casefold()).encode("utf-8")).digest()
        path = DIRS["audio"] / f"background_{digest.hex()[:16]}.wav"
        if path.is_file() and path.stat().st_size > 2048:
            return str(path), style

        settings = self.STYLES[style]
        bpm = settings["bpm"] + (digest[1] % 5 - 2)
        beat = 60.0 / bpm
        bar = 4.0 * beat
        duration = self.BARS * bar
        sample_count = int(math.ceil(duration * self.SAMPLE_RATE))
        mix = np.zeros(sample_count, dtype=np.float32)
        major = settings["mode"] == "major"
        scale = self.SCALES[settings["mode"]]
        key = digest[2] % 12
        progression = settings["progression"]
        melody_choices = (0, 2, 4, 2, 5, 4, 1, 2, 4, 5, 2, 0, 1, 4, 2, 5)

        def frequency(midi_note):
            return 440.0 * (2.0 ** ((midi_note - 69) / 12.0))

        def add_note(start, length, midi_note, amplitude, kind="pad"):
            first = max(0, int(start * self.SAMPLE_RATE))
            last = min(sample_count, int((start + length) * self.SAMPLE_RATE))
            if last <= first:
                return
            relative = np.arange(last - first, dtype=np.float32) / self.SAMPLE_RATE
            f = frequency(midi_note)
            if kind == "pad":
                attack = np.minimum(1.0, relative / 0.42)
                release = np.minimum(1.0, np.maximum(0.0, (length - relative) / 0.72))
                envelope = attack * release
                tone = (np.sin(2 * np.pi * f * relative)
                        + 0.22 * np.sin(2 * np.pi * f * 2.003 * relative)
                        + 0.08 * np.sin(2 * np.pi * f * 3.01 * relative)) / 1.30
            elif kind == "bass":
                envelope = np.minimum(1.0, relative / 0.04) * np.minimum(1.0, np.maximum(0.0, (length - relative) / 0.24))
                tone = (np.sin(2 * np.pi * f * relative) + 0.13 * np.sin(2 * np.pi * f * 2 * relative)) / 1.13
            else:
                envelope = np.minimum(1.0, relative / 0.018) * np.exp(-relative / max(0.16, length * 0.34))
                tone = (np.sin(2 * np.pi * f * relative)
                        + 0.34 * np.sin(2 * np.pi * f * 2.01 * relative)
                        + 0.12 * np.sin(2 * np.pi * f * 3.02 * relative)) / 1.46
            mix[first:last] += (amplitude * envelope * tone).astype(np.float32)

        for bar_index in range(self.BARS):
            chord_degree = progression[(bar_index + (digest[3] % len(progression))) % len(progression)]
            chord_start = bar_index * bar
            chord_root = key + scale[chord_degree]
            chord_is_major = (chord_degree in (0, 3, 4) if major else chord_degree in (2, 5, 6))
            third = 4 if chord_is_major else 3
            for note in (chord_root + 12, chord_root + third + 12, chord_root + 7 + 12):
                add_note(chord_start, bar * 1.02, 48 + note, 0.045 * settings["pad"], "pad")
            add_note(chord_start, bar * 0.96, 36 + chord_root, 0.055, "bass")

            # A sparse, soft arpeggio gives each style a musical pulse without competing with speech.
            for beat_index in (0, 2):
                scale_degree = melody_choices[(bar_index * 2 + beat_index // 2 + digest[4] % 8) % len(melody_choices)]
                note = 60 + key + scale[scale_degree]
                note_start = chord_start + beat_index * beat
                add_note(note_start, beat * 0.82, note, 0.035 * settings["pluck"], "pluck")

        # Make the phrase loop cleanly and keep its peak comfortably below narration.
        fade_samples = min(int(0.35 * self.SAMPLE_RATE), sample_count // 8)
        if fade_samples:
            mix[:fade_samples] *= np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
            mix[-fade_samples:] *= np.linspace(1.0, 0.0, fade_samples, dtype=np.float32)
        peak = float(np.max(np.abs(mix))) if mix.size else 0.0
        if peak > 0:
            mix *= 0.42 / peak
        pcm = np.clip(mix * 32767.0, -32768, 32767).astype("<i2")
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(self.SAMPLE_RATE)
            output.writeframes(pcm.tobytes())
        return str(path), style

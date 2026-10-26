"""Create script-aware, high-contrast thumbnail collages inspired by the supplied template."""
import re
import subprocess

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

from common import DIRS, ROOT_DIR
from image_generator import ImageGenerator

WIDTH, HEIGHT = 1280, 720
MAX_PANELS = 5
GOLD = (255, 190, 42)


def _font(size: int):
    """Prefer the app's bundled Montserrat variable font at a heavy weight."""
    font_path = ROOT_DIR / "frontend" / "assets" / "fonts" / "Montserrat-Variable.ttf"
    if font_path.is_file():
        try:
            font = ImageFont.truetype(str(font_path), size=size)
            axes = font.get_variation_axes()
            if axes:
                values = []
                for axis in axes:
                    name = str(axis.get("name", "")).lower()
                    if "weight" in name or axis.get("tag") == "wght":
                        values.append(min(max(800, axis.get("minimum", 800)), axis.get("maximum", 800)))
                    else:
                        values.append(axis.get("default", 0))
                font.set_variation_by_axes(values)
                return font
            font.set_variation_by_name("ExtraBold")
            return font
        except (AttributeError, OSError, ValueError):
            pass
    for candidate in (
        r"C:\Windows\Fonts\arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    image = ImageOps.exif_transpose(image).convert("RGB")
    scale = max(size[0] / image.width, size[1] / image.height)
    resized = image.resize((max(size[0], round(image.width * scale)),
                            max(size[1], round(image.height * scale))), Image.Resampling.LANCZOS)
    left = (resized.width - size[0]) // 2
    top = (resized.height - size[1]) // 2
    return resized.crop((left, top, left + size[0], top + size[1]))


def _wrap_to_width(text: str, draw: ImageDraw.ImageDraw, font, max_width: int, max_lines: int):
    words = str(text or "").upper().split()
    if not words:
        return []
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and draw.textbbox((0, 0), candidate, font=font, stroke_width=2)[2] > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,:;-")
        while draw.textbbox((0, 0), lines[-1] + "…", font=font, stroke_width=2)[2] > max_width and len(lines[-1]) > 5:
            lines[-1] = lines[-1][:-1].rstrip()
        lines[-1] += "…"
    return lines


def _fit_lines(text: str, draw: ImageDraw.ImageDraw, max_width: int, max_lines: int,
               initial_size: int, minimum_size: int):
    for size in range(initial_size, minimum_size - 1, -2):
        font = _font(size)
        lines = _wrap_to_width(text, draw, font, max_width, max_lines)
        if lines and all(draw.textbbox((0, 0), line, font=font, stroke_width=2)[2] <= max_width for line in lines):
            return font, lines
    font = _font(minimum_size)
    return font, _wrap_to_width(text, draw, font, max_width, max_lines)


def _clean_label(value: str, max_words: int = 3) -> str:
    value = re.sub(r"[^\w\s&'-]", " ", str(value or ""), flags=re.UNICODE)
    words = value.split()
    return " ".join(words[:max_words]).upper()


class ThumbnailGenerator:
    def generate(self, seo: dict, title: str, index: int,
                 background_path: str | None = None, scenes: list | None = None,
                 media_items: list | None = None) -> str:
        """Build a cinematic multi-panel thumbnail from the selected scene media."""
        path = DIRS["thumbnails"] / f"thumb_{index}.jpg"
        scenes = scenes if isinstance(scenes, list) else []
        media_items = media_items if isinstance(media_items, list) else []
        panels = self._load_panels(seo, title, index, scenes, media_items)
        if not panels and background_path:
            image = self._load_image(background_path)
            if image is not None:
                fallback_labels = seo.get("thumbnail_panels") if isinstance(seo.get("thumbnail_panels"), list) else []
                scene = scenes[0] if scenes and isinstance(scenes[0], dict) else {}
                label = _clean_label(fallback_labels[0]) if fallback_labels else ""
                label = label or (self._scene_label(scene, title) if scene else _clean_label(title))
                panels = [(image, {"label": label}, 0)]
        if not panels:
            # Keep a topic-colored fallback if no stock or generated scene visual is available.
            fallback = DIRS["thumbnails"] / f"_background_{index}.png"
            ImageGenerator._gradient(seo.get("thumbnail_style") or title, fallback)
            image = self._load_image(str(fallback))
            if image is not None:
                panels = [(image, {"label": _clean_label(title)}, 0)]

        if not panels:
            raise RuntimeError("No usable visual could be prepared for the thumbnail.")

        canvas = self._compose_panels(panels)
        canvas = ImageEnhance.Contrast(canvas).enhance(1.12)
        canvas = ImageEnhance.Color(canvas).enhance(1.2)
        canvas = ImageEnhance.Brightness(canvas).enhance(1.02)
        canvas = self._draw_template(canvas, seo, title, panels)
        if canvas.size != (WIDTH, HEIGHT):
            canvas = canvas.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
        canvas.save(path, format="JPEG", quality=94, optimize=True)
        return str(path)

    def _load_panels(self, seo, title, index, scenes, media_items):
        available_count = min(len(media_items), len(scenes)) if scenes else len(media_items)
        count = min(MAX_PANELS, available_count)
        if count <= 0:
            return []
        if count == 1:
            selected = [0]
        else:
            selected = [round(i * (available_count - 1) / (count - 1)) for i in range(count)]
        labels = seo.get("thumbnail_panels") if isinstance(seo.get("thumbnail_panels"), list) else []
        panels = []
        used_labels = set()
        title_words = {word.casefold() for word in re.findall(r"[\w'-]+", title or "")}
        for output_index, scene_index in enumerate(selected):
            item = media_items[scene_index] if scene_index < len(media_items) else None
            scene = scenes[scene_index] if scene_index < len(scenes) and isinstance(scenes[scene_index], dict) else {}
            image = self._media_image(item, index * 10 + output_index)
            if image is None:
                continue
            model_label = labels[scene_index] if scene_index < len(labels) else ""
            label = _clean_label(model_label)
            label_tokens = {word.casefold() for word in re.findall(r"[\w'-]+", label)}
            if not label or label_tokens.issubset(title_words) or label.casefold() in used_labels:
                label = self._scene_label(scene, title)
            if label.casefold() in used_labels:
                label = f"PART {output_index + 1}"
            used_labels.add(label.casefold())
            panels.append((image, {"label": label}, scene_index))
        return panels

    @staticmethod
    def _load_image(path):
        if not path:
            return None
        try:
            with Image.open(path) as source:
                return ImageOps.exif_transpose(source).convert("RGB").copy()
        except Exception as exc:
            print(f"[thumbnail] Could not load visual {path}: {exc}")
            return None

    def _media_image(self, item, temp_index):
        if not isinstance(item, dict) or not item.get("path"):
            return None
        if item.get("type") != "video":
            return self._load_image(item.get("path"))
        video_path = str(item["path"])
        frame_path = DIRS["thumbnails"] / f"_frame_{temp_index}.jpg"
        try:
            from moviepy.config import FFMPEG_BINARY
            ffmpeg = FFMPEG_BINARY
            for timestamp in ("00:00:01.000", "00:00:00.300", "00:00:00.000"):
                completed = subprocess.run(
                    [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", timestamp,
                     "-i", video_path, "-frames:v", "1", "-q:v", "2", str(frame_path)],
                    capture_output=True, text=True, timeout=30, check=False)
                if completed.returncode == 0 and frame_path.is_file() and frame_path.stat().st_size:
                    image = self._load_image(str(frame_path))
                    frame_path.unlink(missing_ok=True)
                    if image is not None:
                        return image
        except Exception as exc:
            print(f"[thumbnail] Could not extract a stock video frame: {exc}")
        try:
            frame_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None

    @staticmethod
    def _scene_label(scene, title):
        """Prefer a concise scene-specific label; avoid repeating the video title."""
        title_words = {word.casefold() for word in re.findall(r"[\w'-]+", title or "")}
        candidates = [scene.get("search_keywords"), scene.get("text_overlay"), scene.get("visual_description")]
        stop = {"about", "with", "from", "this", "that", "scene", "video", "image", "cinematic",
                "illustrative", "visual", "footage", "showing", "shows", "the", "and", "for"}
        for candidate in candidates:
            for phrase in str(candidate or "").replace(";", ",").split(","):
                words = re.findall(r"[\w'-]+", phrase)
                kept = [word for word in words if word.casefold() not in stop and word.casefold() not in title_words]
                if kept:
                    return " ".join(kept[:3]).upper()
        narration = re.findall(r"[\w'-]+", str(scene.get("narration") or ""))
        kept = [word for word in narration if word.casefold() not in stop and word.casefold() not in title_words]
        return " ".join(kept[:3]).upper() or "KEY MOMENT"

    @staticmethod
    def _compose_panels(panels):
        count = len(panels)
        canvas = Image.new("RGB", (WIDTH, HEIGHT), (8, 14, 24))
        for i, (source, _metadata, _scene_index) in enumerate(panels):
            left = round(i * WIDTH / count)
            right = round((i + 1) * WIDTH / count)
            tile = _cover(source, (right - left, HEIGHT))
            tile = ImageEnhance.Contrast(tile).enhance(1.08)
            canvas.paste(tile, (left, 0))
        return canvas

    def _draw_template(self, canvas, seo, title, panels):
        image = canvas.convert("RGBA")
        shade = Image.new("RGBA", image.size, (0, 0, 0, 0))
        shade_pixels = shade.load()
        # A soft, top-centered fade keeps title type legible without a dated text box.
        for y in range(0, 330):
            alpha = int(175 * (1 - y / 330) ** 1.25)
            for x in range(WIDTH):
                shade_pixels[x, y] = (4, 9, 18, alpha)
        image = Image.alpha_composite(image, shade).convert("RGB")
        draw = ImageDraw.Draw(image)

        panel_count = len(panels)
        # Thin warm dividers echo the reference collage without obscuring the photos.
        for i in range(1, panel_count):
            x = round(i * WIDTH / panel_count)
            draw.line((x - 2, 0, x - 2, HEIGHT), fill=(10, 13, 20), width=5)
            draw.line((x, 0, x, HEIGHT), fill=GOLD, width=3)
            draw.line((x + 3, 0, x + 3, HEIGHT), fill=(255, 225, 145), width=1)

        kicker = self._number_kicker(title)
        headline = str(seo.get("thumbnail_text") or "").strip()
        if not headline or headline.casefold() == "the story explained":
            headline = self._fallback_hook(title)
        if kicker:
            number = re.search(r"\d+", kicker)
            if number:
                headline = re.sub(rf"^\s*(?:top\s*)?{number.group(0)}\b[\s:,-]*", "", headline, flags=re.I)
        subtitle = str(seo.get("thumbnail_subtitle") or "").strip()
        if subtitle.casefold() == headline.casefold():
            subtitle = ""
        if not subtitle:
            subtitle = self._subtitle_from_title(title, headline)
        title_bottom = 34
        if kicker:
            kicker_font = _font(42)
            self._draw_centered(draw, kicker.upper(), kicker_font, 18, fill=(255, 255, 248),
                                stroke_width=5, stroke_fill=(7, 9, 14))
            title_bottom = 66

        main_font, lines = _fit_lines(headline, draw, WIDTH - 110, 2, 96 if len(headline) < 25 else 86, 58)
        line_heights = []
        for line in lines:
            box = draw.textbbox((0, 0), line, font=main_font, stroke_width=4)
            line_heights.append(box[3] - box[1])
        y = title_bottom
        for line_number, (line, line_height) in enumerate(zip(lines, line_heights)):
            self._draw_centered(draw, line, main_font, y, fill=GOLD if line_number == 0 else (255, 255, 248),
                                stroke_width=7, stroke_fill=(4, 6, 11))
            y += line_height + 3
        if subtitle:
            subtitle_font, subtitle_lines = _fit_lines(subtitle, draw, WIDTH - 180, 1, 42, 27)
            self._draw_centered(draw, subtitle_lines[0], subtitle_font, y + 1,
                                fill=(255, 255, 248), stroke_width=5, stroke_fill=(4, 6, 11))

        # Dark footer strips make concise script-specific scene labels easy to scan.
        band = Image.new("RGBA", image.size, (0, 0, 0, 0))
        band_draw = ImageDraw.Draw(band)
        band_draw.rectangle((0, HEIGHT - 92, WIDTH, HEIGHT), fill=(3, 7, 13, 145))
        image = Image.alpha_composite(image.convert("RGBA"), band).convert("RGB")
        draw = ImageDraw.Draw(image)
        for i, (_source, metadata, _scene_index) in enumerate(panels):
            left = round(i * WIDTH / panel_count)
            right = round((i + 1) * WIDTH / panel_count)
            label = _clean_label(metadata.get("label") or "KEY MOMENT")
            font, label_lines = _fit_lines(label, draw, right - left - 24, 1, 34, 20)
            box = draw.textbbox((0, 0), label_lines[0], font=font, stroke_width=3)
            x = left + ((right - left) - (box[2] - box[0])) // 2
            draw.text((x + 2, HEIGHT - 78 + 3), label_lines[0], font=font,
                      fill=(0, 0, 0), stroke_width=5, stroke_fill=(0, 0, 0))
            draw.text((x, HEIGHT - 78), label_lines[0], font=font, fill=(255, 255, 248),
                      stroke_width=3, stroke_fill=(5, 8, 14))
        return image

    @staticmethod
    def _draw_centered(draw, text, font, y, fill, stroke_width, stroke_fill):
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
        width = bbox[2] - bbox[0]
        x = (WIDTH - width) // 2 - bbox[0]
        draw.text((x + 3, y + 5), text, font=font, fill=(0, 0, 0),
                  stroke_width=stroke_width + 3, stroke_fill=(0, 0, 0))
        draw.text((x, y), text, font=font, fill=fill,
                  stroke_width=stroke_width, stroke_fill=stroke_fill)

    @staticmethod
    def _number_kicker(title):
        text = str(title or "")
        explicit = re.match(r"\s*top\s*(\d{1,2})\b", text, flags=re.I)
        if explicit:
            return f"TOP {explicit.group(1)}"
        numbered_list = re.match(r"\s*(\d{1,2})\s+(most|best|worst|ways|things|reasons|places|cities|secrets|tips|mistakes|facts)\b", text, flags=re.I)
        return f"{numbered_list.group(1)} {numbered_list.group(2).upper()}" if numbered_list else ""

    @staticmethod
    def _fallback_hook(title):
        stop = {"a", "an", "the", "in", "on", "at", "of", "to", "for", "and", "or", "why", "how", "what", "when", "where", "is", "are", "your", "you"}
        words = re.findall(r"[\w'-]+", str(title or ""))
        chosen = [word for word in words if word.casefold() not in stop][:4]
        return " ".join(chosen).upper() or "THE BIG STORY"

    @staticmethod
    def _subtitle_from_title(title, headline):
        headline_words = {word.casefold() for word in re.findall(r"[\w'-]+", headline or "")}
        stop = {"a", "an", "the", "in", "on", "at", "of", "to", "for", "and", "or", "why", "how", "what", "when", "where", "is", "are", "your", "you"}
        remaining = [word for word in re.findall(r"[\w'-]+", str(title or ""))
                     if word.casefold() not in headline_words and word.casefold() not in stop]
        return " ".join(remaining[:4]).upper()

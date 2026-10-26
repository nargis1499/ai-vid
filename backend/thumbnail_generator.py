"""Readable, high-contrast YouTube thumbnail generation."""
import random, textwrap
from PIL import Image,ImageDraw,ImageEnhance,ImageFont
from common import DIRS
from image_generator import _font, _cover
class ThumbnailGenerator:
    def __init__(self,local_ai=None):self.local_ai=local_ai
    def generate(self,seo:dict,title:str,index:int)->str:
        path=DIRS["thumbnails"]/f"thumb_{index}.jpg"
        background=None
        if self.local_ai:
            local=self.local_ai.generate_image(f"youtube thumbnail background, {seo.get('thumbnail_style',title)}, vibrant, high contrast",index+10000)
            if local:
                try:background=Image.open(local)
                except Exception:pass
        if background is None:
            # Reuse the stable, dependency-light gradient renderer.
            from image_generator import ImageGenerator
            temp=DIRS["thumbnails"]/f"_background_{index}.png";ImageGenerator._gradient(seo.get("thumbnail_style") or title,temp);background=Image.open(temp)
        image=_cover(background);image=ImageEnhance.Contrast(image).enhance(1.3);image=ImageEnhance.Color(image).enhance(1.4);image=ImageEnhance.Brightness(image).enhance(1.1)
        image=image.convert("RGBA")
        # Left-side fade for crisp copy, blended pixel-wise.
        overlay=Image.new("RGBA",image.size,(0,0,0,0));px=overlay.load()
        for x in range(740):
            alpha=int(195*max(0,1-x/740))
            for y in range(720):px[x,y]=(5,8,17,alpha)
        image=Image.alpha_composite(image,overlay).convert("RGB")
        text=str(seo.get("thumbnail_text") or title).upper();words=text.split()[:5];
        if not words:words=["WATCH NOW"]
        lines=[];line=""
        for word in words:
            if len(line)+len(word)>17 and line:lines.append(line);line=word
            else:line=(line+" "+word).strip()
        if line:lines.append(line)
        lines=lines[:3];draw=ImageDraw.Draw(image);font=_font(78);y=(720-len(lines)*94)//2
        for n,word in enumerate(lines):
            fill="#FFD700" if n==0 else "white"
            draw.text((58,y),word,font=font,fill=fill,stroke_width=5,stroke_fill="#080910")
            y+=100
        image.save(path,quality=95,optimize=True);return str(path)

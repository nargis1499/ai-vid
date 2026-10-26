"""Pexels imagery and local PIL-based gradient fallbacks."""
import io, random, textwrap
from pathlib import Path
import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFont
from common import DIRS

W,H=1280,720

def _font(size=42, bold=True):
    candidates=["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"]
    for p in candidates:
        try:return ImageFont.truetype(p,size)
        except Exception:pass
    return ImageFont.load_default()

def _cover(image):
    image=image.convert("RGB"); ratio=max(W/image.width,H/image.height)
    image=image.resize((int(image.width*ratio),int(image.height*ratio)),Image.Resampling.LANCZOS)
    x=(image.width-W)//2;y=(image.height-H)//2
    return image.crop((x,y,x+W,y+H))

class ImageGenerator:
    def __init__(self): self.api_key=__import__('os').getenv("PEXELS_API_KEY","").strip()
    def generate_scene_image(self, scene:dict, index:int)->str:
        path=DIRS["images"]/f"scene_{index}.png"
        try:
            if not self.api_key: raise RuntimeError("PEXELS_API_KEY not configured")
            r=requests.get("https://api.pexels.com/v1/search",headers={"Authorization":self.api_key},params={"query":scene.get("search_keywords",scene.get("visual_description","nature")),"orientation":"landscape","per_page":10},timeout=20);r.raise_for_status()
            photos=r.json().get("photos",[])
            if not photos: raise RuntimeError("No Pexels results")
            photo=random.choice(photos[:min(5,len(photos))]);url=photo["src"].get("large2x") or photo["src"].get("large")
            raw=requests.get(url,timeout=30);raw.raise_for_status()
            image=_cover(Image.open(io.BytesIO(raw.content))); image=ImageEnhance.Contrast(image).enhance(1.05)
            image=self._overlay(image,scene.get("text_overlay","")); image.save(path)
        except Exception as exc:
            print(f"[images] Stock image unavailable; gradient fallback: {exc}")
            self._gradient(scene.get("text_overlay") or scene.get("search_keywords") or "Explore",path)
        return str(path)
    def process_uploaded(self,path:str,scene:dict,index:int)->str:
        out=DIRS["images"]/f"scene_{index}.png";image=_cover(Image.open(path));image=ImageEnhance.Contrast(image).enhance(1.1);image=ImageEnhance.Color(image).enhance(1.15)
        image=self._overlay(image,scene.get("text_overlay",""),dark=True);image.save(out);return str(out)
    @staticmethod
    def _overlay(image,text,dark=False):
        image=image.convert("RGBA");shade=Image.new("RGBA",(W,H),(0,0,0,0));d=ImageDraw.Draw(shade)
        d.rectangle((0,H-150,W,H),fill=(0,0,0,145 if dark else 175));image=Image.alpha_composite(image,shade).convert("RGB")
        if text:
            draw=ImageDraw.Draw(image);font=_font(40);label=textwrap.fill(str(text)[:90],width=42)
            draw.multiline_text((58,H-124),label,font=font,fill="white",stroke_width=2,stroke_fill=(0,0,0),spacing=4)
        return image
    @staticmethod
    def _gradient(text,path):
        a=random.choice([(20,76,120),(85,40,140),(148,44,74),(13,105,95),(40,55,110)]);b=random.choice([(12,17,35),(30,25,60),(8,45,55)])
        im=Image.new("RGB",(W,H));pix=im.load()
        for y in range(H):
            t=y/(H-1);c=tuple(int(a[k]*(1-t)+b[k]*t) for k in range(3))
            for x in range(W):pix[x,y]=c
        dr=ImageDraw.Draw(im);dr.ellipse((850,-220,1450,380),fill=(*a,));dr.ellipse((-250,500,300,1050),fill=(*b,))
        overlay=Image.new("RGBA",(W,H),(0,0,0,0));d=ImageDraw.Draw(overlay);d.rectangle((0,0,W,H),fill=(0,0,0,45));im=Image.alpha_composite(im.convert("RGBA"),overlay).convert("RGB")
        d=ImageDraw.Draw(im);font=_font(54);lines=textwrap.wrap(str(text).upper(),width=30)[:3];block="\n".join(lines);box=d.multiline_textbbox((0,0),block,font=font,spacing=14,align="center",stroke_width=2);d.multiline_text(((W-(box[2]-box[0]))/2,(H-(box[3]-box[1]))/2),block,font=font,fill="white",stroke_width=2,stroke_fill=(10,10,20),spacing=14,align="center");im.save(path)

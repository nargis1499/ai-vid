"""Pexels/Pixabay stock imagery and designed gradient fallbacks."""
import io, random, textwrap
import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFont
from common import DIRS
from pixabay_client import PixabayClient
from stock_search import EARLY_STOP_SEMANTIC_RELEVANCE, rank_stock_candidates, search_queries, technical_suitability_score

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
    def __init__(self):
        self.api_key=__import__('os').getenv("PEXELS_API_KEY","").strip()
        self.pixabay=PixabayClient(__import__('os').getenv("PIXABAY_API_KEY", ""))
        self.last_source=None
        self.last_selection=None
        self._used_assets=set()
    def generate_scene_image(self, scene:dict, index:int)->str:
        path=DIRS["images"]/f"scene_{index}.png"
        self.last_source=None
        self.last_selection=None
        queries=search_queries(scene,max_queries=5)
        candidates=[]
        for query_rank,query in enumerate(queries):
            found=self._search_pexels([query])+self._search_pixabay([query])
            for item in found:
                item["query_rank"]=query_rank
            candidates.extend(found)
            if query_rank>=1 and len(rank_stock_candidates(
                scene,candidates,media_type="image",min_semantic=EARLY_STOP_SEMANTIC_RELEVANCE,
                used_assets=self._used_assets
            ))>=3:
                break
        ranked=rank_stock_candidates(scene,candidates,media_type="image",used_assets=self._used_assets)
        for selected in ranked:
            try:
                response=requests.get(selected["url"],timeout=30);response.raise_for_status()
                image=_cover(Image.open(io.BytesIO(response.content)))
                image=ImageEnhance.Contrast(image).enhance(1.05)
                image=self._overlay(image,scene.get("text_overlay",""));image.save(path)
                selected["accessible"]=True
                selected_technical=technical_suitability_score(
                    width=selected.get("width"),height=selected.get("height"),
                    orientation=(scene.get("visual_plan") or {}).get("required_orientation","landscape"),
                    accessible=True,media_type="image",
                )
                self.last_source=selected["provider"]
                metadata=selected.get("metadata") if isinstance(selected.get("metadata"),dict) else {}
                self.last_selection={
                    "provider":selected["provider"],"identity":selected.get("identity"),
                    "query":selected.get("query"),"title":metadata.get("title", ""),
                    "semantic_relevance":selected["semantic_relevance"],
                    "technical_suitability":selected_technical["score"],"accessible":True,
                    "final_rank":selected["final_rank"],"stable_key":selected["stable_key"],
                }
                self._used_assets.update(selected.get("stable_keys") or ([selected["stable_key"]] if selected.get("stable_key") else []))
                return str(path)
            except Exception as exc:
                selected["accessible"]=False
                print(f"[images] Could not download a ranked {selected['provider']} still: {exc}")
        self.last_source="Gradient"
        self.last_selection={"fallback":True,"reason":"no semantically acceptable accessible stock image"}
        self._gradient(scene.get("text_overlay") or scene.get("search_keywords") or "Explore",path)
        return str(path)

    def _search_pexels(self, queries):
        if not self.api_key:
            return []
        candidates=[];seen=set()
        for query_rank,query in enumerate(queries):
            try:
                response=requests.get(
                    "https://api.pexels.com/v1/search",
                    headers={"Authorization":self.api_key},
                    params={"query":query,"orientation":"landscape","per_page":20},timeout=20,
                );response.raise_for_status()
                payload=response.json();photos=payload.get("photos",[]) if isinstance(payload,dict) else []
            except Exception as exc:
                print(f"[images] Pexels search failed for a scene query: {exc}")
                continue
            for rank,photo in enumerate(photos):
                if not isinstance(photo,dict):
                    continue
                width,height=photo.get("width",0),photo.get("height",0)
                try:width,height=int(width or 0),int(height or 0)
                except (TypeError,ValueError):width,height=0,0
                if width and height and width/height<1.15:
                    continue
                source=photo.get("src") if isinstance(photo.get("src"),dict) else {}
                url=source.get("large2x") or source.get("large") or source.get("medium")
                if not url or url in seen:
                    continue
                seen.add(url)
                photo_url=str(photo.get("url") or "")
                slug=photo_url.rstrip("/").rsplit("/",1)[-1].replace("-"," ")
                metadata={"title":slug,"description":photo.get("alt") or ""}
                candidates.append({
                    "provider":"Pexels","identity":photo.get("id"),"url":url,"metadata":metadata,
                    "width":width,"height":height,"query":query,"query_rank":query_rank,"rank":rank,
                })
        return candidates

    def _search_pixabay(self, queries):
        if not self.pixabay.api_key:
            return []
        candidates=[];seen=set()
        for query_rank,query in enumerate(queries):
            try:
                hits=self.pixabay.search_images(query,per_page=25)
            except Exception as exc:
                print(f"[images] Pixabay search failed for a scene query: {exc}")
                continue
            for rank,photo in enumerate(hits):
                if not isinstance(photo,dict):
                    continue
                width,height=photo.get("imageWidth",0),photo.get("imageHeight",0)
                try:width,height=int(width or 0),int(height or 0)
                except (TypeError,ValueError):width,height=0,0
                if width and height and width/height<1.15:
                    continue
                url=photo.get("largeImageURL") or photo.get("webformatURL")
                if not url or url in seen:
                    continue
                seen.add(url)
                metadata={"title":photo.get("title") or "","tags":photo.get("tags") or "","type":photo.get("type") or ""}
                candidates.append({
                    "provider":"Pixabay","identity":photo.get("id"),"url":url,"metadata":metadata,
                    "width":width,"height":height,"query":query,"query_rank":query_rank,"rank":rank,
                })
        return candidates

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

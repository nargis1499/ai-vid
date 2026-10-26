"""SEO package generation through Ollama, with sensible offline metadata."""
import json,re,os,requests
from common import env_bool
class YouTubeSEO:
    def __init__(self):self.enabled=env_bool("USE_OLLAMA",True);self.url=os.getenv("OLLAMA_URL","http://localhost:11434").rstrip("/");self.model=os.getenv("OLLAMA_MODEL","qwen2.5:7b")
    def generate_seo_package(self,title:str,script:dict,niche:str)->dict:
        if self.enabled:
            scene_brief=[{"visual":str(scene.get("visual_description", ""))[:180],"keywords":str(scene.get("search_keywords", ""))[:120],"narration":str(scene.get("narration", ""))[:180]} for scene in script.get("scenes", [])[:10] if isinstance(scene, dict)]
            prompt=f'''Create YouTube SEO metadata for title {title!r}, niche {niche!r}, script summary {script.get('description','')!r}, and scene plan {scene_brief!r}. Return only JSON: {{"seo_title":"max 60 characters","seo_description":"500-1000 characters, keywords in first 150 chars, CTA, hashtags at end","tags":["15 relevant tags"],"category_id":"22","hashtag_line":"#tag1 #tag2 #tag3","thumbnail_text":"3-5 punchy words for a bold thumbnail headline, grounded in the script","thumbnail_subtitle":"short supporting phrase of 2-5 words, not a repetition of the headline","thumbnail_panels":["one distinct 1-3 word label per supplied scene, in scene-plan order, up to 10 labels"],"thumbnail_style":"visual description","pinned_comment":"engaging comment"}}. Make thumbnail wording specific to the video's actual subject and scene plan; preserve both named subjects in numbered lists/comparisons, separated with an ampersand; never invent places, people, or claims.'''
            try:
                r=requests.post(self.url+"/api/generate",json={"model":self.model,"prompt":prompt,"stream":False,"keep_alive":-1 if env_bool("OLLAMA_KEEP_ALIVE",True) else 0,"options":{"temperature":.6}},timeout=120);r.raise_for_status();raw=r.json().get("response","");raw=re.sub(r"^\s*```(?:json)?|```\s*$","",raw.strip(),flags=re.I);data=json.loads(raw[raw.find("{"):raw.rfind("}")+1])
                if isinstance(data,dict):return self._normalize(data,title,script,niche)
            except Exception as exc:print(f"[seo] Ollama unavailable; using fallback: {exc}")
        words=[]
        for s in script.get("scenes",[]):words.extend(str(s.get("search_keywords","")).split(","))
        panel_labels=[str(scene.get("search_keywords") or scene.get("visual_description") or "") for scene in script.get("scenes", [])[:10] if isinstance(scene, dict)]
        return self._normalize({"seo_title":title,"seo_description":f"Explore {title} in this {niche.lower()} video. Discover key ideas, practical context, and surprising details. Subscribe for more videos and share your thoughts in the comments!\n\n{title}","tags":list(dict.fromkeys([title,niche]+[w.strip() for w in words if w.strip()]))[:15],"category_id":"22","hashtag_line":"#YouTube #Education #"+re.sub(r"\W","",niche),"thumbnail_text":self._fallback_hook(title),"thumbnail_subtitle":"","thumbnail_panels":panel_labels,"thumbnail_style":f"Bold cinematic imagery related to {title}","pinned_comment":f"What surprised you most about {title}? Let us know below!"},title,script,niche)
    @staticmethod
    def _normalize(data,title,script,niche):
        result={"seo_title":str(data.get("seo_title") or title)[:100],"seo_description":str(data.get("seo_description") or f"Discover more about {title}. Subscribe for more.")[:5000],"tags":data.get("tags") if isinstance(data.get("tags"),list) else [title,niche],"category_id":str(os.getenv("YOUTUBE_CATEGORY_ID") or data.get("category_id") or "22"),"hashtag_line":str(data.get("hashtag_line") or "#YouTube"),"thumbnail_text":str(data.get("thumbnail_text") or YouTubeSEO._fallback_hook(title))[:80],"thumbnail_subtitle":str(data.get("thumbnail_subtitle") or "")[:80],"thumbnail_panels":data.get("thumbnail_panels") if isinstance(data.get("thumbnail_panels"),list) else [],"thumbnail_style":str(data.get("thumbnail_style") or title),"pinned_comment":str(data.get("pinned_comment") or f"What do you think about {title}?")}
        result["tags"]=[str(t)[:100] for t in result["tags"][:30]]
        result["thumbnail_panels"]=[str(label)[:80] for label in result["thumbnail_panels"][:10]]
        return result

    @staticmethod
    def _fallback_hook(title):
        stop={"a","an","the","in","on","at","of","to","for","and","or","why","how","what","when","where","is","are","your","you"}
        words=re.findall(r"[\w'-]+",str(title or ""))
        return " ".join(word for word in words if word.casefold() not in stop)[:80]

"""SEO package generation through Ollama, with sensible offline metadata."""
import json,re,requests
from common import env_bool
class YouTubeSEO:
    def __init__(self):self.enabled=env_bool("USE_OLLAMA",True);self.url=__import__('os').getenv("OLLAMA_URL","http://localhost:11434").rstrip("/");self.model=__import__('os').getenv("OLLAMA_MODEL","qwen2.5:7b")
    def generate_seo_package(self,title:str,script:dict,niche:str)->dict:
        if self.enabled:
            prompt=f'''Create YouTube SEO metadata for title {title!r}, niche {niche!r}, script summary {script.get('description','')!r}. Return only JSON: {{"seo_title":"max 60 characters","seo_description":"500-1000 characters, keywords in first 150 chars, CTA, hashtags at end","tags":["15 relevant tags"],"category_id":"22","hashtag_line":"#tag1 #tag2 #tag3","thumbnail_text":"3-5 punchy words","thumbnail_style":"visual description","pinned_comment":"engaging comment"}}'''
            try:
                r=requests.post(self.url+"/api/generate",json={"model":self.model,"prompt":prompt,"stream":False,"options":{"temperature":.6}},timeout=120);r.raise_for_status();raw=r.json().get("response","");raw=re.sub(r"^\s*```(?:json)?|```\s*$","",raw.strip(),flags=re.I);data=json.loads(raw[raw.find("{"):raw.rfind("}")+1])
                if isinstance(data,dict):return self._normalize(data,title,script,niche)
            except Exception as exc:print(f"[seo] Ollama unavailable; using fallback: {exc}")
        words=[]
        for s in script.get("scenes",[]):words.extend(str(s.get("search_keywords","")).split(","))
        return self._normalize({"seo_title":title,"seo_description":f"Explore {title} in this {niche.lower()} video. Discover key ideas, practical context, and surprising details. Subscribe for more videos and share your thoughts in the comments!\n\n{title}","tags":list(dict.fromkeys([title,niche]+[w.strip() for w in words if w.strip()]))[:15],"category_id":"22","hashtag_line":"#YouTube #Education #"+re.sub(r"\W","",niche),"thumbnail_text":"The Story Explained","thumbnail_style":f"Bold cinematic imagery related to {title}","pinned_comment":f"What surprised you most about {title}? Let us know below!"},title,script,niche)
    @staticmethod
    def _normalize(data,title,script,niche):
        result={"seo_title":str(data.get("seo_title") or title)[:100],"seo_description":str(data.get("seo_description") or f"Discover more about {title}. Subscribe for more.")[:5000],"tags":data.get("tags") if isinstance(data.get("tags"),list) else [title,niche],"category_id":str(data.get("category_id") or "22"),"hashtag_line":str(data.get("hashtag_line") or "#YouTube"),"thumbnail_text":str(data.get("thumbnail_text") or title)[:80],"thumbnail_style":str(data.get("thumbnail_style") or title),"pinned_comment":str(data.get("pinned_comment") or f"What do you think about {title}?")}
        result["tags"]=[str(t)[:100] for t in result["tags"][:30]]
        return result

"""Pexels stock video fetcher."""
import os, random, requests
from common import DIRS
class VideoFetcher:
    def __init__(self): self.api_key=os.getenv("PEXELS_API_KEY","").strip()
    def fetch_clip(self,query:str,index:int,min_dur:float=5)->str|None:
        if not self.api_key:return None
        try:
            r=requests.get("https://api.pexels.com/videos/search",headers={"Authorization":self.api_key},params={"query":query,"orientation":"landscape","per_page":10},timeout=20);r.raise_for_status()
            videos=[v for v in r.json().get("videos",[]) if v.get("duration",0)>=min_dur]
            if not videos:return None
            video=random.choice(videos[:5]);files=[f for f in video.get("video_files",[]) if f.get("file_type")=="video/mp4"]
            if not files:return None
            file=sorted(files,key=lambda f:(0 if 1280<=f.get("width",0)<=1920 else 1, -f.get("width",0)))[0]
            path=DIRS["clips"]/f"clip_{index}.mp4"
            with requests.get(file["link"],stream=True,timeout=60) as response:
                response.raise_for_status()
                with open(path,"wb") as out:
                    for chunk in response.iter_content(8192):
                        if chunk:out.write(chunk)
            return str(path)
        except Exception as exc:print(f"[video] Pexels fetch failed: {exc}");return None

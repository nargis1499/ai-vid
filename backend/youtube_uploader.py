"""YouTube OAuth and resumable video upload support."""
import json,os,time
from pathlib import Path
from common import BASE_DIR
from settings_store import APP_DATA_DIR
class YouTubeUploader:
    SCOPES=["https://www.googleapis.com/auth/youtube.upload","https://www.googleapis.com/auth/youtube"]
    def __init__(self):
        self.client_file=APP_DATA_DIR/"client_secret.json"
        legacy_client=BASE_DIR/"client_secret.json"
        if not self.client_file.exists() and legacy_client.exists(): self.client_file=legacy_client
        self.token_file=APP_DATA_DIR/"token.json";self.service=None;self.last_error=None
        self.counter_file=APP_DATA_DIR/"upload_counter.json";self.max_uploads=int(os.getenv("MAX_UPLOADS_PER_DAY","5"))
    def authenticate(self,interactive=False):
        if not self.client_file.exists():self.last_error="Add backend/client_secret.json from Google Cloud to enable YouTube.";return False
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from google_auth_oauthlib.flow import InstalledAppFlow
            creds=None
            if self.token_file.exists():creds=Credentials.from_authorized_user_file(str(self.token_file),self.SCOPES)
            if creds and creds.expired and creds.refresh_token:creds.refresh(Request())
            if not creds or not creds.valid:
                if not interactive:self.last_error="YouTube authorization required.";return False
                flow=InstalledAppFlow.from_client_secrets_file(str(self.client_file),self.SCOPES);creds=flow.run_local_server(port=0)
            self.token_file.write_text(creds.to_json(),encoding="utf-8")
            from googleapiclient.discovery import build
            self.service=build("youtube","v3",credentials=creds,cache_discovery=False);self.last_error=None;return True
        except Exception as exc:self.last_error=str(exc);return False
    def status(self):
        self.authenticate(False) if self.service is None else None
        count=self._uploads_today()
        return {"connected":self.service is not None,"uploads_today":count,"max_uploads_per_day":self.max_uploads,"error":self.last_error}
    def upload_video(self,video_path:str,seo:dict,thumbnail_path:str,privacy=None,progress_callback=None)->dict:
        if self.service is None and not self.authenticate(True):return {"success":False,"error":self.last_error or "YouTube authentication failed"}
        if self._uploads_today()>=self.max_uploads:return {"success":False,"error":"Daily upload limit reached."}
        try:
            from googleapiclient.http import MediaFileUpload
            from googleapiclient.errors import HttpError
            body={"snippet":{"title":seo.get("seo_title","Untitled")[:100],"description":seo.get("seo_description",""),"tags":seo.get("tags",[])[:30],"categoryId":seo.get("category_id","22")},"status":{"privacyStatus":privacy or os.getenv("YOUTUBE_PRIVACY_STATUS","private"),"selfDeclaredMadeForKids":False}}
            media=MediaFileUpload(video_path,chunksize=10*1024*1024,resumable=True)
            request=self.service.videos().insert(part="snippet,status",body=body,media_body=media);response=None
            while response is None:
                status,response=request.next_chunk()
                if progress_callback and status is not None:
                    progress_callback(max(0.0,min(1.0,float(status.progress()))))
            if progress_callback:progress_callback(1.0)
            video_id=response["id"]
            try:self.service.thumbnails().set(videoId=video_id,media_body=MediaFileUpload(thumbnail_path)).execute()
            except Exception as exc:print(f"[youtube] Thumbnail upload failed: {exc}")
            try:self.service.commentThreads().insert(part="snippet",body={"snippet":{"videoId":video_id,"topLevelComment":{"snippet":{"textOriginal":seo.get("pinned_comment","")}}}}).execute()
            except Exception as exc:print(f"[youtube] Comment post failed: {exc}")
            self._record_upload();return {"success":True,"video_id":video_id,"url":f"https://youtube.com/watch?v={video_id}"}
        except Exception as exc:
            if "quotaExceeded" in str(exc):return {"success":False,"error":"YouTube API quota exceeded. Uploading stopped for today."}
            return {"success":False,"error":str(exc)}
    def _uploads_today(self):
        try:
            data=json.loads(self.counter_file.read_text());today=time.strftime("%Y-%m-%d")
            return data.get("count",0) if data.get("date")==today else 0
        except Exception:return 0
    def _record_upload(self):
        today=time.strftime("%Y-%m-%d");self.counter_file.write_text(json.dumps({"date":today,"count":self._uploads_today()+1}))

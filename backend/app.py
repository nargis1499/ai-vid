"""Flask API and static frontend for YouTube Auto Creator."""
import os
import time
import requests
from pathlib import Path
from flask import Flask, jsonify, request, send_file, send_from_directory
from flask_cors import CORS
import common
from common import ROOT_DIR
from settings_store import APP_DATA_DIR, apply_environment, load_settings, public_settings, save_settings

_boot_settings = load_settings()
apply_environment(_boot_settings)
common.OUTPUT_DIR = Path(_boot_settings["OUTPUT_DIR"]).expanduser().resolve()
common.DIRS.clear()
common.DIRS.update({name: common.OUTPUT_DIR / name for name in ("images", "audio", "videos", "clips", "thumbnails", "uploads", "batch")})
for _directory in common.DIRS.values():
    _directory.mkdir(parents=True, exist_ok=True)
from batch_orchestrator import BatchOrchestrator

app=Flask(__name__,static_folder=None)
CORS(app)
app.config["MAX_CONTENT_LENGTH"]=100*1024*1024
orchestrator=BatchOrchestrator()
FRONTEND=ROOT_DIR/"frontend"
_OPENROUTER_MODELS_CACHE={"loaded_at":0.0,"models":[]}
_GROQ_MODELS_CACHE={"loaded_at":0.0,"key_digest":"","models":[]}
_GROQ_FALLBACK_MODELS=("openai/gpt-oss-120b","openai/gpt-oss-20b","llama-3.3-70b-versatile")

def _openrouter_models(refresh=False):
    """Return only free, text-output models available through OpenRouter."""
    now=time.time()
    cached=_OPENROUTER_MODELS_CACHE
    if not refresh and cached["models"] and now-cached["loaded_at"]<600:
        return cached["models"]
    response=requests.get(
        "https://openrouter.ai/api/v1/models",
        timeout=20,
        headers={"Accept":"application/json","X-Title":"CreatorFlow"},
    )
    response.raise_for_status()
    payload=response.json()
    if not isinstance(payload,dict) or not isinstance(payload.get("data"),list):
        raise ValueError("OpenRouter returned an invalid model catalog response.")
    models=[]
    for item in payload["data"]:
        if not isinstance(item,dict):
            continue
        model_id=str(item.get("id") or "").strip()
        if not model_id:
            continue
        architecture=item.get("architecture") or {}
        modalities=architecture.get("output_modalities") if isinstance(architecture,dict) else None
        if isinstance(modalities,list) and "text" not in {str(value).lower() for value in modalities}:
            continue
        pricing=item.get("pricing") if isinstance(item.get("pricing"),dict) else {}
        free_id=model_id.endswith(":free") or model_id=="openrouter/free"
        try:
            free_pricing=float(pricing.get("prompt"))==0 and float(pricing.get("completion"))==0
        except (TypeError,ValueError):
            free_pricing=False
        if not (free_id or free_pricing):
            continue
        models.append({
            "id":model_id,
            "name":str(item.get("name") or model_id),
            "context_length":item.get("context_length"),
            "input_price":pricing.get("prompt"),
            "output_price":pricing.get("completion"),
            "free":True,
        })
    models.sort(key=lambda item:(item["name"].lower(),item["id"].lower()))
    if not models:
        raise ValueError("OpenRouter currently returned no free text-generation models.")
    cached.update(loaded_at=now,models=models)
    return models

def _groq_models(refresh=False):
    """Load text models from GroqCloud when a Groq key is saved; offer safe defaults before that."""
    api_key=load_settings().get("GROQ_API_KEY","").strip()
    fallback=[{"id":model,"name":model,"free":False} for model in _GROQ_FALLBACK_MODELS]
    if not api_key:
        return fallback,"Save your GroqCloud API key to load the current model catalog. Available models and rate limits depend on your GroqCloud account."
    import hashlib
    key_digest=hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    cached=_GROQ_MODELS_CACHE
    now=time.time()
    if not refresh and cached["models"] and cached["key_digest"]==key_digest and now-cached["loaded_at"]<600:
        return cached["models"],None
    response=requests.get(
        "https://api.groq.com/openai/v1/models",
        headers={"Authorization":f"Bearer {api_key}","Accept":"application/json"},
        timeout=20,
    )
    response.raise_for_status()
    payload=response.json()
    if not isinstance(payload,dict) or not isinstance(payload.get("data"),list):
        raise ValueError("GroqCloud returned an invalid model catalog response.")
    models=[]
    for item in payload["data"]:
        if not isinstance(item,dict):
            continue
        model_id=str(item.get("id") or "").strip()
        excluded_terms=("embedding","image","vision","whisper","orpheus","tts","speech","audio","guard","moderation")
        if not model_id or any(term in model_id.lower() for term in excluded_terms):
            continue
        models.append({"id":model_id,"name":str(item.get("name") or model_id),"context_length":item.get("context_window") or item.get("context_length"),"free":False})
    if not models:
        raise ValueError("GroqCloud returned no Groq text-generation models.")
    models.sort(key=lambda item:(item["name"].lower(),item["id"].lower()))
    cached.update(loaded_at=now,key_digest=key_digest,models=models)
    return models,None

def _apply_runtime_settings(settings):
    apply_environment(settings)
    output_dir=Path(settings["OUTPUT_DIR"]).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    common.OUTPUT_DIR=output_dir
    common.DIRS.clear()
    common.DIRS.update({name: output_dir/name for name in ("images", "audio", "videos", "clips", "thumbnails", "uploads", "batch")})
    for directory in common.DIRS.values(): directory.mkdir(parents=True, exist_ok=True)
    provider=str(settings.get("TEXT_PROVIDER") or "openrouter").lower()
    model=str(settings.get("TEXT_MODEL") or settings.get("OPENROUTER_MODEL") or "").strip()
    openrouter_key=os.getenv("OPENROUTER_API_KEY", "").strip()
    groq_key=os.getenv("GROQ_API_KEY", "").strip()
    orchestrator.scripts.configure(provider,model,openrouter_key,groq_key)
    orchestrator.seo.configure(provider,model,openrouter_key,groq_key)
    orchestrator.images.api_key=os.getenv("PEXELS_API_KEY", "").strip()
    orchestrator.images.pixabay.set_api_key(os.getenv("PIXABAY_API_KEY", "").strip())
    orchestrator.fetcher.api_key=orchestrator.images.api_key
    orchestrator.fetcher.pixabay.set_api_key(os.getenv("PIXABAY_API_KEY", "").strip())
    orchestrator.youtube.max_uploads=int(os.getenv("MAX_UPLOADS_PER_DAY", "5"))
    orchestrator.audio.voice=os.getenv("EDGE_TTS_VOICE", "en-US-AriaNeural")

@app.get("/")
def index():return send_from_directory(FRONTEND,"index.html")
@app.get("/<path:filename>")
def frontend_file(filename):
    if filename.startswith("api/"):return jsonify({"error":"Not found"}),404
    return send_from_directory(FRONTEND,filename)
@app.post("/api/batch/create")
def create_batch():
    body=request.get_json(silent=True) or {};titles=body.get("titles")
    if isinstance(titles,str):titles=titles.splitlines()
    if not isinstance(titles,list):return jsonify({"error":"titles must be a list of titles"}),400
    titles=[str(t).strip()[:200] for t in titles if str(t).strip()]
    if not titles:return jsonify({"error":"Enter at least one video title."}),400
    provider=str(os.getenv("TEXT_PROVIDER","openrouter")).strip().lower()
    provider_name="Groq (GroqCloud)" if provider=="groq" else "OpenRouter"
    api_key=os.getenv("GROQ_API_KEY","").strip() if provider=="groq" else os.getenv("OPENROUTER_API_KEY","").strip()
    if not api_key:return jsonify({"error":f"Add your {provider_name} API key in Settings before generating videos."}),400
    if not os.getenv("TEXT_MODEL","").strip():return jsonify({"error":f"Choose a {provider_name} text model in Settings before generating videos."}),400
    if len(titles)>30:return jsonify({"error":"A batch can contain at most 30 titles."}),400
    custom_scripts=body.get("custom_scripts")
    if custom_scripts is not None:
        if not isinstance(custom_scripts,list) or len(custom_scripts)!=len(titles):return jsonify({"error":"Provide exactly one custom script for each video title."}),400
        custom_scripts=[str(script or "").strip() for script in custom_scripts]
        if any(not script for script in custom_scripts):return jsonify({"error":"Each custom script must contain narration text."}),400
        if any(len(script)>40000 for script in custom_scripts):return jsonify({"error":"Each custom script must be 40,000 characters or fewer."}),400
    try:duration=max(10,min(1800,int(body.get("duration",120))))
    except (TypeError,ValueError):return jsonify({"error":"duration must be a number of seconds (at least 10)"}),400
    resolution=str(body.get("resolution","720p")).lower()
    if resolution not in {"720x480","720p","1080p"}:return jsonify({"error":"resolution must be 720x480, 720p, or 1080p"}),400
    voice_speed=str(body.get("voice_speed","medium")).lower()
    if voice_speed not in {"slower","slow","medium","faster"}:return jsonify({"error":"voice_speed must be slower, slow, medium, or faster"}),400
    niche=str(body.get("niche","General"))[:80];privacy=body.get("privacy",os.getenv("YOUTUBE_PRIVACY_STATUS","private"))
    if privacy not in {"private","unlisted","public"}:return jsonify({"error":"privacy must be private, unlisted, or public"}),400
    thumbnail_only=bool(body.get("thumbnail_only",False))
    auto_upload=bool(body.get("auto_upload",False)) and not thumbnail_only
    batch_id=orchestrator.start_batch(titles,duration,niche,auto_upload,privacy,custom_scripts=custom_scripts,resolution=resolution,voice_speed=voice_speed,thumbnail_only=thumbnail_only)
    return jsonify({"batch_id":batch_id,"status":"queued"}),202
@app.get("/api/batch/status/<batch_id>")
def batch_status(batch_id):
    state=orchestrator.get_status(batch_id)
    if not state:return jsonify({"error":"Batch not found"}),404
    return jsonify(state)

@app.post("/api/batch/<batch_id>/stop")
def stop_batch(batch_id):
    stopped,message=orchestrator.request_stop(batch_id)
    return (jsonify({"success":True,"message":message}),202) if stopped else (jsonify({"error":message}),409)

@app.post("/api/batch/<batch_id>/retry/<int:index>/<step>")
def retry_batch_step(batch_id,index,step):
    if step=="upload" and not orchestrator.youtube.status().get("connected"):
        return jsonify({"error":"Connect your YouTube channel before retrying the upload step."}),409
    started,message=orchestrator.retry_failed_step(batch_id,index,step)
    return (jsonify({"success":True,"message":message}),202) if started else (jsonify({"error":message}),409)

@app.post("/api/batch/<batch_id>/publish/<int:index>")
def publish_video(batch_id,index):
    youtube_status=orchestrator.youtube.status()
    if not youtube_status.get("connected"):
        return jsonify({"error":"Connect your YouTube channel before publishing."}),409
    started,message=orchestrator.start_manual_publish(batch_id,index)
    return (jsonify({"success":True,"message":message}),202) if started else (jsonify({"error":message}),409)

@app.get("/api/batch/list")
def batch_list():return jsonify({"batches":orchestrator.list_batches()})
@app.post("/api/youtube/auth")
def youtube_auth():
    ok=orchestrator.youtube.authenticate(True)
    return jsonify(orchestrator.youtube.status()),(200 if ok else 400)
@app.get("/api/youtube/status")
def youtube_status():return jsonify(orchestrator.youtube.status())

@app.get("/api/video/<batch_id>/<int:index>")
def get_video(batch_id,index):
    batch=orchestrator.get_status(batch_id)
    if not batch or index<0 or index>=len(batch["videos"]):return jsonify({"error":"Video not found"}),404
    path=batch["videos"][index].get("video_path")
    if not path or not Path(path).is_file():return jsonify({"error":"Video is not ready"}),404
    return send_file(path,as_attachment=True,download_name=Path(path).name)

@app.get("/api/subtitles/<batch_id>/<int:index>")
def get_subtitles(batch_id,index):
    batch=orchestrator.get_status(batch_id)
    if not batch or index<0 or index>=len(batch["videos"]):return jsonify({"error":"Subtitle file not found"}),404
    path=batch["videos"][index].get("subtitle_path")
    if not path or not Path(path).is_file():return jsonify({"error":"Subtitles are not ready"}),404
    return send_file(path,as_attachment=True,download_name=Path(path).name,mimetype="application/x-subrip")

@app.get("/api/thumbnail/<batch_id>/<int:index>")
def get_thumbnail(batch_id,index):
    batch=orchestrator.get_status(batch_id)
    if not batch or index<0 or index>=len(batch["videos"]):return jsonify({"error":"Thumbnail not found"}),404
    path=batch["videos"][index].get("thumbnail_path")
    if not path or not Path(path).is_file():return jsonify({"error":"Thumbnail is not ready"}),404
    return send_file(path,as_attachment=True,download_name=Path(path).name,mimetype="image/jpeg")

@app.get("/api/text/models")
def text_models():
    provider=str(request.args.get("provider","openrouter")).strip().lower()
    if provider=="grok":
        provider="groq"
    if provider=="openrouter":
        try:
            return jsonify({"models":_openrouter_models(request.args.get("refresh")=="1"),"provider":provider,"free_only":True})
        except (requests.RequestException,ValueError,TypeError) as exc:
            return jsonify({"error":f"Could not load free OpenRouter text models: {exc}"}),502
    if provider=="groq":
        try:
            models,notice=_groq_models(request.args.get("refresh")=="1")
            return jsonify({"models":models,"provider":provider,"free_only":False,"notice":notice})
        except (requests.RequestException,ValueError,TypeError) as exc:
            fallback=[{"id":model,"name":model,"free":False} for model in _GROQ_FALLBACK_MODELS]
            return jsonify({"models":fallback,"provider":provider,"free_only":False,"notice":f"The live GroqCloud catalog could not be loaded ({exc}); showing fallback model choices. Check your GroqCloud key and connection."})
    return jsonify({"error":"Choose OpenRouter or Groq as the text provider."}),400

@app.get("/api/settings")
def get_settings():
    return jsonify(public_settings())

@app.post("/api/settings")
def update_settings():
    body=request.get_json(silent=True) or {}
    allowed=set(public_settings())-{"PEXELS_API_KEY_CONFIGURED", "PIXABAY_API_KEY_CONFIGURED", "OPENROUTER_API_KEY_CONFIGURED", "GROQ_API_KEY_CONFIGURED", "YOUTUBE_CREDENTIALS_CONFIGURED"}
    updates={key:value for key,value in body.items() if key in allowed}
    if body.get("PEXELS_API_KEY"):
        updates["PEXELS_API_KEY"]=str(body["PEXELS_API_KEY"]).strip()
    elif body.get("CLEAR_PEXELS_API_KEY"):
        updates["PEXELS_API_KEY"]=""
    if body.get("PIXABAY_API_KEY"):
        updates["PIXABAY_API_KEY"]=str(body["PIXABAY_API_KEY"]).strip()
    elif body.get("CLEAR_PIXABAY_API_KEY"):
        updates["PIXABAY_API_KEY"]=""
    if body.get("OPENROUTER_API_KEY"):
        updates["OPENROUTER_API_KEY"]=str(body["OPENROUTER_API_KEY"]).strip()
    elif body.get("CLEAR_OPENROUTER_API_KEY"):
        updates["OPENROUTER_API_KEY"]=""
    if body.get("GROQ_API_KEY"):
        updates["GROQ_API_KEY"]=str(body["GROQ_API_KEY"]).strip()
    elif body.get("CLEAR_GROQ_API_KEY"):
        updates["GROQ_API_KEY"]=""
    try:
        settings=save_settings(updates)
        _apply_runtime_settings(settings)
        return jsonify(public_settings(settings))
    except (ValueError, TypeError, OSError) as exc:
        return jsonify({"error":str(exc)}),400

@app.post("/api/youtube/credentials")
def save_youtube_credentials():
    upload=request.files.get("file")
    if not upload or not str(upload.filename or "").lower().endswith(".json"):
        return jsonify({"error":"Choose the OAuth client JSON file downloaded from Google Cloud."}),400
    try:
        import json
        credentials=json.loads(upload.read().decode("utf-8"))
        if not isinstance(credentials,dict) or not ("installed" in credentials or "web" in credentials):
            return jsonify({"error":"This does not look like a Google OAuth client credentials JSON file."}),400
        credential_path=APP_DATA_DIR/"client_secret.json"
        credential_path.write_text(json.dumps(credentials,indent=2),encoding="utf-8")
        if os.name!="nt": credential_path.chmod(0o600)
        orchestrator.youtube.client_file=credential_path
        orchestrator.youtube.token_file=APP_DATA_DIR/"token.json"
        orchestrator.youtube.service=None
        return jsonify({"success":True,"configured":True})
    except (UnicodeDecodeError, ValueError, OSError) as exc:
        return jsonify({"error":f"Could not save credentials: {exc}"}),400

@app.get("/api/health")
def health():return jsonify({"ok":True,"service":"CreatorFlow Web App"})

if __name__=="__main__":
    print("YouTube Auto Creator starting on http://localhost:5000")
    print(f"Text generation: {orchestrator.scripts.provider_name} {orchestrator.scripts.model or '(model not selected)'}")
    print("YouTube:","connected" if orchestrator.youtube.status()["connected"] else "not configured")
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","5000")),debug=False,threaded=True)

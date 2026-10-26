"""Flask API and static frontend for YouTube Auto Creator."""
import os
import requests
from pathlib import Path
from flask import Flask, jsonify, request, send_file, send_from_directory
from flask_cors import CORS
import common
from common import DIRS, ROOT_DIR, env_bool
from settings_store import APP_DATA_DIR, apply_environment, load_settings, public_settings, save_settings
from ollama_runtime import OllamaRuntime

_boot_settings = load_settings()
apply_environment(_boot_settings)
common.OUTPUT_DIR = Path(_boot_settings["OUTPUT_DIR"]).expanduser().resolve()
common.DIRS.clear()
common.DIRS.update({name: common.OUTPUT_DIR / name for name in ("images", "audio", "videos", "clips", "thumbnails", "local_ai", "uploads", "batch")})
for _directory in common.DIRS.values():
    _directory.mkdir(parents=True, exist_ok=True)
from batch_orchestrator import BatchOrchestrator

app=Flask(__name__,static_folder=None)
CORS(app)
app.config["MAX_CONTENT_LENGTH"]=100*1024*1024
orchestrator=BatchOrchestrator()
app.config["OLLAMA_RUNTIME"]=OllamaRuntime(load_settings)
FRONTEND=ROOT_DIR/"frontend"

def _apply_runtime_settings(settings):
    apply_environment(settings)
    output_dir=Path(settings["OUTPUT_DIR"]).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    common.OUTPUT_DIR=output_dir
    common.DIRS.clear()
    common.DIRS.update({name: output_dir/name for name in ("images", "audio", "videos", "clips", "thumbnails", "local_ai", "uploads", "batch")})
    for directory in common.DIRS.values(): directory.mkdir(parents=True, exist_ok=True)
    orchestrator.scripts.enabled=env_bool("USE_OLLAMA", True)
    orchestrator.scripts.url=os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    orchestrator.scripts.model=os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    orchestrator.seo.enabled=orchestrator.scripts.enabled
    orchestrator.seo.url=orchestrator.scripts.url
    orchestrator.seo.model=orchestrator.scripts.model
    orchestrator.images.api_key=os.getenv("PEXELS_API_KEY", "").strip()
    orchestrator.fetcher.api_key=orchestrator.images.api_key
    orchestrator.youtube.max_uploads=int(os.getenv("MAX_UPLOADS_PER_DAY", "5"))
    orchestrator.audio.voice=os.getenv("EDGE_TTS_VOICE", "en-US-AriaNeural")
    orchestrator.audio.configure(
        engine=os.getenv("TTS_ENGINE", "chatterbox"),
        chatterbox_python=os.getenv("CHATTERBOX_PYTHON", ""),
    )
    runtime=app.config.get("OLLAMA_RUNTIME")
    if runtime:
        if settings.get("USE_OLLAMA", True) and settings.get("OLLAMA_AUTO_START", True):
            runtime.start()
        else:
            runtime.deactivate()

def _gpu_status():
    status=orchestrator.local.gpu_status()
    if status.get("in_use"):
        return status
    if getattr(orchestrator.audio,"chatterbox_active",False) and getattr(orchestrator.audio,"last_device","") == "cuda":
        status.update(available=True,state="active",in_use=True,device="NVIDIA GPU",operation="Chatterbox voice generation",message="Generating natural voiceover with Chatterbox on the NVIDIA GPU.")
        return status
    if getattr(orchestrator.assembler,"encoding_active",False) and getattr(orchestrator.assembler,"last_video_encoder",None)=="NVIDIA NVENC":
        status.update(available=True,state="active",in_use=True,device="NVIDIA GPU",operation="NVENC video encoding",message="Encoding video with NVIDIA NVENC.")
        return status
    if env_bool("USE_OLLAMA",True):
        base_url=os.getenv("OLLAMA_URL","http://localhost:11434").rstrip("/")
        model_name=os.getenv("OLLAMA_MODEL","qwen2.5:7b").lower()
        try:
            response=requests.get(base_url+"/api/ps",timeout=1)
            response.raise_for_status()
            loaded=response.json().get("models",[])
            ollama_busy=False
            with orchestrator.lock:
                for batch in orchestrator.batches.values():
                    for video in batch.get("videos",[]):
                        if video.get("status")=="processing" and video.get("current_stage") in {"script","seo"}:
                            ollama_busy=True;break
                    if ollama_busy:break
            for model in loaded:
                name=str(model.get("name") or model.get("model") or "").lower()
                if name==model_name or name==model_name+":latest":
                    vram=int(model.get("size_vram") or 0)
                    if vram>0:
                        model_label=model.get("name") or model_name
                        status.update(available=True,device=status.get("device") or "Ollama GPU",vram_used_gb=round(vram/(1024**3),1),loaded=True,operation=f"Ollama · {model_label}")
                        if ollama_busy:
                            status.update(state="active",in_use=True,message=f"GPU in use by Ollama · {model_label}")
                        else:
                            status.update(state="loaded",in_use=False,message=f"Ollama model loaded in GPU memory · idle · {model_label}")
                        return status
        except (requests.RequestException,ValueError,TypeError):
            pass
    return status

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
    if len(titles)>30:return jsonify({"error":"A batch can contain at most 30 titles."}),400
    custom_scripts=body.get("custom_scripts")
    if custom_scripts is not None:
        if not env_bool("USE_OLLAMA",True):return jsonify({"error":"Custom-script review requires the local Ollama model. Enable Ollama in Settings first."}),400
        if not isinstance(custom_scripts,list) or len(custom_scripts)!=len(titles):return jsonify({"error":"Provide exactly one custom script for each video title."}),400
        custom_scripts=[str(script or "").strip() for script in custom_scripts]
        if any(not script for script in custom_scripts):return jsonify({"error":"Each custom script must contain narration text."}),400
        if any(len(script)>40000 for script in custom_scripts):return jsonify({"error":"Each custom script must be 40,000 characters or fewer."}),400
    try:duration=max(30,min(1800,int(body.get("duration",120))))
    except (TypeError,ValueError):return jsonify({"error":"duration must be a number of seconds"}),400
    resolution=str(body.get("resolution","720p")).lower()
    if resolution not in {"720p","1080p"}:return jsonify({"error":"resolution must be 720p or 1080p"}),400
    niche=str(body.get("niche","General"))[:80];privacy=body.get("privacy",os.getenv("YOUTUBE_PRIVACY_STATUS","private"))
    if privacy not in {"private","unlisted","public"}:return jsonify({"error":"privacy must be private, unlisted, or public"}),400
    batch_id=orchestrator.start_batch(titles,duration,niche,bool(body.get("auto_upload",False)),privacy,custom_scripts=custom_scripts,resolution=resolution)
    return jsonify({"batch_id":batch_id,"status":"queued"}),202
@app.get("/api/batch/status/<batch_id>")
def batch_status(batch_id):
    state=orchestrator.get_status(batch_id)
    if not state:return jsonify({"error":"Batch not found"}),404
    state["gpu_status"]=_gpu_status()
    return jsonify(state)

@app.get("/api/gpu/status")
def gpu_status():
    return jsonify(_gpu_status())
@app.get("/api/batch/list")
def batch_list():return jsonify({"batches":orchestrator.list_batches()})
@app.post("/api/youtube/auth")
def youtube_auth():
    ok=orchestrator.youtube.authenticate(True)
    return jsonify(orchestrator.youtube.status()),(200 if ok else 400)
@app.get("/api/youtube/status")
def youtube_status():return jsonify(orchestrator.youtube.status())

@app.get("/api/ollama/status")
def ollama_status():
    """Report service reachability, model installation, and actual model load state."""
    model=os.getenv("OLLAMA_MODEL", "qwen2.5:7b").strip()
    runtime=app.config.get("OLLAMA_RUNTIME")
    runtime_status=runtime.status() if runtime else None
    if not env_bool("USE_OLLAMA", True):
        return jsonify({"state":"disabled","model":model,"installed":False,"loaded":False,"detail":"Ollama use is disabled in Settings.","runtime":runtime_status})
    base_url=os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    try:
        tags_response=requests.get(f"{base_url}/api/tags", timeout=2.5)
        tags_response.raise_for_status()
        installed_models=tags_response.json().get("models", [])
        def same_model(candidate):
            name=str(candidate or "").strip().lower()
            target=model.lower()
            return name==target or name==target+":latest" or (target.endswith(":latest") and name==target[:-7])
        installed=any(same_model(entry.get("name") or entry.get("model")) for entry in installed_models)
        loaded=False
        try:
            ps_response=requests.get(f"{base_url}/api/ps", timeout=2.5)
            ps_response.raise_for_status()
            loaded=any(same_model(entry.get("name") or entry.get("model")) for entry in ps_response.json().get("models", []))
        except requests.RequestException:
            # Service is reachable even if its process-list endpoint is unavailable.
            loaded=False
        if loaded:
            state,detail="running",f"{model} is loaded and running in Ollama."
        elif installed:
            state,detail="ready",f"{model} is installed; Ollama loads it when a generation request arrives."
        else:
            state,detail="missing",f"Ollama is reachable, but {model} is not installed. Run: ollama pull {model}"
        if runtime_status and runtime_status.get("state") in {"starting", "warming", "pulling"}:
            state=runtime_status["state"]
            detail=runtime_status.get("detail", detail)
        return jsonify({"state":state,"model":model,"installed":installed,"loaded":loaded,"detail":detail,"runtime":runtime_status})
    except requests.RequestException:
        runtime_state=runtime_status.get("state") if runtime_status else None
        pending=runtime_state in {"checking", "starting", "warming", "pulling", "not-installed", "error"}
        state=runtime_state if pending else "offline"
        detail=runtime_status.get("detail") if pending else f"Cannot reach Ollama at {base_url}. Start the Ollama app/service."
        return jsonify({"state":state,"model":model,"installed":False,"loaded":False,"detail":detail,"runtime":runtime_status})
    except (ValueError, TypeError) as exc:
        return jsonify({"state":"error","model":model,"installed":False,"loaded":False,"detail":f"Could not read Ollama status: {exc}","runtime":runtime_status})

@app.post("/api/ollama/start")
def start_ollama():
    runtime=app.config.get("OLLAMA_RUNTIME")
    if not runtime:
        return jsonify({"error":"Ollama desktop runtime is unavailable."}),503
    return jsonify(runtime.activate()),202

@app.post("/api/ollama/pull")
def pull_ollama_model():
    runtime=app.config.get("OLLAMA_RUNTIME")
    if not runtime:
        return jsonify({"error":"Ollama desktop runtime is unavailable."}),503
    result=runtime.pull_model()
    return (jsonify(result),202) if result.get("started") else (jsonify(result),409)

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

@app.get("/api/settings")
def get_settings():
    return jsonify(public_settings())

@app.post("/api/settings")
def update_settings():
    body=request.get_json(silent=True) or {}
    allowed=set(public_settings())-{"PEXELS_API_KEY_CONFIGURED", "YOUTUBE_CREDENTIALS_CONFIGURED"}
    updates={key:value for key,value in body.items() if key in allowed}
    if body.get("PEXELS_API_KEY"):
        updates["PEXELS_API_KEY"]=str(body["PEXELS_API_KEY"]).strip()
    elif body.get("CLEAR_PEXELS_API_KEY"):
        updates["PEXELS_API_KEY"]=""
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
def health():return jsonify({"ok":True,"service":"CreatorFlow Desktop"})

if __name__=="__main__":
    print("YouTube Auto Creator starting on http://localhost:5000")
    print("GPU generation enabled:",orchestrator.local.available)
    print("YouTube:","connected" if orchestrator.youtube.status()["connected"] else "not configured")
    app.config["OLLAMA_RUNTIME"].start()
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","5000")),debug=False,threaded=True)

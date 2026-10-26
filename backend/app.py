"""Flask API and static frontend for YouTube Auto Creator."""
import os
import requests
from pathlib import Path
from flask import Flask, jsonify, request, send_file, send_from_directory
from flask_cors import CORS
from common import DIRS, ROOT_DIR, env_bool
from batch_orchestrator import BatchOrchestrator

app=Flask(__name__,static_folder=None)
CORS(app)
app.config["MAX_CONTENT_LENGTH"]=100*1024*1024
orchestrator=BatchOrchestrator()
FRONTEND=ROOT_DIR/"frontend"

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
    try:duration=max(30,min(1800,int(body.get("duration",120))))
    except (TypeError,ValueError):return jsonify({"error":"duration must be a number of seconds"}),400
    niche=str(body.get("niche","General"))[:80];privacy=body.get("privacy","private")
    if privacy not in {"private","unlisted","public"}:return jsonify({"error":"privacy must be private, unlisted, or public"}),400
    batch_id=orchestrator.start_batch(titles,duration,niche,bool(body.get("auto_upload",False)),privacy)
    return jsonify({"batch_id":batch_id,"status":"queued"}),202
@app.get("/api/batch/status/<batch_id>")
def batch_status(batch_id):
    state=orchestrator.get_status(batch_id)
    return (jsonify(state),200) if state else (jsonify({"error":"Batch not found"}),404)
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
    if not env_bool("USE_OLLAMA", True):
        return jsonify({"state":"disabled","model":model,"installed":False,"loaded":False,"detail":"Ollama use is disabled in backend/.env."})
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
        return jsonify({"state":state,"model":model,"installed":installed,"loaded":loaded,"detail":detail})
    except requests.RequestException:
        return jsonify({"state":"offline","model":model,"installed":False,"loaded":False,"detail":f"Cannot reach Ollama at {base_url}. Start the Ollama app/service."})
    except (ValueError, TypeError) as exc:
        return jsonify({"state":"error","model":model,"installed":False,"loaded":False,"detail":f"Could not read Ollama status: {exc}"})

@app.get("/api/video/<batch_id>/<int:index>")
def get_video(batch_id,index):
    batch=orchestrator.get_status(batch_id)
    if not batch or index<0 or index>=len(batch["videos"]):return jsonify({"error":"Video not found"}),404
    path=batch["videos"][index].get("video_path")
    if not path or not Path(path).is_file():return jsonify({"error":"Video is not ready"}),404
    return send_file(path,as_attachment=True,download_name=Path(path).name)
@app.get("/api/health")
def health():return jsonify({"ok":True,"service":"YouTube Auto Creator"})

if __name__=="__main__":
    print("YouTube Auto Creator starting on http://localhost:5000")
    print("GPU generation enabled:",orchestrator.local.available)
    print("YouTube:","connected" if orchestrator.youtube.status()["connected"] else "not configured")
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","5000")),debug=False,threaded=True)

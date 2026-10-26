"""Flask API and static frontend for YouTube Auto Creator."""
import os
from pathlib import Path
from flask import Flask, jsonify, request, send_file, send_from_directory
from flask_cors import CORS
from common import DIRS, ROOT_DIR
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

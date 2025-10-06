import os
import threading
import uuid
import time
import queue
import subprocess
import shutil
from pathlib import Path
from typing import Dict, Optional
from flask import Flask, request, jsonify, Response, abort
import requests

# ========= Config =========
RUNNER_BIND = os.getenv("RUNNER_BIND", "0.0.0.0")
RUNNER_PORT = int(os.getenv("RUNNER_PORT", "8080"))

# Absolute path to your DigitalTwin project (contains Makefile and tool folders)
DIGITALTWIN_ROOT = Path(os.getenv("DIGITALTWIN_ROOT", "/opt/digitaltwin")).resolve()

# Where per-job working dirs live (logs, inputs, temp)
WORK_ROOT = Path(os.getenv("WORK_ROOT", "/work")).resolve()

# Path to kubeconfig file mounted as secret or volume
KUBECONFIG_PATH = Path(os.getenv("KUBECONFIG_PATH", "/secrets/kubeconfig")).resolve()

# Optional: map of allowed make targets to safe values (defense-in-depth)
ALLOWED_TARGETS = set(os.getenv("ALLOWED_TARGETS", "deploy,validate,plan").split(","))

# Folder (relative to the project root) where files must be placed for the tool
# e.g., "input" or "config/ttl" — change to match your tool’s expectation
TOOL_INPUT_SUBDIR = os.getenv("TOOL_INPUT_SUBDIR", "input")

# ======== State ========
app = Flask(__name__)

class JobState:
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.status = "QUEUED"       # QUEUED | RUNNING | SUCCEEDED | FAILED
        self.created_at = time.time()
        self.updated_at = self.created_at
        self.proc: Optional[subprocess.Popen] = None
        self.log_path: Path = WORK_ROOT / job_id / "runner.log"
        self.work_dir: Path = WORK_ROOT / job_id
        self.tool_dir: Path = self.work_dir / "project"  # copy/symlink repo here
        self.input_dir: Path = self.tool_dir / TOOL_INPUT_SUBDIR
        self.last_error: Optional[str] = None
        self._log_q: "queue.Queue[str]" = queue.Queue()

    def set_status(self, s: str):
        self.status = s
        self.updated_at = time.time()

JOBS: Dict[str, JobState] = {}
JOBS_LOCK = threading.Lock()

# ========= Utilities =========

def ensure_dirs(job: JobState):
    job.work_dir.mkdir(parents=True, exist_ok=True)
    job.input_dir.mkdir(parents=True, exist_ok=True)
    # bring the Makefile project in (choose one: copy or symlink)
    if not job.tool_dir.exists():
        # faster: symlink. If you need job-local copies, switch to shutil.copytree
        job.tool_dir.symlink_to(DIGITALTWIN_ROOT, target_is_directory=True)

def download_to_file(url: str, dest: Path, timeout=120):
    # Assumes pre-signed URLs or public HTTP(S)
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)

def sse_format(event: Optional[str], data: str) -> str:
    # event: optional custom event name; data must be single-line chunks (split if needed)
    out = ""
    if event:
        out += f"event: {event}\n"
    for line in data.splitlines() or [""]:
        out += f"data: {line}\n"
    return out + "\n"

def tail_file(path: Path, start_at_end=False):
    with open(path, "a+", buffering=1) as f:
        f.flush()
    with open(path, "r", buffering=1) as f:
        if start_at_end:
            f.seek(0, os.SEEK_END)
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.2)
                continue
            yield line.rstrip("\n")

def append_log(job: JobState, text: str):
    job.log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(job.log_path, "a", buffering=1) as f:
        f.write(text.rstrip("\n") + "\n")

def stream_subprocess_logs(proc: subprocess.Popen, job: JobState):
    # Read stdout and stderr, write to file & queue for live subscribers
    def pump(stream, tag):
        for raw in iter(stream.readline, b""):
            line = raw.decode(errors="replace").rstrip("\n")
            msg = f"[{tag}] {line}"
            append_log(job, msg)
            job._log_q.put(msg)
        stream.close()

    t1 = threading.Thread(target=pump, args=(proc.stdout, "OUT"), daemon=True)
    t2 = threading.Thread(target=pump, args=(proc.stderr, "ERR"), daemon=True)
    t1.start(); t2.start()
    t1.join(); t2.join()

def run_tool(job: JobState, target: str, env: dict):
    try:
        job.set_status("RUNNING")
        append_log(job, f"Starting job {job.job_id} with target '{target}'")

        cmd = ["make", target]
        proc = subprocess.Popen(
            cmd,
            cwd=str(job.tool_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        job.proc = proc

        stream_subprocess_logs(proc, job)

        rc = proc.wait()
        if rc == 0:
            job.set_status("SUCCEEDED")
            append_log(job, f"Job {job.job_id} finished successfully.")
        else:
            job.set_status("FAILED")
            append_log(job, f"Job {job.job_id} failed with exit code {rc}")
    except Exception as e:
        job.set_status("FAILED")
        job.last_error = str(e)
        append_log(job, f"Runner exception: {e}")
    finally:
        job.proc = None

def start_job_thread(job: JobState, target: str, kube_context: Optional[str]):
    # Prepare ENV for the tool
    env = os.environ.copy()
    env["KUBECONFIG"] = str(KUBECONFIG_PATH)
    if kube_context:
        env["KUBE_CONTEXT"] = kube_context  # if your Makefile uses it

    t = threading.Thread(target=run_tool, args=(job, target, env), daemon=True)
    t.start()

# ========= API =========

@app.route("/jobs", methods=["POST"])
def create_job():
    """
    Body JSON:
    {
      "jobId": "uuid-from-nest",        // optional; if absent, we generate
      "target": "deploy",
      "urls": {
         "knowledge": "<presigned-get-url>",
         "shacl": "<presigned-get-url>"
      },
      "context": {
         "kubeContext": "my-context",   // optional
         "...": "..."
      }
    }
    """
    data = request.get_json(silent=True) or {}
    job_id = (data.get("jobId") or str(uuid.uuid4())).strip()
    target = (data.get("target") or "deploy").strip()
    urls = (data.get("urls") or {})
    knowledge_url = urls.get("knowledge")
    shacl_url = urls.get("shacl")
    context = data.get("context") or {}
    kube_context = context.get("kubeContext")

    # Validate
    if not knowledge_url or not shacl_url:
        abort(400, "urls.knowledge and urls.shacl are required")
    if ALLOWED_TARGETS and target not in ALLOWED_TARGETS:
        abort(400, f"target '{target}' not allowed")

    with JOBS_LOCK:
        if job_id in JOBS and JOBS[job_id].status in ("QUEUED", "RUNNING"):
            return jsonify({"jobId": job_id, "status": JOBS[job_id].status}), 202
        job = JobState(job_id)
        JOBS[job_id] = job

    # Prepare work folder and inputs
    try:
        ensure_dirs(job)
        # Place files where your tool expects them:
        # e.g., <project>/<TOOL_INPUT_SUBDIR>/knowledge.ttl and shacl.ttl
        knowledge_dest = job.input_dir / "knowledge.ttl"
        shacl_dest = job.input_dir / "shacl.ttl"

        append_log(job, "Downloading input files...")
        download_to_file(knowledge_url, knowledge_dest)
        download_to_file(shacl_url, shacl_dest)
        append_log(job, f"Inputs placed in {job.input_dir}")

        # Optionally: write a job metadata file consumed by Make/Python
        meta_file = job.work_dir / "job.json"
        meta_file.write_text(
            jsonify({
                "jobId": job.job_id,
                "target": target,
                "kubeContext": kube_context,
                "inputs": {
                    "knowledge": str(knowledge_dest),
                    "shacl": str(shacl_dest),
                }
            }).get_data(as_text=True)
        )

        # Kick off the tool
        start_job_thread(job, target, kube_context)

    except Exception as e:
        job.set_status("FAILED")
        job.last_error = str(e)
        append_log(job, f"Setup error: {e}")
        return jsonify({"jobId": job_id, "status": job.status, "error": job.last_error}), 500

    return jsonify({"jobId": job_id, "status": job.status})

@app.route("/jobs/<job_id>", methods=["GET"])
def get_job(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        abort(404, "Job not found")

    return jsonify({
        "jobId": job.job_id,
        "status": job.status,
        "createdAt": job.created_at,
        "updatedAt": job.updated_at,
        "logPath": str(job.log_path),
        "workDir": str(job.work_dir),
        "lastError": job.last_error,
    })

@app.route("/jobs/<job_id>/logs", methods=["GET"])
def stream_logs(job_id: str):
    """
    Server-Sent Events (SSE)
    - Sends historical lines (tail -f behavior from file)
    - Then pushes new lines written by the subprocess
    """
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        abort(404, "Job not found")

    def gen():
        # send a hello/ping so client knows stream is open
        yield sse_format("hello", f"stream-start job={job_id}")

        # 1) Replay existing file contents from the start
        if job.log_path.exists():
            for line in tail_file(job.log_path, start_at_end=False):
                yield sse_format(None, line)
                # break as soon as we hit EOF once for initial replay
                if not line:
                    break

        # 2) Live updates via queue (producer writes lines there)
        # also tail the file end, as a fallback if queue is quiet
        last_heartbeat = time.time()
        while True:
            try:
                line = job._log_q.get(timeout=1.0)
                yield sse_format(None, line)
            except queue.Empty:
                # periodic heartbeat to keep proxies alive
                now = time.time()
                if now - last_heartbeat > 15:
                    last_heartbeat = now
                    yield sse_format("ping", "keep-alive")

            # Stop streaming shortly after terminal status (but keep a short tail time)
            if job.status in ("SUCCEEDED", "FAILED"):
                # send a final event and end
                yield sse_format("done", job.status)
                break

    return Response(gen(), mimetype="text/event-stream")

@app.route("/jobs/<job_id>/cancel", methods=["POST"])
def cancel_job(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        abort(404, "Job not found")
    if job.proc and job.status == "RUNNING":
        try:
            job.proc.terminate()
            try:
                job.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                job.proc.kill()
            job.set_status("FAILED")
            append_log(job, "Job canceled by request.")
            return jsonify({"jobId": job_id, "status": job.status})
        except Exception as e:
            append_log(job, f"Cancel failed: {e}")
            abort(500, "Failed to cancel")
    return jsonify({"jobId": job_id, "status": job.status})

# Health
@app.route("/healthz", methods=["GET"])
def health():
    ok = DIGITALTWIN_ROOT.exists() and KUBECONFIG_PATH.exists()
    return ("ok", 200) if ok else ("bad", 500)

if __name__ == "__main__":
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    app.run(host=RUNNER_BIND, port=RUNNER_PORT, threaded=True)
    print(f"Runner listening on {RUNNER_BIND}:{RUNNER_PORT}")
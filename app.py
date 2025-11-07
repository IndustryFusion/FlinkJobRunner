import os
import threading
import uuid
import time
import queue
import subprocess
import shutil
import logging
from pathlib import Path
from typing import Dict, Optional
from flask import Flask, request, jsonify, Response, abort
import requests
from alerts_shacl import bp as alerts_shacl_bp

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
    
# ========= Config =========
RUNNER_BIND = os.getenv("RUNNER_BIND", "0.0.0.0")
RUNNER_PORT = int(os.getenv("RUNNER_PORT", "8080"))

# Absolute path to your DigitalTwin project (contains Makefile and tool folders)
DIGITALTWIN_ROOT = Path(os.getenv("DIGITALTWIN_ROOT", "./work/shacl2flink")).resolve()

# Where per-job working dirs live (logs, inputs, temp)
WORK_ROOT = Path(os.getenv("WORK_ROOT", "./work")).resolve()
print(f"WORK_ROOT is set to: {WORK_ROOT}")
# Path to kubeconfig file mounted as secret or volume (optional for in-cluster)
KUBECONFIG_PATH = os.getenv("KUBECONFIG_PATH", "./secrets/kubeconfig")

# Optional: map of allowed make targets to safe values (defense-in-depth)
ALLOWED_TARGETS = set(os.getenv("ALLOWED_TARGETS", "setup,flink-deploy,deploy,validate,plan,setup-and-deploy,build,flink-undeploy").split(","))

# Folder (relative to the project root) where files must be placed for the tool
# e.g., "input" or "config/ttl" — change to match your tool's expectation
TOOL_INPUT_SUBDIR = os.getenv("TOOL_INPUT_SUBDIR", "../kms")

# ======== State ========
app = Flask(__name__)
app.register_blueprint(alerts_shacl_bp)

class JobState:
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.status = "QUEUED"       # QUEUED | RUNNING | SUCCEEDED | FAILED
        self.created_at = time.time()
        self.updated_at = self.created_at
        self.proc: Optional[subprocess.Popen] = None
        self.log_path: Path = WORK_ROOT / job_id / "runner.log"
        self.work_dir: Path = WORK_ROOT / job_id
        self.tool_dir: Path = DIGITALTWIN_ROOT  # use the shacl2flink directory directly
        self.input_dir: Path = WORK_ROOT / "kms"  # use the existing kms folder
        self.last_error: Optional[str] = None
        self._log_q: "queue.Queue[str]" = queue.Queue()

    def set_status(self, s: str):
        old_status = self.status
        self.status = s
        self.updated_at = time.time()
        logger.info(f"Job {self.job_id} status changed: {old_status} -> {s}")

JOBS: Dict[str, JobState] = {}
JOBS_LOCK = threading.Lock()

def recover_jobs_from_disk():
    """Recover jobs from existing work directories on startup"""
    if not WORK_ROOT.exists():
        return
    
    recovered_count = 0
    for item in WORK_ROOT.iterdir():
        if item.is_dir() and item.name not in ["kms", "helm", "shacl2flink"]:
            # This looks like a job directory
            try:
                job_id = item.name
                log_file = item / "runner.log"
                job_meta_file = item / "job.json"
                
                if log_file.exists() or job_meta_file.exists():
                    logger.info(f"Recovering job {job_id} from disk")
                    job = JobState(job_id)
                    
                    # Try to determine status from log file
                    if log_file.exists():
                        try:
                            with open(log_file, 'r') as f:
                                last_lines = f.readlines()[-10:]  # Read last 10 lines
                                if any("finished successfully" in line for line in last_lines):
                                    job.set_status("SUCCEEDED")
                                elif any("failed with exit code" in line for line in last_lines):
                                    job.set_status("FAILED")
                                else:
                                    job.set_status("UNKNOWN")
                        except Exception as e:
                            logger.warning(f"Could not read log file for job {job_id}: {e}")
                            job.set_status("UNKNOWN")
                    else:
                        job.set_status("UNKNOWN")
                    
                    JOBS[job_id] = job
                    recovered_count += 1
            except Exception as e:
                logger.warning(f"Could not recover job from directory {item.name}: {e}")
    
    logger.info(f"Recovered {recovered_count} jobs from disk")

# ========= Utilities =========

def ensure_dirs(job: JobState):
    job.work_dir.mkdir(parents=True, exist_ok=True)
    job.input_dir.mkdir(parents=True, exist_ok=True)
    # tool_dir now points directly to the existing shacl2flink directory
    # no need to create symlinks since we're using the existing structure

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

def tail_file(path: Path, start_at_end=False, replay_only=False):
    with open(path, "a+", buffering=1) as f:
        f.flush()
    with open(path, "r", buffering=1) as f:
        if start_at_end:
            f.seek(0, os.SEEK_END)
        while True:
            line = f.readline()
            if not line:
                if replay_only:
                    # For replay mode, stop when we reach EOF
                    break
                time.sleep(0.2)
                continue
            yield line.rstrip("\n")

def append_log(job: JobState, text: str):
    job.log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(job.log_path, "a", buffering=1) as f:
        f.write(text.rstrip("\n") + "\n")
    # Also log to console for debugging
    logger.debug(f"Job {job.job_id} log: {text}")

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

        # Handle sequential targets
        if target == "setup-and-deploy":
            targets = ["setup", "build", "flink-undeploy", "flink-deploy"]
        else:
            targets = [target]

        # Run each target sequentially
        for i, current_target in enumerate(targets):
            append_log(job, f"Running step {i+1}/{len(targets)}: make {current_target}")
            
            cmd = ["make", current_target]
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
                append_log(job, f"Step {i+1}/{len(targets)} (make {current_target}) completed successfully.")
            else:
                job.set_status("FAILED")
                append_log(job, f"Step {i+1}/{len(targets)} (make {current_target}) failed with exit code {rc}")
                return  # Stop on first failure

        # All steps completed successfully
        job.set_status("SUCCEEDED")
        append_log(job, f"Job {job.job_id} finished successfully. All {len(targets)} steps completed.")
        
    except Exception as e:
        job.set_status("FAILED")
        job.last_error = str(e)
        append_log(job, f"Runner exception: {e}")
    finally:
        job.proc = None

def start_job_thread(job: JobState, target: str, kube_context: Optional[str]):
    # Prepare ENV for the tool
    env = os.environ.copy()
    # Only set KUBECONFIG if path is provided and is a file (for local development).
    # In-cluster service account mounts a directory at /var/run/secrets/kubernetes.io/serviceaccount
    # which is NOT a kubeconfig file; avoid setting KUBECONFIG to a directory to prevent kubectl errors.
    if KUBECONFIG_PATH and Path(KUBECONFIG_PATH).is_file():
        env["KUBECONFIG"] = str(KUBECONFIG_PATH)
    # For Kubernetes pods, kubectl will use in-cluster authentication automatically
    if kube_context:
        env["KUBE_CONTEXT"] = kube_context  # if your Makefile uses it

    # Add thread logging
    append_log(job, f"Starting thread for job {job.job_id} with target '{target}'")
    if kube_context:
        append_log(job, f"Using kube context: {kube_context}")
    
    t = threading.Thread(target=run_tool, args=(job, target, env), daemon=True)
    t.start()
    
    append_log(job, f"Thread started for job {job.job_id}")

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

    logger.info(f"Creating job {job_id} with target '{target}', knowledge_url: {knowledge_url[:50]}..., shacl_url: {shacl_url[:50]}...")

    # Validate
    if not knowledge_url or not shacl_url:
        logger.error(f"Job {job_id} validation failed: missing URLs")
        abort(400, "urls.knowledge and urls.shacl are required")
    if ALLOWED_TARGETS and target not in ALLOWED_TARGETS:
        logger.error(f"Job {job_id} validation failed: target '{target}' not in allowed targets: {ALLOWED_TARGETS}")
        abort(400, f"target '{target}' not allowed")

    with JOBS_LOCK:
        if job_id in JOBS:
            existing_job = JOBS[job_id]
            logger.info(f"Job {job_id} already exists with status: {existing_job.status}")
            if existing_job.status in ("QUEUED", "RUNNING"):
                return jsonify({"jobId": job_id, "status": existing_job.status}), 202
        job = JobState(job_id)
        JOBS[job_id] = job
        logger.info(f"Created new job {job_id}, total jobs in memory: {len(JOBS)}")

    # Prepare work folder and inputs
    try:
        ensure_dirs(job)
        # Download files directly to the kms folder that the Makefile uses
        knowledge_dest = job.input_dir / "knowledge.ttl"
        shacl_dest = job.input_dir / "shacl.ttl"

        append_log(job, f"Downloading input files to {job.input_dir}...")
        append_log(job, f"Downloading knowledge.ttl from: {knowledge_url}")
        download_to_file(knowledge_url, knowledge_dest)
        append_log(job, f"Downloaded knowledge.ttl ({knowledge_dest.stat().st_size} bytes)")
        
        append_log(job, f"Downloading shacl.ttl from: {shacl_url}")
        download_to_file(shacl_url, shacl_dest)
        append_log(job, f"Downloaded shacl.ttl ({shacl_dest.stat().st_size} bytes)")
        
        append_log(job, f"All input files ready in {job.input_dir}")

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

@app.route("/jobs", methods=["GET"])
def list_jobs():
    """List all jobs for debugging"""
    with JOBS_LOCK:
        job_list = []
        for job_id, job in JOBS.items():
            job_list.append({
                "jobId": job.job_id,
                "status": job.status,
                "createdAt": job.created_at,
                "updatedAt": job.updated_at,
                "lastError": job.last_error,
                "logPathExists": job.log_path.exists() if job.log_path else False,
                "workDirExists": job.work_dir.exists() if job.work_dir else False,
            })
    logger.info(f"Listing {len(job_list)} jobs")
    return jsonify({"jobs": job_list, "count": len(job_list)})

@app.route("/debug/job/<job_id>", methods=["GET"])
def debug_job(job_id: str):
    """Debug endpoint to check job existence and details"""
    with JOBS_LOCK:
        job_exists = job_id in JOBS
        all_job_ids = list(JOBS.keys())
        
    logger.info(f"Debug request for job {job_id}: exists={job_exists}")
    logger.info(f"All jobs in memory: {all_job_ids}")
    
    result = {
        "requestedJobId": job_id,
        "jobExists": job_exists,
        "totalJobsInMemory": len(all_job_ids),
        "allJobIds": all_job_ids
    }
    
    if job_exists:
        with JOBS_LOCK:
            job = JOBS[job_id]
            result.update({
                "status": job.status,
                "createdAt": job.created_at,
                "updatedAt": job.updated_at,
                "logPath": str(job.log_path),
                "logPathExists": job.log_path.exists(),
                "workDir": str(job.work_dir),
                "workDirExists": job.work_dir.exists(),
                "queueSize": job._log_q.qsize(),
                "lastError": job.last_error
            })
    
    return jsonify(result)

@app.route("/jobs/<job_id>", methods=["GET"])
def get_job(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        logger.warning(f"Job {job_id} not found")
        abort(404, "Job not found")

    logger.info(f"Getting job {job_id} details, status: {job.status}")
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
def get_logs(job_id: str):
    """
    Return the complete log file content as plain text
    """
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        all_job_ids = list(JOBS.keys())
    
    # Try to find log file even if job not in memory
    log_path = WORK_ROOT / job_id / "runner.log"
    
    if not log_path.exists():
        if job:
            logger.error(f"Log file {log_path} does not exist for job {job_id}")
        else:
            logger.error(f"Job {job_id} not found and no log file. Available jobs: {all_job_ids}")
        return Response(f"Log file not found for job {job_id}", mimetype="text/plain", status=404)
    
    logger.info(f"Serving log file for job {job_id}")
    
    try:
        with open(log_path, 'r') as f:
            content = f.read()
        return Response(content, mimetype="text/plain")
    except Exception as e:
        logger.error(f"Error reading log file {log_path}: {e}")
        return Response(f"Error reading log file: {e}", mimetype="text/plain", status=500)

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

# Add request logging middleware
@app.before_request
def log_request_info():
    logger.info(f"Request: {request.method} {request.path} - Query: {request.query_string.decode()} - Headers: {dict(request.headers)}")

# Catch-all route for debugging unknown requests
@app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
def catch_all(path):
    logger.warning(f"404 - Unknown endpoint: {request.method} /{path} - Query: {request.query_string.decode()}")
    logger.warning(f"Request headers: {dict(request.headers)}")
    logger.warning(f"Remote addr: {request.remote_addr}")
    return jsonify({"error": "Endpoint not found", "path": f"/{path}", "method": request.method}), 404

if __name__ == "__main__":
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    logger.info(f"Starting FlinkJobRunner on {RUNNER_BIND}:{RUNNER_PORT}")
    logger.info(f"DIGITALTWIN_ROOT: {DIGITALTWIN_ROOT}")
    logger.info(f"WORK_ROOT: {WORK_ROOT}")
    logger.info(f"KUBECONFIG_PATH: {KUBECONFIG_PATH}")
    logger.info(f"ALLOWED_TARGETS: {ALLOWED_TARGETS}")
    
    # Recover existing jobs from disk
    recover_jobs_from_disk()
    
    print(f"Runner listening on {RUNNER_BIND}:{RUNNER_PORT}")
    app.run(host=RUNNER_BIND, port=RUNNER_PORT, threaded=True, debug=False)
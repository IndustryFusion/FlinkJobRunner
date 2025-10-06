from fastapi import FastAPI, File, UploadFile, WebSocket, HTTPException, Form
from fastapi.responses import JSONResponse
from typing import List, Optional
import asyncio
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from app.models import JobStatus, JobResponse, JobStatusResponse
from app.job_manager import JobManager

app = FastAPI(title="FlinkJobRunner", version="1.0.0")

# Initialize job manager
job_manager = JobManager()

# Configuration - can be overridden via environment variables
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/tmp/digitaltwin")
KUBECONFIG_PATH = os.getenv("KUBECONFIG_PATH", "/root/.kube/config")


@app.on_event("startup")
async def startup_event():
    """Initialize on startup"""
    Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


@app.post("/jobs/upload", response_model=JobResponse)
async def upload_and_execute(
    files: List[UploadFile] = File(...),
    target: str = Form(...),
    project_path: Optional[str] = Form(None)
):
    """
    Upload files and execute make target
    
    Args:
        files: List of files to upload
        target: Make target to execute (e.g., 'deploy', 'build')
        project_path: Optional subdirectory path within UPLOAD_DIR
    
    Returns:
        JobResponse with job_id and status
    """
    job_id = str(uuid.uuid4())
    
    # Determine target directory
    if project_path:
        target_dir = Path(UPLOAD_DIR) / project_path / job_id
    else:
        target_dir = Path(UPLOAD_DIR) / job_id
    
    try:
        # Create target directory
        target_dir.mkdir(parents=True, exist_ok=True)
        
        # Save uploaded files
        uploaded_files = []
        for file in files:
            file_path = target_dir / file.filename
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            uploaded_files.append(file.filename)
        
        # Start job execution
        await job_manager.start_job(
            job_id=job_id,
            work_dir=str(target_dir),
            make_target=target,
            kubeconfig_path=KUBECONFIG_PATH
        )
        
        return JobResponse(
            job_id=job_id,
            status=JobStatus.RUNNING,
            message=f"Job started with {len(uploaded_files)} files",
            files=uploaded_files
        )
    
    except Exception as e:
        # Cleanup on error
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """
    Get status of a job
    
    Args:
        job_id: Job identifier
    
    Returns:
        JobStatusResponse with current status and details
    """
    job_info = job_manager.get_job_info(job_id)
    
    if not job_info:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return JobStatusResponse(**job_info)


@app.websocket("/ws/jobs/{job_id}/logs")
async def websocket_logs(websocket: WebSocket, job_id: str):
    """
    WebSocket endpoint for streaming job logs
    
    Args:
        websocket: WebSocket connection
        job_id: Job identifier
    """
    await websocket.accept()
    
    try:
        async for log_line in job_manager.stream_logs(job_id):
            await websocket.send_text(log_line)
        
        # Send final status
        job_info = job_manager.get_job_info(job_id)
        if job_info:
            await websocket.send_json({
                "type": "status",
                "status": job_info["status"],
                "exit_code": job_info.get("exit_code")
            })
    
    except Exception as e:
        await websocket.send_json({
            "type": "error",
            "message": str(e)
        })
    finally:
        await websocket.close()


@app.delete("/jobs/{job_id}")
async def cancel_job(job_id: str):
    """
    Cancel a running job
    
    Args:
        job_id: Job identifier
    
    Returns:
        Success message
    """
    success = await job_manager.cancel_job(job_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Job not found or already completed")
    
    return {"message": "Job cancelled successfully", "job_id": job_id}


@app.get("/jobs")
async def list_jobs():
    """
    List all jobs
    
    Returns:
        List of all jobs with their status
    """
    return job_manager.list_jobs()

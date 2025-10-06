from enum import Enum
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime


class JobStatus(str, Enum):
    """Job status enumeration"""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class JobResponse(BaseModel):
    """Response model for job creation"""
    job_id: str
    status: JobStatus
    message: str
    files: List[str] = []


class JobStatusResponse(BaseModel):
    """Response model for job status query"""
    job_id: str
    status: JobStatus
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    exit_code: Optional[int] = None
    error: Optional[str] = None
    work_dir: Optional[str] = None
    make_target: Optional[str] = None

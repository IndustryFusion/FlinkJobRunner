import asyncio
import os
from datetime import datetime
from typing import Dict, Optional, AsyncGenerator
from pathlib import Path
import signal

from app.models import JobStatus


class JobManager:
    """Manages job execution and status tracking"""
    
    def __init__(self):
        self.jobs: Dict[str, Dict] = {}
        self.processes: Dict[str, asyncio.subprocess.Process] = {}
        self.log_queues: Dict[str, asyncio.Queue] = {}
    
    async def start_job(
        self,
        job_id: str,
        work_dir: str,
        make_target: str,
        kubeconfig_path: str
    ):
        """
        Start a new job execution
        
        Args:
            job_id: Unique job identifier
            work_dir: Working directory for execution
            make_target: Make target to execute
            kubeconfig_path: Path to kubeconfig file
        """
        # Initialize job info
        self.jobs[job_id] = {
            "job_id": job_id,
            "status": JobStatus.RUNNING,
            "created_at": datetime.utcnow().isoformat(),
            "started_at": datetime.utcnow().isoformat(),
            "completed_at": None,
            "exit_code": None,
            "error": None,
            "work_dir": work_dir,
            "make_target": make_target
        }
        
        # Create log queue for this job
        self.log_queues[job_id] = asyncio.Queue()
        
        # Start execution in background
        asyncio.create_task(self._execute_job(job_id, work_dir, make_target, kubeconfig_path))
    
    async def _execute_job(
        self,
        job_id: str,
        work_dir: str,
        make_target: str,
        kubeconfig_path: str
    ):
        """
        Execute the make command and capture output
        
        Args:
            job_id: Unique job identifier
            work_dir: Working directory for execution
            make_target: Make target to execute
            kubeconfig_path: Path to kubeconfig file
        """
        try:
            # Set up environment with kubeconfig
            env = os.environ.copy()
            env["KUBECONFIG"] = kubeconfig_path
            
            # Start the process
            process = await asyncio.create_subprocess_exec(
                "make",
                make_target,
                cwd=work_dir,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL
            )
            
            self.processes[job_id] = process
            
            # Stream output to log queue
            if process.stdout:
                async for line in process.stdout:
                    log_line = line.decode('utf-8', errors='replace').rstrip()
                    await self.log_queues[job_id].put(log_line)
            
            # Wait for completion
            exit_code = await process.wait()
            
            # Update job status
            self.jobs[job_id]["exit_code"] = exit_code
            self.jobs[job_id]["completed_at"] = datetime.utcnow().isoformat()
            
            if exit_code == 0:
                self.jobs[job_id]["status"] = JobStatus.SUCCEEDED
            else:
                self.jobs[job_id]["status"] = JobStatus.FAILED
                self.jobs[job_id]["error"] = f"Process exited with code {exit_code}"
            
        except Exception as e:
            # Handle execution error
            self.jobs[job_id]["status"] = JobStatus.FAILED
            self.jobs[job_id]["completed_at"] = datetime.utcnow().isoformat()
            self.jobs[job_id]["error"] = str(e)
            await self.log_queues[job_id].put(f"ERROR: {str(e)}")
        
        finally:
            # Signal end of logs
            await self.log_queues[job_id].put(None)
            
            # Cleanup process reference
            if job_id in self.processes:
                del self.processes[job_id]
    
    async def stream_logs(self, job_id: str) -> AsyncGenerator[str, None]:
        """
        Stream logs for a job
        
        Args:
            job_id: Job identifier
        
        Yields:
            Log lines as they become available
        """
        if job_id not in self.log_queues:
            # Job doesn't exist or logs not available
            return
        
        queue = self.log_queues[job_id]
        
        while True:
            # Wait for next log line
            log_line = await queue.get()
            
            # None signals end of logs
            if log_line is None:
                break
            
            yield log_line
    
    def get_job_info(self, job_id: str) -> Optional[Dict]:
        """
        Get job information
        
        Args:
            job_id: Job identifier
        
        Returns:
            Job information dictionary or None if not found
        """
        return self.jobs.get(job_id)
    
    async def cancel_job(self, job_id: str) -> bool:
        """
        Cancel a running job
        
        Args:
            job_id: Job identifier
        
        Returns:
            True if job was cancelled, False if not found or already completed
        """
        if job_id not in self.jobs:
            return False
        
        job = self.jobs[job_id]
        
        # Can only cancel running jobs
        if job["status"] != JobStatus.RUNNING:
            return False
        
        # Kill the process if it exists
        if job_id in self.processes:
            process = self.processes[job_id]
            try:
                process.send_signal(signal.SIGTERM)
                
                # Wait a bit for graceful shutdown
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    # Force kill if still running
                    process.kill()
                    await process.wait()
            except ProcessLookupError:
                pass  # Process already terminated
        
        # Update job status
        job["status"] = JobStatus.CANCELLED
        job["completed_at"] = datetime.utcnow().isoformat()
        
        return True
    
    def list_jobs(self) -> Dict[str, Dict]:
        """
        List all jobs
        
        Returns:
            Dictionary of all jobs
        """
        return self.jobs

#!/usr/bin/env python3
"""
Example client demonstrating how to use the FlinkJobRunner API
"""
import asyncio
import requests
import websockets
import sys


def upload_and_start_job(files, target, project_path=None):
    """
    Upload files and start a job
    
    Args:
        files: List of file paths to upload
        target: Make target to execute
        project_path: Optional project path
    
    Returns:
        Job ID if successful, None otherwise
    """
    url = "http://localhost:8000/jobs/upload"
    
    # Prepare files for upload
    files_dict = [("files", open(f, "rb")) for f in files]
    
    # Prepare form data
    data = {"target": target}
    if project_path:
        data["project_path"] = project_path
    
    try:
        response = requests.post(url, files=files_dict, data=data)
        response.raise_for_status()
        
        result = response.json()
        print(f"✓ Job created: {result['job_id']}")
        print(f"  Status: {result['status']}")
        print(f"  Files: {', '.join(result['files'])}")
        return result['job_id']
    
    except requests.exceptions.RequestException as e:
        print(f"✗ Error uploading files: {e}")
        return None
    
    finally:
        # Close file handles
        for _, file_obj in files_dict:
            file_obj.close()


def get_job_status(job_id):
    """
    Get current job status
    
    Args:
        job_id: Job identifier
    
    Returns:
        Job status dictionary
    """
    url = f"http://localhost:8000/jobs/{job_id}"
    
    try:
        response = requests.get(url)
        response.raise_for_status()
        
        return response.json()
    
    except requests.exceptions.RequestException as e:
        print(f"✗ Error getting job status: {e}")
        return None


async def stream_job_logs(job_id):
    """
    Stream job logs via WebSocket
    
    Args:
        job_id: Job identifier
    """
    uri = f"ws://localhost:8000/ws/jobs/{job_id}/logs"
    
    try:
        async with websockets.connect(uri) as websocket:
            print(f"\n--- Streaming logs for job {job_id} ---")
            
            async for message in websocket:
                # Try to parse as JSON (status message)
                try:
                    import json
                    data = json.loads(message)
                    
                    if data.get("type") == "status":
                        print(f"\n✓ Final status: {data['status']}")
                        if data.get('exit_code') is not None:
                            print(f"  Exit code: {data['exit_code']}")
                    elif data.get("type") == "error":
                        print(f"\n✗ Error: {data['message']}")
                
                except (json.JSONDecodeError, ValueError):
                    # Regular log line
                    print(message)
            
            print("--- End of logs ---\n")
    
    except Exception as e:
        print(f"✗ Error streaming logs: {e}")


def list_jobs():
    """List all jobs"""
    url = "http://localhost:8000/jobs"
    
    try:
        response = requests.get(url)
        response.raise_for_status()
        
        jobs = response.json()
        
        if not jobs:
            print("No jobs found")
            return
        
        print(f"\nFound {len(jobs)} job(s):")
        for job_id, job_info in jobs.items():
            print(f"\n  Job ID: {job_id}")
            print(f"  Status: {job_info['status']}")
            print(f"  Target: {job_info.get('make_target', 'N/A')}")
            print(f"  Created: {job_info['created_at']}")
    
    except requests.exceptions.RequestException as e:
        print(f"✗ Error listing jobs: {e}")


def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        print("FlinkJobRunner Example Client")
        print("\nUsage:")
        print("  Upload and run:")
        print("    python example_client.py upload <target> <file1> [file2 ...]")
        print("  Check status:")
        print("    python example_client.py status <job_id>")
        print("  Stream logs:")
        print("    python example_client.py logs <job_id>")
        print("  List all jobs:")
        print("    python example_client.py list")
        print("\nExamples:")
        print("  python example_client.py upload deploy Makefile config.yaml")
        print("  python example_client.py status 550e8400-e29b-41d4-a716-446655440000")
        print("  python example_client.py logs 550e8400-e29b-41d4-a716-446655440000")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "upload":
        if len(sys.argv) < 4:
            print("Error: upload requires <target> and at least one file")
            sys.exit(1)
        
        target = sys.argv[2]
        files = sys.argv[3:]
        
        job_id = upload_and_start_job(files, target)
        
        if job_id:
            print(f"\nTo stream logs: python {sys.argv[0]} logs {job_id}")
            print(f"To check status: python {sys.argv[0]} status {job_id}")
    
    elif command == "status":
        if len(sys.argv) < 3:
            print("Error: status requires <job_id>")
            sys.exit(1)
        
        job_id = sys.argv[2]
        status = get_job_status(job_id)
        
        if status:
            print(f"\nJob Status:")
            print(f"  ID: {status['job_id']}")
            print(f"  Status: {status['status']}")
            print(f"  Target: {status.get('make_target', 'N/A')}")
            print(f"  Work Dir: {status.get('work_dir', 'N/A')}")
            print(f"  Created: {status['created_at']}")
            if status.get('started_at'):
                print(f"  Started: {status['started_at']}")
            if status.get('completed_at'):
                print(f"  Completed: {status['completed_at']}")
            if status.get('exit_code') is not None:
                print(f"  Exit Code: {status['exit_code']}")
            if status.get('error'):
                print(f"  Error: {status['error']}")
    
    elif command == "logs":
        if len(sys.argv) < 3:
            print("Error: logs requires <job_id>")
            sys.exit(1)
        
        job_id = sys.argv[2]
        asyncio.run(stream_job_logs(job_id))
    
    elif command == "list":
        list_jobs()
    
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()

# FlinkJobRunner

A FastAPI-based service for managing and executing Flink jobs on Kubernetes. This service provides a RESTful API and WebSocket interface for uploading files, executing make targets, and streaming logs in real-time.

## Features

- **File Upload**: Upload files via multipart form data
- **Job Execution**: Execute `make` targets in a managed environment
- **Real-time Log Streaming**: Stream job logs via WebSocket
- **Status Tracking**: Track job status (RUNNING → SUCCEEDED/FAILED)
- **Kubernetes Integration**: Mounted kubeconfig support for deployment
- **Job Management**: List, query, and cancel jobs

## Prerequisites

- Python 3.11+
- Docker and Docker Compose (optional, for containerized deployment)
- `make` utility
- Kubernetes cluster with configured kubeconfig (for deployment features)

## Installation

### Local Installation

1. Clone the repository:
```bash
git clone https://github.com/IndustryFusion/FlinkJobRunner.git
cd FlinkJobRunner
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Run the service:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Docker Installation

1. Build and run with Docker Compose:
```bash
docker-compose up -d
```

The service will be available at `http://localhost:8000`

## Configuration

Configure the service using environment variables:

- `UPLOAD_DIR`: Directory for uploaded files (default: `/tmp/digitaltwin`)
- `KUBECONFIG_PATH`: Path to kubeconfig file (default: `/root/.kube/config`)

Example:
```bash
export UPLOAD_DIR=/path/to/uploads
export KUBECONFIG_PATH=/path/to/kubeconfig
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## API Documentation

### Endpoints

#### Health Check
```http
GET /health
```

Returns the health status of the service.

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2024-01-01T00:00:00.000000"
}
```

#### Upload Files and Execute Job
```http
POST /jobs/upload
Content-Type: multipart/form-data
```

Upload files and execute a make target.

**Parameters:**
- `files`: List of files to upload (multipart)
- `target`: Make target to execute (form field)
- `project_path`: Optional subdirectory path (form field)

**Example using curl:**
```bash
curl -X POST http://localhost:8000/jobs/upload \
  -F "files=@file1.txt" \
  -F "files=@file2.txt" \
  -F "target=deploy" \
  -F "project_path=myproject"
```

**Response:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "RUNNING",
  "message": "Job started with 2 files",
  "files": ["file1.txt", "file2.txt"]
}
```

#### Get Job Status
```http
GET /jobs/{job_id}
```

Get the current status of a job.

**Response:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "SUCCEEDED",
  "created_at": "2024-01-01T00:00:00.000000",
  "started_at": "2024-01-01T00:00:01.000000",
  "completed_at": "2024-01-01T00:05:00.000000",
  "exit_code": 0,
  "error": null,
  "work_dir": "/tmp/digitaltwin/myproject/550e8400-e29b-41d4-a716-446655440000",
  "make_target": "deploy"
}
```

#### Stream Job Logs (WebSocket)
```
WS /ws/jobs/{job_id}/logs
```

Connect to this WebSocket endpoint to receive real-time log streaming.

**Example using JavaScript:**
```javascript
const ws = new WebSocket('ws://localhost:8000/ws/jobs/{job_id}/logs');

ws.onmessage = (event) => {
  const data = event.data;
  if (typeof data === 'string') {
    console.log('Log:', data);
  } else {
    const status = JSON.parse(data);
    console.log('Status:', status);
  }
};
```

**Example using Python:**
```python
import asyncio
import websockets

async def stream_logs(job_id):
    uri = f"ws://localhost:8000/ws/jobs/{job_id}/logs"
    async with websockets.connect(uri) as websocket:
        async for message in websocket:
            print(message)

asyncio.run(stream_logs("550e8400-e29b-41d4-a716-446655440000"))
```

#### List All Jobs
```http
GET /jobs
```

List all jobs and their current status.

**Response:**
```json
{
  "550e8400-e29b-41d4-a716-446655440000": {
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "status": "SUCCEEDED",
    "created_at": "2024-01-01T00:00:00.000000",
    ...
  }
}
```

#### Cancel Job
```http
DELETE /jobs/{job_id}
```

Cancel a running job.

**Response:**
```json
{
  "message": "Job cancelled successfully",
  "job_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

## Job Status Values

- `PENDING`: Job is queued but not yet started
- `RUNNING`: Job is currently executing
- `SUCCEEDED`: Job completed successfully (exit code 0)
- `FAILED`: Job failed (non-zero exit code or error)
- `CANCELLED`: Job was cancelled by user

## Interactive API Documentation

Once the service is running, visit:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## Example Workflow

1. **Upload files and start a job:**
```bash
curl -X POST http://localhost:8000/jobs/upload \
  -F "files=@Makefile" \
  -F "files=@config.yaml" \
  -F "target=deploy"
```

2. **Get the job_id from the response and check status:**
```bash
curl http://localhost:8000/jobs/{job_id}
```

3. **Stream logs in real-time:**
```bash
# Using websocat (install via: cargo install websocat)
websocat ws://localhost:8000/ws/jobs/{job_id}/logs
```

## Development

### Running Tests

```bash
pytest tests/
```

### Code Style

```bash
# Format code
black app/

# Lint code
flake8 app/
```

## Architecture

The service consists of three main components:

1. **FastAPI Application** (`app/main.py`): Handles HTTP endpoints and WebSocket connections
2. **Job Manager** (`app/job_manager.py`): Manages job execution, process lifecycle, and log streaming
3. **Models** (`app/models.py`): Pydantic models for request/response validation

### Job Execution Flow

1. Client uploads files via `/jobs/upload` endpoint
2. Files are saved to `UPLOAD_DIR/{project_path}/{job_id}/`
3. Job manager spawns `make {target}` process in the work directory
4. Process output is captured and queued for streaming
5. Job status is updated based on exit code
6. Clients can connect via WebSocket to stream logs in real-time

## Security Considerations

- Ensure kubeconfig has appropriate RBAC permissions
- Consider implementing authentication/authorization for production use
- Validate and sanitize uploaded files
- Limit file upload sizes
- Use environment-specific configurations

## License

Apache License 2.0 - See [LICENSE](LICENSE) file for details

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
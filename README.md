# FlinkJobRunner

## 🚀 Overview

FlinkJobRunner is a containerized Flask-based job orchestration service that automates the deployment and management of Apache Flink streaming jobs with SHACL (Shapes Constraint Language) validation. It provides a RESTful API for running complex data processing workflows that combine semantic data validation, knowledge graph processing, and distributed stream processing.

## 🏗️ Architecture

The application acts as a job runner that:

1. **Receives semantic data files** (RDF knowledge graphs and SHACL constraint files) via pre-signed URLs
2. **Orchestrates multi-step workflows** using Make targets for setup, validation, and deployment
3. **Manages Kubernetes resources** for Flink cluster deployment and database operations
4. **Provides real-time logging** through Server-Sent Events (SSE) for job monitoring
5. **Handles PostgreSQL database operations** for storing processed semantic data

## 🔧 Key Features

- **🌐 RESTful API**: Create, monitor, and manage jobs via HTTP endpoints
- **📊 Real-time Monitoring**: Server-Sent Events (SSE) for live job log streaming
- **🔄 Sequential Workflows**: Support for complex multi-step operations (`setup-and-deploy`)
- **☸️ Kubernetes Native**: Designed for in-cluster execution with proper RBAC
- **🗄️ Database Integration**: PostgreSQL operations with secure credential management
- **🛡️ Security**: Runs as non-root user with minimal required permissions
- **📦 Containerized**: Docker-ready with all required tools pre-installed

## 🛠️ Supported Tools & Technologies

The container includes all necessary tools for the complete workflow:

- **Apache Flink**: Stream processing engine deployment
- **Kubernetes**: Cluster management (kubectl, helm, helmfile)
- **PostgreSQL**: Database client (psql) for data operations
- **SHACL Processing**: Python libraries (rdflib, pyshacl) for semantic validation
- **YAML Processing**: yq v3.4.1 for configuration management
- **Build Tools**: Make, Python 3.12, and development dependencies

## 📋 Prerequisites

- Docker for building the container image
- Kubernetes cluster with:
  - PostgreSQL database (acid-cluster or similar)
  - Strimzi Kafka operator
  - Flink operator
  - Appropriate RBAC permissions

## 🚀 Quick Start

### 1. Build the Container

```bash
# Simple build
./build.sh

# Build with custom name and tag
IMAGE_NAME=my-flink-runner IMAGE_TAG=v1.0.0 ./build.sh

# Build and push to registry
REGISTRY=your-registry.com ./build.sh
```

### 2. Run Locally (Development)

```bash
# With kubeconfig file
docker run -p 8080:8080 \
  -v ~/.kube/config:/app/secrets/kubeconfig \
  flink-job-runner

# Check health
curl http://localhost:8080/healthz
```

### 3. Deploy to Kubernetes

```bash
# Update the image name in k8s-deployment.yaml, then:
kubectl apply -f k8s-deployment.yaml
```

## 📡 API Usage

### Create a Job

```bash
POST /jobs
Content-Type: application/json

{
  "jobId": "optional-custom-id",
  "target": "setup-and-deploy",
  "urls": {
    "knowledge": "https://your-bucket/knowledge.ttl",
    "shacl": "https://your-bucket/shacl.ttl"
  },
  "context": {
    "kubeContext": "optional-context"
  }
}
```

### Monitor Job Progress

```bash
# Get job status
GET /jobs/{jobId}

# Stream live logs (Server-Sent Events)
GET /jobs/{jobId}/logs
```

### Cancel Running Job

```bash
POST /jobs/{jobId}/cancel
```

## 🎯 Supported Targets

- **`setup`**: Install dependencies and prepare environment
- **`flink-deploy`**: Deploy Flink cluster and processing jobs
- **`setup-and-deploy`**: Sequential execution of setup + flink-deploy
- **`validate`**: Run SHACL validation only
- **`plan`**: Generate deployment plan without execution

## 🔒 Security & RBAC

The application requires the following Kubernetes permissions:

- **Pods/Services**: CRUD operations for Flink components
- **Deployments**: Scaling and management of Strimzi operators  
- **Secrets**: Access to database credentials
- **ConfigMaps**: Configuration management
- **KafkaTopics**: Strimzi resource management

See `k8s-deployment.yaml` for complete RBAC configuration.

## 📊 Monitoring & Logging

### Health Check
```bash
curl http://localhost:8080/healthz
```

### Real-time Logs
```javascript
// JavaScript example for SSE log streaming
const eventSource = new EventSource('/jobs/your-job-id/logs');
eventSource.onmessage = function(event) {
  console.log('Log:', event.data);
};
```

### Job Status
```bash
curl http://localhost:8080/jobs/your-job-id
```

## 🐳 Container Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `RUNNER_BIND` | `0.0.0.0` | Flask server bind address |
| `RUNNER_PORT` | `8080` | Flask server port |
| `DIGITALTWIN_ROOT` | `/app/work/shacl2flink` | Path to shacl2flink tools |
| `WORK_ROOT` | `/app/work` | Working directory for jobs |
| `KUBECONFIG` | `` | Kubeconfig path (empty for in-cluster) |
| `ALLOWED_TARGETS` | `setup,flink-deploy,...` | Comma-separated allowed targets |

### Volume Mounts

- `/app/work` - Persistent storage for job data (recommended: 10Gi)
- `/var/run/secrets/kubernetes.io/serviceaccount` - Service account token (auto-mounted)

## 🔧 Development

### Local Development Setup

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies  
pip install -r requirements.txt
pip install -r work/shacl2flink/requirements.txt
pip install -r work/shacl2flink/requirements-dev.txt

# Run locally
python3 app.py
```

### Project Structure

```
FlinkJobRunner/
├── app.py                    # Main Flask application
├── alerts_shacl.py          # SHACL alerts blueprint
├── requirements.txt         # Root Python dependencies
├── Dockerfile              # Container definition
├── build.sh               # Build script
├── k8s-deployment.yaml    # Kubernetes deployment
├── work/
│   ├── shacl2flink/       # Core processing tools
│   │   ├── Makefile       # Build and deployment targets
│   │   ├── requirements.txt
│   │   └── requirements-dev.txt
│   └── helm/              # Helm charts and configurations
└── secrets/
    └── kubeconfig         # Local development kubeconfig
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test locally and in Kubernetes
5. Submit a pull request

## 📄 License

See LICENSE file for details.

---

**Built for Industry 4.0 semantic data processing workflows** 🏭✨

## Security settings

| Variable | Effect |
|---|---|
| `RUNNER_TOKEN` | When set, every request except `GET /healthz` needs `Authorization: Bearer <RUNNER_TOKEN>`; others get 401. Factory Manager already sends its own `RUNNER_TOKEN`, so set the **same value on both**. When empty, the API stays open and a warning is logged at start-up. |
| `ALLOWED_DOWNLOAD_HOSTS` | Comma-separated hosts that `POST /jobs` may download `urls.knowledge` / `urls.shacl` from, normally the object store's host. Other hosts get 400. When empty, any http/https host is allowed and a warning is logged. |

Request logs never include headers, and download URLs are logged without their query string (pre-signed URLs carry their signature there).

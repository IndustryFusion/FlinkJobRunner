# Base
FROM python:3.11-slim

# Install OS deps: make, bash, curl, git, kubectl, helm
RUN apt-get update && apt-get install -y --no-install-recommends \
    make bash curl ca-certificates git tar gzip \
    && rm -rf /var/lib/apt/lists/*

# kubectl
ARG KUBECTL_VERSION=v1.30.0
RUN curl -L -o /usr/local/bin/kubectl \
    https://storage.googleapis.com/kubernetes-release/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl \
    && chmod +x /usr/local/bin/kubectl

# helm
ARG HELM_VERSION=v3.15.1
RUN curl -L https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz \
    | tar -xz -C /tmp && mv /tmp/linux-amd64/helm /usr/local/bin/helm && rm -rf /tmp/linux-amd64

# App dirs
RUN mkdir -p /app /work /secrets /opt
WORKDIR /app

# Copy runner
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py /app/app.py

# Copy/clone your DigitalTwin project into image (or mount at runtime)
# Option A: baked-in
# COPY digitaltwin/ /opt/digitaltwin/
# Option B: clone at build
# RUN git clone --depth=1 https://example.com/your/DigitalTwin.git /opt/digitaltwin

# Runtime env
ENV RUNNER_BIND=0.0.0.0 \
    RUNNER_PORT=8080 \
    DIGITALTWIN_ROOT=/opt/digitaltwin \
    WORK_ROOT=/work \
    KUBECONFIG_PATH=/secrets/kubeconfig \
    ALLOWED_TARGETS=deploy,validate,plan \
    TOOL_INPUT_SUBDIR=input

EXPOSE 8080

# Use gunicorn for production
CMD ["gunicorn", "-b", "0.0.0.0:8080", "-w", "2", "--threads", "4", "app:app"]

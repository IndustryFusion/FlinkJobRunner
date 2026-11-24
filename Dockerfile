# Security baseline (ifx-docs 14.1, step 4): pinned base image, and every tool
# download pinned to a version and checked against its published SHA-256.
FROM python:3.12.16-slim-trixie

ARG KUBECTL_VERSION=v1.37.2
ARG KUBECTL_SHA256=c04487523e7aa7d1493f7808174c899db93d7a9cf57531420cd76b9bd415f8f4
ARG YQ_VERSION=3.4.1
ARG YQ_SHA256=adbc6dd027607718ac74ceac15f74115ac1f3caef68babfb73246929d4ffb23c
ARG HELM_VERSION=v3.22.1
ARG HELM_SHA256=896cf63684a42a723d61c980369d6e588330927deb13676ca0d85006dd6da0ea
ARG HELMFILE_VERSION=0.158.1
ARG HELMFILE_SHA256=b8843105157d4281847f54c35a3100afce4175a8b44a3539d54f9194f5497162
ARG HELM_DIFF_VERSION=v3.15.15

# Set working directory
WORKDIR /app

# Install system dependencies and tools
RUN apt-get update && apt-get install -y \
    # Basic utilities
    curl \
    wget \
    unzip \
    tar \
    gzip \
    git \
    # Network utilities
    netcat-openbsd \
    # PostgreSQL client
    postgresql-client \
    # Build dependencies for Python packages
    build-essential \
    # SQLite with PCRE support
    sqlite3 \
    libsqlite3-dev \
    # Clean up
    && rm -rf /var/lib/apt/lists/*

# Install kubectl
RUN curl -fsSLo kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
    && echo "${KUBECTL_SHA256}  kubectl" | sha256sum -c - \
    && chmod +x kubectl \
    && mv kubectl /usr/local/bin/

# Install yq v3 (the version compatible with your Makefile)
RUN curl -fsSLo /usr/local/bin/yq "https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_amd64" \
    && echo "${YQ_SHA256}  /usr/local/bin/yq" | sha256sum -c - \
    && chmod +x /usr/local/bin/yq

# Install Helm 3 from its release archive (was: the get-helm-3 script from the helm main branch)
RUN curl -fsSLo helm.tar.gz "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz" \
    && echo "${HELM_SHA256}  helm.tar.gz" | sha256sum -c - \
    && tar -xzf helm.tar.gz --strip-components=1 linux-amd64/helm \
    && mv helm /usr/local/bin/helm \
    && rm helm.tar.gz

# Create app user (security best practice)
RUN useradd -m -u 1000 appuser

# Copy Python requirements and install
# Root requirements
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy and install shacl2flink requirements
COPY work/shacl2flink/requirements.txt work/shacl2flink/requirements-dev.txt ./shacl2flink/
RUN pip install --no-cache-dir -r shacl2flink/requirements.txt -r shacl2flink/requirements-dev.txt

# Copy application code
COPY . .

# Verify work directory structure was copied correctly
RUN echo "Verifying work directory structure:" \
    && ls -la work/ || echo "No work directory" \
    && echo "Contents of work/kms/:" \
    && ls -la work/kms/ || echo "No kms directory" \
    && echo "Contents of work/shacl2flink/:" \
    && ls -la work/shacl2flink/ || echo "No shacl2flink directory" \
    && echo "Contents of work/helm/:" \
    && ls -la work/helm/ || echo "No helm directory"

# Create compatibility symlinks so scripts can access /app/kms, /app/helm, etc.
RUN ln -sf /app/work/kms /app/kms \
    && ln -sf /app/work/helm /app/helm \
    && ln -sf /app/work/shacl2flink /app/shacl2flink \
    && echo "Created symlinks:" \
    && ls -la /app/kms /app/helm /app/shacl2flink

# Create secrets directory and set permissions (work dirs already copied above)
RUN mkdir -p secrets \
    && chmod -R 755 work secrets

# Install helmfile in the helm directory
RUN curl -fsSLo helmfile.tar.gz "https://github.com/helmfile/helmfile/releases/download/v${HELMFILE_VERSION}/helmfile_${HELMFILE_VERSION}_linux_amd64.tar.gz" \
    && echo "${HELMFILE_SHA256}  helmfile.tar.gz" | sha256sum -c - \
    && tar -xzf helmfile.tar.gz helmfile \
    && chmod +x helmfile \
    && mv helmfile /app/work/helm/ \
    && rm helmfile.tar.gz

# Set ownership and ensure write permissions
RUN chown -R appuser:appuser /app \
    && chmod -R u+w /app/work

# Switch to non-root user
USER appuser

# Install Helm diff plugin as appuser
RUN helm plugin install https://github.com/databus23/helm-diff --version ${HELM_DIFF_VERSION}

# Set up SQLite configuration
RUN echo ".headers on" > ~/.sqliterc \
    && echo ".mode column" >> ~/.sqliterc \
    && echo ".load /usr/lib/sqlite3/pcre2.so" >> ~/.sqliterc

# Expose port
EXPOSE 8080

# Environment variables with defaults
ENV RUNNER_BIND=0.0.0.0
ENV RUNNER_PORT=8080
ENV DIGITALTWIN_ROOT=/app/work/shacl2flink
ENV WORK_ROOT=/app/work
# For Kubernetes pods, kubectl will automatically use the service account
# Set KUBECONFIG to empty to use in-cluster authentication
ENV KUBECONFIG=
ENV ALLOWED_TARGETS=setup,setup-and-deploy,flink-deploy,build,flink-undeploy

# Health: /healthz answers as soon as the API is up (no token needed)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://localhost:${RUNNER_PORT}/healthz" >/dev/null || exit 1

# Run the application
CMD ["python3", "app.py"]
FROM python:3.12-slim

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
RUN curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \
    && chmod +x kubectl \
    && mv kubectl /usr/local/bin/

# Install yq v3 (the version compatible with your Makefile)
RUN wget https://github.com/mikefarah/yq/releases/download/3.4.1/yq_linux_amd64 -O /usr/local/bin/yq \
    && chmod +x /usr/local/bin/yq

# Install Helm
RUN curl -fsSL -o get_helm.sh https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 \
    && chmod 700 get_helm.sh \
    && ./get_helm.sh \
    && rm get_helm.sh

# Install Helm diff plugin
RUN helm plugin install https://github.com/databus23/helm-diff

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
    && ls -la work/kms/ || echo "No kms directory" \
    && ls -la work/shacl2flink/ || echo "No shacl2flink directory" \
    && ls -la work/helm/ || echo "No helm directory"

# Create secrets directory and set permissions (work dirs already copied above)
RUN mkdir -p secrets \
    && chmod -R 755 work secrets

# Install helmfile in the helm directory
RUN wget https://github.com/helmfile/helmfile/releases/download/v0.158.1/helmfile_0.158.1_linux_amd64.tar.gz \
    && tar -xzf helmfile_0.158.1_linux_amd64.tar.gz helmfile \
    && chmod +x helmfile \
    && mv helmfile /app/work/helm/ \
    && rm helmfile_0.158.1_linux_amd64.tar.gz

# Set ownership and ensure write permissions
RUN chown -R appuser:appuser /app \
    && chmod -R u+w /app/work

# Switch to non-root user
USER appuser

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
ENV ALLOWED_TARGETS=setup,setup-and-deploy,flink-deploy

# Run the application
CMD ["python3", "app.py"]
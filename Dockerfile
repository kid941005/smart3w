# Smart3W MCP Server - Docker image
# =============================================================================
# Build: docker build -t smart3w-mcp .
# Run:   docker run -p 50826:50826 -e SEARXNG_INSTANCE=... smart3w-mcp
# =============================================================================

FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.title="Smart3W MCP Server"
LABEL org.opencontainers.image.description="Web search & scraping via MCP (SearXNG + curl + Chrome)"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.source="https://github.com/kid941005/smart3w"

# --------------------------------------------------------------------------
# Install system dependencies: curl + Chrome for scrapling
# --------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install Google Chrome Stable
RUN curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
    | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
    > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# --------------------------------------------------------------------------
# Install Python dependencies
# --------------------------------------------------------------------------
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --------------------------------------------------------------------------
# Copy smart3w
# --------------------------------------------------------------------------
COPY scripts/ ./scripts/
COPY mcp_server.py .

RUN chmod +x scripts/fetch.sh

# --------------------------------------------------------------------------
# Runtime
# --------------------------------------------------------------------------
ENV SEARXNG_INSTANCE=https://searxng.hqgg.top:59826
ENV SMART3W_PORT=50826
ENV SMART3W_TIMEOUT=30

EXPOSE 50826

CMD ["python3", "mcp_server.py"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python3 -c "import socket,os; socket.create_connection(('127.0.0.1', int(os.environ.get('SMART3W_PORT','50826'))), 3).close()" || exit 1

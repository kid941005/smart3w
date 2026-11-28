# Smart3W MCP Server - Docker image
# =============================================================================
# Build: docker build -t smart3w-mcp .
# Run:   docker run -p 50826:50826 -e SEARXNG_INSTANCE=... smart3w-mcp
# =============================================================================

# ---------- Rust 加速后端构建阶段 ----------
# 编译独立 sidecar 二进制（HTML→Markdown / PDF / XLSX / CSV），
# 运行时由 smart3w_rust.py 通过子进程调用；缺失时自动回退纯 Python。
FROM rust:1-slim-bookworm AS rustbuild
WORKDIR /build
COPY rust/ ./rust/
RUN cd rust && cargo build --release --locked

FROM python:3.12-slim-bookworm

# 构建信息（可选）：
# docker build --build-arg SMART3W_BUILD_COMMIT=$(git rev-parse --short HEAD) \
#              --build-arg SMART3W_BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ) .
ARG SMART3W_BUILD_COMMIT=unknown
ARG SMART3W_BUILD_TIME=unknown
# 非 root 运行用户（可用 build-arg 覆盖以对齐宿主机挂载目录属主）
ARG SMART3W_UID=10001
ARG SMART3W_GID=10001

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

LABEL org.opencontainers.image.title="Smart3W MCP Server"
LABEL org.opencontainers.image.description="Web search & scraping via MCP (SearXNG + Python HTTP/curl + Chrome)"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.source="https://github.com/kid941005/smart3w"

# --------------------------------------------------------------------------
# Install system dependencies: curl + Chrome for scrapling
# Python HTTP 抓取器使用容器内 Python 的 httpx；curl 作为回退
# --------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install Google Chrome Stable
RUN curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
    | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" \
    > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# 创建非 root 用户；Chrome 默认沙箱依赖此用户而非 root
RUN groupadd --gid "${SMART3W_GID}" smart3w \
    && useradd --uid "${SMART3W_UID}" --gid smart3w --create-home --shell /usr/sbin/nologin smart3w

# --------------------------------------------------------------------------
# Install Python dependencies
# --------------------------------------------------------------------------
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --------------------------------------------------------------------------
# Install smart3w（与 wheel 相同的打包路径，顺带验证 package-data 是否完整）
# --------------------------------------------------------------------------
COPY pyproject.toml README.md MANIFEST.in LICENSE ./
COPY scripts/ ./scripts/
COPY mcp_server.py smart3w_content.py smart3w_core.py smart3w_discovery.py smart3w_document.py smart3w_fetch_utils.py smart3w_interact.py smart3w_url_utils.py smart3w_rust.py smart3w_version.py ./
COPY smart3w_mcp_tools/ ./smart3w_mcp_tools/

# 从构建阶段复制 Rust 二进制
COPY --from=rustbuild /build/rust/target/release/smart3w-rs /usr/local/bin/smart3w-rs

RUN chmod +x scripts/fetch.sh && pip install --no-cache-dir --no-deps . \
    && mkdir -p /data/profiles /data/jobs \
    && chown -R smart3w:smart3w /app /data/profiles /data/jobs

# --------------------------------------------------------------------------
# Runtime
# --------------------------------------------------------------------------
ENV SEARXNG_INSTANCE=https://searxng.example.com:59826
ENV SMART3W_HOST=0.0.0.0
ENV SMART3W_PORT=50826
ENV SMART3W_TIMEOUT=30
ENV SMART3W_RS_BIN=/usr/local/bin/smart3w-rs
ENV SMART3W_BUILD_COMMIT=${SMART3W_BUILD_COMMIT} \
    SMART3W_BUILD_TIME=${SMART3W_BUILD_TIME} \
    HOME=/home/smart3w

EXPOSE 50826

USER smart3w

CMD ["smart3w-mcp"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python3 /app/scripts/healthcheck.py || exit 1

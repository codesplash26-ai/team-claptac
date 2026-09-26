# ATLAS: the API, the agents and the console in one image.
#
#   docker compose up --build        (see compose.yaml and the README)
#
# Stage 1 builds the console from frontend/ so the image never depends on a stale committed bundle.
# Stage 2 is the Python runtime; no Node is needed to run.

FROM node:20-alpine AS console
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# vite writes to ../atlas/dashboard/static/manager (vite.config.ts)
RUN mkdir -p /src/atlas/dashboard/static && npm run build


FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .
COPY --from=console /src/atlas/dashboard/static/manager ./atlas/dashboard/static/manager

# Everything the system writes goes under /app/state (a volume in compose.yaml), so the repo's bundled
# data (road networks, scenarios, reference files) is never hidden by the volume.
ENV DATABASE_URL=sqlite:////app/state/atlas.db \
    ATLAS_LLM_SETTINGS_PATH=/app/state/llm-settings.json
RUN mkdir -p /app/state /app/outputs && useradd --create-home --uid 10001 atlas \
    && chown -R atlas /app/state /app/outputs /app/data
USER atlas

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "atlas.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Financial RAG — FastAPI + chat UI, CPU-only (fastembed/ONNX, no GPU needed).
#
# Build:
#   docker build -t financial-rag .
#
# Run (mount a volume for data/ so ingested filings + the Qdrant index
# survive container restarts, and pass secrets via env vars, not the image):
#   docker run -p 8000:8000 \
#     -e groq_api=YOUR_GROQ_KEY \
#     -e edgar_email=you@example.com \
#     -v financial_rag_data:/app/data \
#     financial-rag
#
# First request after a fresh volume has no indexed filings — run ingestion
# once (either `docker exec <container> python run_ingestion.py`, or let the
# agentic auto-ingest path index companies on demand as they're asked about).

# Pinned to the same minor version used for local development and the Phase 1
# baseline (CPython 3.12.14), where fastembed + onnxruntime were verified on
# macOS arm64. Fixes spec issue N2: v1 pinned 3.10 here while running 3.12
# locally. NOT yet verified inside this image — `docker build` is first
# exercised in P5-01 (T5-01).
FROM python:3.12-slim

WORKDIR /app

# lxml/pandas need a C toolchain to build from sdist on some platforms.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Serving requirements only — the dev and RAGAS stacks are deliberately absent
# from the image (spec issue N3).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# data/ is meant to be a mounted volume, not baked into the image — see the
# gitignored subpaths (raw/, qdrant/, parsed/, chunks/) in .gitignore.
RUN mkdir -p data

EXPOSE 8000

# Warm-up (model download + load) happens on first startup via api/app.py's
# lifespan handler, so the first request after a cold start is slower.
#
# Binds to $PORT when the platform sets one (as most PaaS hosts do),
# falling back to 8000 for local `docker run`. Shell form so $PORT expands.
CMD uvicorn api.app:app --host 0.0.0.0 --port ${PORT:-8000}

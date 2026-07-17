# Build from the repo root so the shared uv workspace lockfile is in context:
#   docker build -f backend/Dockerfile -t flowsage-backend .
#   docker run --rm -p 8000:8000 --env-file .env --network host flowsage-backend
FROM python:3.11-slim

RUN pip install --no-cache-dir uv

WORKDIR /workspace
COPY pyproject.toml uv.lock ./
COPY backend ./backend
COPY scripts/flowsage-predict ./scripts/flowsage-predict
COPY scripts/flowsage-graph ./scripts/flowsage-graph

RUN uv sync --frozen --no-dev --package flowsage-backend

RUN useradd --create-home --uid 1000 appuser
# Pre-create and own the uploads mount point before the volume attaches: Docker
# seeds a fresh named volume from whatever's already at the mount point in the
# image (including ownership), so this is what makes appuser able to write there
# instead of it staying root-owned.
RUN mkdir -p /data/uploads && chown -R appuser:appuser /data
USER appuser

EXPOSE 8000
CMD ["/workspace/.venv/bin/flowsage-backend"]

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
USER appuser

EXPOSE 8000
CMD ["/workspace/.venv/bin/flowsage-backend"]

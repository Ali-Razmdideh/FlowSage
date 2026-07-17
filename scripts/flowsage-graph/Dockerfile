# Build from the repo root so the shared uv workspace lockfile is in context:
#   docker build -f scripts/flowsage-graph/Dockerfile -t flowsage-graph .
#   docker run --rm --network host \
#     -v "$PWD/scripts/sample_data:/data/sample_data:ro" -v "$PWD/out:/data/out" \
#     flowsage-graph run --events /data/sample_data/events.jsonl \
#     --out /data/out/funnel_report.html
FROM python:3.11-slim

RUN pip install --no-cache-dir uv

WORKDIR /workspace
COPY pyproject.toml uv.lock ./
COPY scripts/flowsage-graph ./scripts/flowsage-graph
COPY scripts/flowsage-predict ./scripts/flowsage-predict

RUN uv sync --frozen --no-dev --package flowsage-graph

RUN useradd --create-home --uid 1000 appuser
WORKDIR /data
RUN chown appuser:appuser /data
USER appuser

ENTRYPOINT ["/workspace/.venv/bin/flowsage-graph"]

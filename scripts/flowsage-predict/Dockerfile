# Build from the repo root so the shared uv workspace lockfile is in context:
#   docker build -f scripts/flowsage-predict/Dockerfile -t flowsage-predict .
#   docker run --rm -e ANTHROPIC_API_KEY -v "$PWD/screens:/data/screens:ro" \
#     -v "$PWD/out:/data/out" flowsage-predict \
#     run --screenshots /data/screens --persona novice --goal "Complete purchase" \
#     --flow-name "Checkout Flow" --out /data/out/friction_report.md
FROM python:3.11-slim

RUN pip install --no-cache-dir uv

WORKDIR /workspace
COPY pyproject.toml uv.lock ./
COPY scripts/flowsage-predict ./scripts/flowsage-predict
COPY scripts/flowsage-graph ./scripts/flowsage-graph

RUN uv sync --frozen --no-dev --package flowsage-predict

RUN useradd --create-home --uid 1000 appuser
WORKDIR /data
RUN chown appuser:appuser /data
USER appuser

ENTRYPOINT ["/workspace/.venv/bin/flowsage-predict"]

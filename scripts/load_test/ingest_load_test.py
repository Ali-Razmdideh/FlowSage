"""Manual load-test tool for `POST /v1/events`. Not part of the pytest suite or
CI -- run by hand against a live stack (see README.md in this directory).

Usage:
    uv run --project backend python scripts/load_test/ingest_load_test.py \\
        --url http://localhost:8000 --api-key fs_... --concurrency 20 --total 2000
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx


@dataclass
class Result:
    latencies_ms: list[float] = field(default_factory=list)
    errors: int = 0


async def _send_one(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    url: str,
    api_key: str,
    batch_size: int,
    result: Result,
) -> None:
    session_id = str(uuid.uuid4())
    payload = [
        {
            "session_id": session_id,
            "screen": "load-test-screen",
            "event": "screen_view",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "device": "load-test",
            "cohort": "load-test",
        }
        for _ in range(batch_size)
    ]
    async with semaphore:
        start = time.perf_counter()
        try:
            response = await client.post(url, json=payload, headers={"X-API-Key": api_key})
            elapsed_ms = (time.perf_counter() - start) * 1000
            if response.status_code != 201:
                result.errors += 1
            result.latencies_ms.append(elapsed_ms)
        except httpx.HTTPError:
            result.errors += 1


async def run_load_test(
    base_url: str, api_key: str, concurrency: int, total: int, batch_size: int
) -> Result:
    url = f"{base_url.rstrip('/')}/v1/events"
    result = Result()
    semaphore = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=30.0) as client:
        await asyncio.gather(
            *(_send_one(client, semaphore, url, api_key, batch_size, result) for _ in range(total))
        )
    return result


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int(len(ordered) * pct), len(ordered) - 1)
    return ordered[index]


def main() -> None:
    parser = argparse.ArgumentParser(description="Load-test POST /v1/events")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--total", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=1)
    args = parser.parse_args()

    start = time.perf_counter()
    result = asyncio.run(
        run_load_test(args.url, args.api_key, args.concurrency, args.total, args.batch_size)
    )
    wall_seconds = time.perf_counter() - start

    print(f"requests: {args.total}  errors: {result.errors}  wall: {wall_seconds:.2f}s")
    print(f"throughput: {args.total / wall_seconds:.1f} req/s")
    if result.latencies_ms:
        print(f"latency p50: {_percentile(result.latencies_ms, 0.50):.1f}ms")
        print(f"latency p95: {_percentile(result.latencies_ms, 0.95):.1f}ms")
        print(f"latency p99: {_percentile(result.latencies_ms, 0.99):.1f}ms")
        print(f"latency mean: {statistics.mean(result.latencies_ms):.1f}ms")
    error_rate = result.errors / args.total if args.total else 0.0
    print(f"error rate: {error_rate:.1%}")


if __name__ == "__main__":
    main()

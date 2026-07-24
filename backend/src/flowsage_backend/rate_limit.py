"""Redis-backed rate limiting (slowapi/`limits`). One `Limiter` instance shared by
the whole app; `_rate_limit_key` picks a per-request key so the same instance can
back per-IP (auth), per-API-key (ingestion), and per-user (everything else) tiers
depending on which decorator a route uses."""

from __future__ import annotations

import inspect
import typing
from typing import Any, Callable, TypeVar

from fastapi import FastAPI, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

import jwt

F = TypeVar("F", bound=Callable[..., Any])

AUTH_RATE_LIMIT = "5/minute"
INGEST_RATE_LIMIT = "120/minute"
DEFAULT_RATE_LIMIT = "300/minute"


def _rate_limit_key(request: Request) -> str:
    api_key = request.headers.get("X-API-Key")
    if api_key is not None:
        return f"apikey:{api_key}"

    settings = request.app.state.settings
    token = request.cookies.get(settings.cookie_name)
    if token is not None:
        try:
            payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
            return f"user:{payload['sub']}"
        except jwt.PyJWTError:
            pass

    return f"ip:{get_remote_address(request)}"


def resolve_signature(func: F) -> F:
    """Fixes a real slowapi + FastAPI + `from __future__ import annotations`
    incompatibility. Every module in this codebase uses postponed (string)
    annotations, so FastAPI resolves them via the route callable's `__globals__`
    at registration time. slowapi's `@limiter.limit(...)` wrapper is defined
    inside slowapi's own module, though, so the wrapper FastAPI actually sees
    has the WRONG `__globals__` -- FastAPI ends up trying to look up e.g.
    "LoginRequest" inside slowapi's module, fails silently, and downgrades that
    Pydantic body parameter to a required query parameter instead (a 422 on
    every call). Re-resolving type hints against the original function (found
    via `__wrapped__`, which `functools.wraps` sets) and attaching them as an
    explicit `__signature__` sidesteps the broken globals lookup entirely, since
    `inspect.signature` prefers an explicit `__signature__` over introspecting
    the callable, and FastAPI only re-resolves an annotation via `__globals__`
    when it's still a string -- these no longer are.

    Apply directly beneath `@limiter.limit(...)` (i.e. the decorator closest to
    the route function, so it runs on slowapi's wrapper right after slowapi
    produces it) and above `@router.<method>(...)`."""
    original = getattr(func, "__wrapped__", func)
    hints = typing.get_type_hints(original)
    sig = inspect.signature(original)
    resolved_params = [
        param.replace(annotation=hints.get(name, param.annotation))
        for name, param in sig.parameters.items()
    ]
    func.__signature__ = sig.replace(parameters=resolved_params)  # type: ignore[attr-defined]
    return func


limiter = Limiter(key_func=_rate_limit_key, default_limits=[DEFAULT_RATE_LIMIT])


def configure_rate_limiting(app: FastAPI, redis_url: str) -> None:
    limiter._storage_uri = redis_url  # noqa: SLF001 - slowapi has no public setter;
    # setting this before the first `.limit()` call takes effect is the documented
    # way to point an already-constructed Limiter at a real backend (see slowapi's
    # own test suite for this exact pattern).
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
    # slowapi's handler is typed for its own narrower RateLimitExceeded parameter;
    # Starlette's add_exception_handler wants the wider Exception -- a known,
    # harmless variance mismatch (this is the documented slowapi/FastAPI wiring).
    app.add_middleware(SlowAPIMiddleware)

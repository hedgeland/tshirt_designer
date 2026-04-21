"""Shared transient-failure retry with exponential backoff.

Used by src/image.py (Gemini API) and src/printify.py (Printify API).
Delays: 5 s → 10 s → 20 s across three attempts.
"""

import time

# Try to import Google API core exceptions for type-safe matching;
# fall back gracefully if the package isn't installed as a direct dep.
try:
    from google.api_core.exceptions import (
        DeadlineExceeded,
        InternalServerError,
        ServiceUnavailable,
    )
    _GOOGLE_TRANSIENT: tuple = (ServiceUnavailable, DeadlineExceeded, InternalServerError)
except ImportError:
    _GOOGLE_TRANSIENT = ()

# httpx network-level errors that indicate a transient connectivity issue
try:
    import httpx as _httpx
    _HTTP_TRANSIENT: tuple = (_httpx.TimeoutException, _httpx.ConnectError)
except ImportError:
    _HTTP_TRANSIENT = ()

# Keyword fallback for wrapped errors the SDK doesn't re-type cleanly
_TRANSIENT_KEYWORDS = ("503", "unavailable", "deadline", "timeout", "500", "internal")


def _is_transient(exc: Exception) -> bool:
    """Return True if exc looks like a recoverable transient error.

    Prefers type-safe checks; falls back to string matching for SDK errors
    that arrive as generic Exception with status codes in the message.
    """
    if _GOOGLE_TRANSIENT and isinstance(exc, _GOOGLE_TRANSIENT):
        return True
    if _HTTP_TRANSIENT and isinstance(exc, _HTTP_TRANSIENT):
        return True
    msg = str(exc).lower()
    return any(k in msg for k in _TRANSIENT_KEYWORDS)


def with_retry(fn, retries: int = 3, base_delay: float = 5.0):
    """Call fn(), retrying up to `retries` times on transient errors with exponential backoff.

    Raises on the final attempt regardless of error type.
    """
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            if _is_transient(e) and attempt < retries - 1:
                time.sleep(base_delay * (2 ** attempt))
                continue
            raise

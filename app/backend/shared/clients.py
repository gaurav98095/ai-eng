"""How the workers talk to the three model services.

The services are unchanged from version 1 — same four endpoints, same payloads.
What is new is the discipline around calling them, which version 1 had none of:
a timeout on every call, retries with exponential backoff and jitter, and a
circuit breaker so a service that is genuinely down stops being hammered.

Only workers call these. The API never does, which is the rule the whole design
rests on.
"""
import logging
import random
import threading
import time
from typing import Any

import requests

from . import services
from .config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()


class ServiceError(RuntimeError):
    """A model service could not be reached, or refused the request."""


# --- circuit breaker ---------------------------------------------------------

class _Breaker:
    """Stop calling something that is clearly down.

    After enough consecutive failures the breaker opens and calls fail
    immediately instead of waiting out a timeout each time. After a cooldown one
    call is let through; if it succeeds the breaker closes.

    Without this, a dead service turns every worker into a process waiting
    fifteen minutes to learn what the first one already knew.
    """

    def __init__(self, threshold: int = 5, cooldown: float = 30.0):
        self.threshold = threshold
        self.cooldown = cooldown
        self._failures = 0
        self._opened_at = 0.0
        self._lock = threading.Lock()

    def before(self, name: str) -> None:
        with self._lock:
            if self._failures < self.threshold:
                return
            if time.time() - self._opened_at < self.cooldown:
                raise ServiceError(f"{name} is unavailable (circuit open)")
            self._failures = self.threshold - 1      # let one probe through

    def succeeded(self) -> None:
        with self._lock:
            self._failures = 0

    def failed(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures == self.threshold:
                self._opened_at = time.time()


_breakers: dict[str, _Breaker] = {}
_breakers_lock = threading.Lock()


def _breaker(name: str) -> _Breaker:
    with _breakers_lock:
        return _breakers.setdefault(name, _Breaker())


def _request(name: str, url: str, **kwargs) -> dict[str, Any]:
    breaker = _breaker(name)
    breaker.before(name)

    kwargs.setdefault("timeout", settings.service_timeout_seconds)
    last: Exception | None = None

    for attempt in range(settings.service_retries):
        try:
            response = requests.post(url, **kwargs)
            if 400 <= response.status_code < 500:
                # We sent something wrong. Retrying will not help.
                breaker.succeeded()
                raise ServiceError(f"{name} rejected the request: "
                                   f"{response.status_code} {response.text[:200]}")
            response.raise_for_status()
            breaker.succeeded()
            return response.json()
        except ServiceError:
            raise
        except Exception as exc:                       # noqa: BLE001 — retry anything else
            last = exc
            breaker.failed()
            if attempt == settings.service_retries - 1:
                break
            # Exponential backoff with jitter, so a recovering service is not
            # met by every worker retrying in lockstep.
            delay = settings.service_backoff_seconds * (2 ** attempt) * (0.5 + random.random())
            log.warning("%s failed (%s); retrying in %.1fs", name, exc, delay)
            time.sleep(delay)

    raise ServiceError(f"{name} failed after {settings.service_retries} attempts: {last}")


# --- embedding ---------------------------------------------------------------
#
# Only the query half is here. Indexing moved to the queue: it is bulk work,
# nobody is waiting on it, and the GPU pulls those jobs for itself through the
# broker. See api/routes/broker.py.
#
# This is the split worth remembering: a call is direct when a person is
# waiting for it, and queued when they are not.

def retrieve(session_id: str, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
    """Ask which chunks best match a question.

    A direct call on purpose: a person is waiting, and it takes milliseconds.
    Returns the *keys* and scores; the text comes from our own database.
    """
    body = _request("embedding", f"{services.url('embedding')}/retrieve",
                    json={"session_id": session_id, "query": query,
                          "top_k": top_k or settings.top_k})
    return body.get("hits", [])


# Speech to text is not here at all. It is the slowest thing the system does,
# and a queued job -- the GPU claims it from the broker, writes the transcript
# straight to storage, and reports back. No process holds a connection for the
# twenty minutes it takes.


# --- generation --------------------------------------------------------------

def generate(prompt: str, max_new_tokens: int | None = None,
             temperature: float | None = None) -> dict[str, Any]:
    """Answer a prompt.

    A direct call, not a queue: latency decides how this feels, and when the
    service learns to stream, a queue could not carry it.
    """
    return _request("llm", f"{services.url('llm')}/generate",
                    json={"prompt": prompt,
                          "max_new_tokens": max_new_tokens or settings.max_new_tokens,
                          "temperature": settings.temperature
                          if temperature is None else temperature})


# --- health ------------------------------------------------------------------

def health(url: str) -> dict[str, Any] | None:
    """Ask a service what it is. Returns its /health body, or None if it is down."""
    try:
        response = requests.get(f"{url.rstrip('/')}/health", timeout=10)
        return response.json() if response.ok else None
    except (requests.RequestException, ValueError):
        return None

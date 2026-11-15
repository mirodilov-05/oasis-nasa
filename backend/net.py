"""Shared HTTP layer for every external data provider.

One place decides, per provider:
  * timeouts (connect / read) and an overall time budget for one call including retries
  * retries with exponential backoff and jitter, only for errors worth retrying
    (connection failures, HTTP 429 / 5xx; read timeouts only where the policy says so),
    honouring Retry-After up to the remaining budget
  * request deduplication: identical requests in flight at the same time share one call
  * a response cache with expiry (ttl_s; 0 = no cache)
  * a circuit breaker: after N consecutive failures the provider is skipped for a while,
    so one dead service cannot slow every request down
  * health statistics for /api/data-sources (calls, failures, latency, cache and dedup hits)

Callers get a Response (status, bytes, parsed JSON, fetch time, whether it came from the
cache) or an Unavailable exception that says why. Nothing here invents data.
"""
from __future__ import annotations

import json
import random
import threading
import time
from concurrent.futures import Future
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import httpx

USER_AGENT = "terrashift (+https://github.com/mirodilov-05/oasis-nasa)"
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class Policy:
    connect_s: float = 4.0
    read_s: float = 10.0
    retries: int = 1                 # extra attempts after the first
    backoff_s: float = 0.5           # first wait; doubles each retry, +-25 % jitter
    backoff_max_s: float = 4.0
    budget_s: float = 30.0           # no new attempt starts after this much time
    retry_read_timeout: bool = False  # a slow server rarely gets faster: do not wait twice by default
    ttl_s: float = 0.0               # response cache lifetime; 0 = no cache
    cache_max: int = 256
    breaker_after: int = 3           # consecutive failures that open the breaker
    breaker_open_s: float = 120.0
    max_bytes: int = 50_000_000


# Timeouts follow each service's observed behaviour (see ARCHITECTURE.md, "Data providers").
POLICIES: dict[str, Policy] = {
    # NASA POWER daily point: ~2-4 s per quarter of the record; data change once a day
    "nasa_power": Policy(connect_s=4, read_s=8, retries=1, budget_s=20, ttl_s=6 * 3600, cache_max=64),
    # NASA GIBS WMTS health probe: one small tile, must be fast
    "nasa_gibs": Policy(connect_s=3, read_s=3.5, retries=0, budget_s=5, breaker_open_s=300),
    # NASA CMR granule search: metadata, fast but occasionally 5xx
    "earthdata_cmr": Policy(connect_s=5, read_s=30, retries=2, budget_s=60, ttl_s=3600, cache_max=512),
    # NASA Earthdata OPeNDAP / LP DAAC data: large, slow, authenticated
    "earthdata_data": Policy(connect_s=10, read_s=90, retries=1, budget_s=180, retry_read_timeout=True,
                             breaker_after=5, max_bytes=200_000_000),
    # NASA SRTM through OpenTopoData: public instance allows 1 call/s
    "srtm": Policy(connect_s=4, read_s=8, retries=1, backoff_s=1.2, budget_s=15, ttl_s=30 * 86400, cache_max=2048),
    # ISRIC SoilGrids: often ~10 s, fetched in the background
    "soilgrids": Policy(connect_s=5, read_s=25, retries=1, backoff_s=2, budget_s=60, ttl_s=30 * 86400,
                        cache_max=2048, breaker_open_s=120),
    # Open-Meteo forecast / ERA5 / ensemble: fast; forecasts refresh hourly
    "open_meteo": Policy(connect_s=3, read_s=12, retries=2, budget_s=25, ttl_s=900, cache_max=512),
    "open_meteo_ensemble": Policy(connect_s=3, read_s=20, retries=1, budget_s=30, ttl_s=1800, cache_max=256),
    # user-added JSON / tile providers
    "custom": Policy(connect_s=3, read_s=3.5, retries=0, budget_s=5, max_bytes=2_000_000, breaker_open_s=600),
}


class Unavailable(Exception):
    """The provider could not deliver. `reason` is short and safe to show."""

    def __init__(self, provider: str, reason: str, status: int | None = None):
        super().__init__(f"{provider}: {reason}")
        self.provider, self.reason, self.status = provider, reason, status


@dataclass
class Response:
    status: int
    content: bytes
    url: str
    fetched_at: str                  # when the bytes left the provider (UTC, ISO 8601)
    from_cache: bool = False
    attempts: int = 1
    elapsed_ms: int = 0
    headers: dict = field(default_factory=dict)

    def json(self):
        try:
            return json.loads(self.content)
        except ValueError as exc:
            raise Unavailable("parse", f"invalid JSON ({exc.__class__.__name__})") from exc


@dataclass
class Health:
    calls: int = 0
    ok: int = 0
    failures: int = 0
    retries: int = 0
    cache_hits: int = 0
    dedup_hits: int = 0
    breaker_skips: int = 0
    consecutive_failures: int = 0
    last_ok_at: str | None = None
    last_error_at: str | None = None
    last_error: str | None = None
    last_latency_ms: int | None = None
    breaker_open_until: float = 0.0


_lock = threading.Lock()
_health: dict[str, Health] = {}
_cache: dict[str, dict[tuple, tuple[float, Response]]] = {}
_inflight: dict[tuple, Future] = {}
_clients: dict[str, httpx.Client] = {}
_sleep = time.sleep          # replaced in tests


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def policy(provider: str) -> Policy:
    return POLICIES.get(provider, POLICIES["custom"])


def _client(provider: str) -> httpx.Client:
    with _lock:
        c = _clients.get(provider)
        if c is None:
            p = policy(provider)
            c = _clients[provider] = httpx.Client(
                timeout=httpx.Timeout(p.read_s, connect=p.connect_s), headers={"User-Agent": USER_AGENT})
        return c


def _h(provider: str) -> Health:
    h = _health.get(provider)
    if h is None:
        h = _health[provider] = Health()
    return h


def _key(method: str, url: str, params, headers) -> tuple:
    items = tuple(sorted((str(k), str(v)) for k, v in (params or {}).items())) if isinstance(params, dict) else params
    auth = tuple(sorted((headers or {}).items()))
    return (method, url, items, hash(auth))


def get(provider: str, url: str, params: dict | None = None, *, headers: dict | None = None,
        follow_redirects: bool = False, cache: bool = True, accept_status: tuple = ()) -> Response:
    """GET with the provider's policy. Raises Unavailable.

    accept_status: non-2xx statuses that are a valid answer (e.g. 302 for a signed-link lookup).
    """
    p = policy(provider)
    key = _key("GET", url, params, headers)
    now = time.monotonic()
    with _lock:
        h = _h(provider)
        h.calls += 1
        if cache and p.ttl_s > 0:
            hit = _cache.get(provider, {}).get(key)
            if hit and now - hit[0] < p.ttl_s:
                h.cache_hits += 1
                return dataclasses_replace(hit[1], from_cache=True)
        if h.breaker_open_until > now:
            h.breaker_skips += 1
            wait = int(h.breaker_open_until - now)
            raise Unavailable(provider, f"skipped after {h.consecutive_failures} failures, retry in {wait} s")
        leader = key not in _inflight
        if leader:
            fut: Future = Future()
            _inflight[key] = fut
        else:
            fut = _inflight[key]
            h.dedup_hits += 1
    if not leader:
        return fut.result()          # re-raises the leader's Unavailable
    try:
        resp = _attempt(provider, p, key if cache else None, url, params, headers, follow_redirects, accept_status)
        fut.set_result(resp)
        return resp
    except Unavailable as exc:
        fut.set_exception(exc)
        raise
    finally:
        with _lock:
            _inflight.pop(key, None)


def dataclasses_replace(resp: Response, **changes) -> Response:
    data = asdict(resp)
    data.update(changes)
    return Response(**data)


def _retryable(exc: Exception | None, status: int | None, p: Policy) -> bool:
    if status is not None:
        return status in RETRY_STATUS
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError, httpx.PoolTimeout)):
        return True
    if isinstance(exc, httpx.ReadTimeout):
        return p.retry_read_timeout
    return False


def _attempt(provider, p: Policy, cache_key, url, params, headers, follow_redirects, accept_status) -> Response:
    started = time.monotonic()
    attempt, reason, status = 0, "unknown error", None
    while True:
        attempt += 1
        t0 = time.monotonic()
        exc_seen, status = None, None
        retry_after = None
        try:
            r = _client(provider).get(url, params=params, headers=headers, follow_redirects=follow_redirects)
            status = r.status_code
            if 200 <= status < 300 or status in accept_status:
                if len(r.content) > p.max_bytes:
                    reason = "response too large"
                    break
                resp = Response(status=status, content=r.content, url=str(r.url), fetched_at=_now_iso(),
                                attempts=attempt, elapsed_ms=round((time.monotonic() - started) * 1000),
                                headers={k.lower(): v for k, v in r.headers.items()
                                         if k.lower() in ("location", "content-type", "last-modified", "date")})
                _record_ok(provider, p, cache_key, resp)
                return resp
            reason = f"HTTP {status}"
            retry_after = r.headers.get("retry-after")
        except httpx.HTTPError as exc:
            exc_seen = exc
            reason = type(exc).__name__
        if attempt > p.retries or not _retryable(exc_seen, status if exc_seen is None else None, p):
            break
        wait = min(p.backoff_max_s, p.backoff_s * 2 ** (attempt - 1)) * random.uniform(0.75, 1.25)
        if retry_after and retry_after.isdigit():
            wait = max(wait, float(retry_after))
        if time.monotonic() - started + wait + (time.monotonic() - t0) > p.budget_s:  # next try would not finish
            reason += " (time budget spent)"
            break
        with _lock:
            _h(provider).retries += 1
        _sleep(wait)
    _record_failure(provider, p, reason)
    raise Unavailable(provider, reason, status)


def _record_ok(provider: str, p: Policy, cache_key: tuple | None, resp: Response) -> None:
    with _lock:
        h = _h(provider)
        h.ok += 1
        h.consecutive_failures = 0
        h.breaker_open_until = 0.0
        h.last_ok_at = resp.fetched_at
        h.last_latency_ms = resp.elapsed_ms
        if cache_key is not None and p.ttl_s > 0:
            store = _cache.setdefault(provider, {})
            if len(store) >= p.cache_max:
                store.pop(min(store, key=lambda k: store[k][0]), None)     # drop the oldest entry
            store[cache_key] = (time.monotonic(), resp)


def _record_failure(provider: str, p: Policy, reason: str) -> None:
    with _lock:
        h = _h(provider)
        h.failures += 1
        h.consecutive_failures += 1
        h.last_error = reason
        h.last_error_at = _now_iso()
        if h.consecutive_failures >= p.breaker_after:
            h.breaker_open_until = time.monotonic() + p.breaker_open_s


def reset(provider: str | None = None) -> None:
    """Forget health, cache and breaker state (tests, or a forced health check)."""
    with _lock:
        for name in ([provider] if provider else list(_health)):
            _health.pop(name, None)
            _cache.pop(name, None)


def status() -> dict:
    """Health and policy of every provider that has been called, plus the policy of the others."""
    now = time.monotonic()
    out = {}
    with _lock:
        for name in sorted(set(POLICIES) | set(_health)):
            h = _health.get(name, Health())
            if h.ok == 0 and h.failures == 0:
                state = "unknown"          # never called, or the first calls are still in flight
            elif h.breaker_open_until > now:
                state = "down"
            elif h.consecutive_failures:
                state = "degraded"
            else:
                state = "ok"
            stats = asdict(h)
            stats.pop("breaker_open_until")
            stats["breaker_open_s_left"] = max(0, round(h.breaker_open_until - now)) if h.breaker_open_until else 0
            out[name] = {"state": state, "stats": stats, "policy": asdict(policy(name))}
    return out

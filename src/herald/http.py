"""HTTPS client with a host pause after 403, 429, or 503."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

import httpx

from herald.config import Settings
from herald.errors import HeraldError, ParseFailure, RateLimited
from herald.limiter import HostLimiter
from herald.util import hostname_of

MAX_BYTES = 2_000_000
JAVDB_FLOOR_SECONDS = 5.0
BLOCK_STATUSES = {403, 429, 503}


class HttpClient:
    def __init__(
        self,
        *,
        timeout: float,
        user_agent: str,
        limiter: HostLimiter,
        transport: httpx.BaseTransport | None = None,
        trust_env: bool = True,
    ) -> None:
        self.limiter = limiter
        self._paused: dict[str, str] = {}
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            max_redirects=5,
            trust_env=trust_env,
            transport=transport,
            headers={
                "User-Agent": user_agent,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def request(
        self,
        url: str,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.port is not None:
            raise HeraldError(f"refusing non-https URL for {parts.hostname or 'unknown host'}")
        host = parts.hostname.lower()
        if host in self._paused:
            raise RateLimited(self._paused[host])
        self.limiter.wait(host)
        try:
            response = self._client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise HeraldError(f"network error talking to {host} ({type(exc).__name__})") from exc
        if len(response.content) > MAX_BYTES:
            raise HeraldError(f"{host} response is larger than {MAX_BYTES} bytes")
        if response.status_code in BLOCK_STATUSES:
            retry = response.headers.get("retry-after", "")
            extra = f" (Retry-After: {retry})" if retry and len(retry) < 20 else ""
            self.pause_host(
                url,
                f"{host} returned HTTP {response.status_code}{extra}; "
                "this host is paused for the rest of the run, and Herald will not retry or evade it",
            )
            raise RateLimited(self._paused[host])
        return response

    def pause_host(self, url: str, reason: str) -> None:
        host = hostname_of(url)
        if host:
            self._paused[host] = reason

    def get_text(self, url: str, headers: Mapping[str, str] | None = None) -> tuple[str, str]:
        response = self.request(url, headers=headers)
        host = hostname_of(url)
        if response.status_code != 200:
            raise HeraldError(f"{host} returned HTTP {response.status_code}")
        if response.charset_encoding is None:
            response.encoding = "utf-8"
        return response.text, str(response.url)

    def get_json(self, url: str, headers: Mapping[str, str] | None = None) -> tuple[object, str]:
        response = self.request(url, headers=headers)
        host = hostname_of(url)
        if response.status_code != 200:
            raise HeraldError(f"{host} returned HTTP {response.status_code}")
        try:
            return response.json(), str(response.url)
        except ValueError as exc:
            raise ParseFailure(f"response from {host} was not JSON") from exc


def make_client(
    settings: Settings,
    *,
    transport: httpx.BaseTransport | None = None,
    trust_env: bool = True,
) -> HttpClient:
    host = hostname_of(settings.javdb_base)
    floors = {host: JAVDB_FLOOR_SECONDS, "javdb.com": JAVDB_FLOOR_SECONDS}
    limiter = HostLimiter(
        default_interval=settings.min_delay_seconds,
        global_gap=settings.min_delay_seconds,
        floors=floors,
    )
    return HttpClient(
        timeout=settings.timeout_seconds,
        user_agent=settings.user_agent,
        limiter=limiter,
        transport=transport,
        trust_env=trust_env,
    )

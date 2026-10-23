import httpx
import pytest

from herald.errors import HeraldError, RateLimited
from herald.http import HttpClient
from herald.limiter import HostLimiter
from herald.lockfile import file_lock


def _client(handler) -> HttpClient:
    return HttpClient(
        timeout=5,
        user_agent="herald-test",
        limiter=HostLimiter(0, 0, {}),
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )


def test_block_status_pauses_without_a_second_request():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return httpx.Response(429, text="slow down", headers={"retry-after": "30"})

    with _client(handler) as client:
        with pytest.raises(RateLimited, match="429"):
            client.request("https://javdb.com/actors/Ab12")
        with pytest.raises(RateLimited, match="paused"):
            client.request("https://javdb.com/actors/Cd34")
    assert calls == ["javdb.com"]


def test_refuses_plain_http():
    with _client(lambda _request: httpx.Response(200)) as client:
        with pytest.raises(HeraldError, match="non-https"):
            client.request("http://javdb.com/actors/Ab12")


def test_not_found_is_returned():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="missing")

    with _client(handler) as client:
        response = client.request("https://kemono.su/api/v1/patreon/user/1/posts")
    assert response.status_code == 404


def test_limiter_applies_javdb_floor_and_global_gap():
    clock = {"now": 0.0}
    slept = []

    def now() -> float:
        return clock["now"]

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["now"] += seconds

    limiter = HostLimiter(2.0, 2.0, {"javdb.com": 5.0}, clock=now, sleep=sleep)
    limiter.wait("javdb.com")
    limiter.wait("javdb.com")
    limiter.wait("kemono.cr")
    assert slept == [5.0, 2.0]


def test_lock_is_exclusive(isolated):
    from herald.errors import AlreadyRunning

    path = isolated / "data" / "herald.lock"
    with file_lock(path):
        with pytest.raises(AlreadyRunning):
            with file_lock(path):
                pass

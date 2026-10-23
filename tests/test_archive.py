import json
from pathlib import Path

import httpx

from herald.config import load_settings
from herald.errors import HeraldError
from herald.http import HttpClient
from herald.limiter import HostLimiter
from herald.providers.archive import ArchiveProvider
from herald.routing import KEMONO_HOSTS
from tests.conftest import subscription

FIXTURES = Path(__file__).parent / "fixtures"
POSTS = json.loads((FIXTURES / "kemono_posts.json").read_text(encoding="utf-8"))


def _client(handler) -> HttpClient:
    return HttpClient(
        timeout=5,
        user_agent="herald-test",
        limiter=HostLimiter(0, 0, {}),
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )


def test_kemono_posts_become_links_without_media():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "text/css" in request.headers["accept"]
        assert request.url.path == "/api/v1/patreon/user/42/posts"
        return httpx.Response(200, json=POSTS)

    sub = subscription()
    with _client(handler) as client:
        result = ArchiveProvider("kemono", KEMONO_HOSTS).fetch(sub, client, load_settings())
    assert [item.key for item in result.items] == ["9001", "9000"]
    assert result.items[0].title == "素描近况"
    assert result.items[0].url == "https://kemono.cr/patreon/user/42/post/9001"
    assert result.items[0].cover_url is None
    blob = " ".join(item.url + item.title for item in result.items)
    assert "DO-NOT-LEAK-BODY" not in blob
    assert "do-not-fetch.jpg" not in blob
    assert "别人的帖子" not in blob


def test_wrapped_post_list_and_legacy_path():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/posts"):
            return httpx.Response(404, text="missing")
        return httpx.Response(200, json={"posts": POSTS[:1]})

    sub = subscription()
    with _client(handler) as client:
        result = ArchiveProvider("kemono", KEMONO_HOSTS).fetch(sub, client, load_settings())
    assert calls == [
        "/api/v1/patreon/user/42/posts",
        "/api/v1/patreon/user/42",
    ]
    assert result.items[0].key == "9001"


def test_not_found_does_not_look_like_an_empty_creator():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="missing")

    sub = subscription()
    with _client(handler) as client:
        try:
            ArchiveProvider("kemono", KEMONO_HOSTS).fetch(sub, client, load_settings())
        except HeraldError as exc:
            message = str(exc)
        else:
            raise AssertionError("404 should not become an empty post list")
    assert "404" in message
    assert "marked seen" in message

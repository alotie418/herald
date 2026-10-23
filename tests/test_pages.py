from pathlib import Path

import httpx
import pytest

from herald.config import load_settings
from herald.errors import AuthRequired, ChallengeDetected
from herald.http import HttpClient
from herald.limiter import HostLimiter
from herald.providers.fanbox import FanboxProvider
from herald.providers.publicpage import PublicPageProvider
from tests.conftest import subscription

FIXTURES = Path(__file__).parent / "fixtures"


def _client(handler) -> HttpClient:
    return HttpClient(
        timeout=5,
        user_agent="herald-test",
        limiter=HostLimiter(0, 0, {}),
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )


def test_fanbox_keeps_a_cover_url_without_fetching_the_image():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        assert request.url.host == "api.fanbox.cc"
        assert request.url.params["creatorId"] == "ada"
        assert request.headers["origin"] == "https://www.fanbox.cc"
        return httpx.Response(
            200,
            json={
                "body": {
                    "creator": {"user": {"name": "Ada"}},
                    "items": [
                        {
                            "id": "10",
                            "title": "公开草稿",
                            "publishedDatetime": "2026-02-02T00:00:00Z",
                            "isRestricted": False,
                            "coverImageUrl": "https://downloads.fanbox.cc/do-not-fetch.jpg",
                        },
                        {
                            "id": "11",
                            "title": "会员稿",
                            "publishedDatetime": "2026-03-03T00:00:00Z",
                            "isRestricted": True,
                        },
                    ],
                }
            },
        )

    sub = subscription(provider="fanbox", target="ada", url="https://www.fanbox.cc/@ada")
    with _client(handler) as client:
        result = FanboxProvider().fetch(sub, client, load_settings())
    assert result.display_name == "Ada"
    assert result.items[0].url == "https://www.fanbox.cc/@ada/posts/10"
    assert result.items[0].cover_url == "https://downloads.fanbox.cc/do-not-fetch.jpg"
    assert result.items[1].cover_url is None
    assert result.items[1].title.endswith("(supporters only)")
    assert calls == ["api.fanbox.cc"]
    blob = " ".join(item.url + item.title for item in result.items)
    assert "do-not-fetch.jpg" not in blob


def test_patreon_public_posts_are_deduped():
    html = (FIXTURES / "patreon_public.html").read_text(encoding="utf-8")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    sub = subscription(provider="patreon", target="ada", url="https://www.patreon.com/ada")
    with _client(handler) as client:
        result = PublicPageProvider("patreon").fetch(sub, client, load_settings())
    assert [item.key for item in result.items] == ["55501", "55400"]
    assert result.items[0].title == "春季更新"
    assert result.items[0].url == "https://www.patreon.com/posts/55501"
    assert result.items[0].cover_url == "https://images.example.test/patreon/spring.jpg"
    assert result.display_name == "Ada Sketch"


def test_patreon_challenge_fails():
    html = (FIXTURES / "javdb_actor_challenge.html").read_text(encoding="utf-8")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    sub = subscription(provider="patreon", target="ada", url="https://www.patreon.com/ada")
    with _client(handler) as client:
        with pytest.raises(ChallengeDetected):
            PublicPageProvider("patreon").fetch(sub, client, load_settings())


def test_subscribestar_public_post():
    html = (FIXTURES / "subscribestar_public.html").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "subscribestar.adult"
        return httpx.Response(200, text=html)

    sub = subscription(
        provider="subscribestar",
        target="adult:north",
        url="https://subscribestar.adult/north",
    )
    with _client(handler) as client:
        result = PublicPageProvider("subscribestar").fetch(sub, client, load_settings())
    assert result.items[0].key == "88001"
    assert result.items[0].url == "https://subscribestar.adult/posts/88001"


def test_empty_public_shell_is_not_a_baseline():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body><div id='app'></div></body></html>")

    sub = subscription(provider="patreon", target="ada", url="https://www.patreon.com/ada")
    with _client(handler) as client:
        with pytest.raises(AuthRequired, match="no posts"):
            PublicPageProvider("patreon").fetch(sub, client, load_settings())

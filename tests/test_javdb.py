import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from herald.config import load_settings
from herald.errors import AuthRequired, ChallengeDetected, ParseFailure, RateLimited
from herald.http import HttpClient
from herald.limiter import HostLimiter
from herald.providers.javdb import (
    JavdbProvider,
    parse_actor_page,
    parse_actor_search,
    search_actors,
)
from tests.conftest import subscription

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _client(handler) -> HttpClient:
    return HttpClient(
        timeout=5,
        user_agent="herald-test",
        limiter=HostLimiter(0, 0, {}),
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )


def test_actor_page_extracts_codes_dates_and_links():
    page = parse_actor_page(_read("javdb_actor.html"), "https://javdb.com/actors/Ab12Cd")
    assert page.name == "示例演员"
    assert page.declared_count == 3
    assert [work.code for work in page.works] == ["ABCD-001", "FC2-PPV-100200", None]
    assert page.works[0].published_at == "2026-01-15"
    assert page.works[0].url == "https://javdb.com/v/alpha01"
    assert page.works[0].cover_url == "https://images.example.test/covers/alpha01.jpg"
    assert page.works[1].cover_url is None
    assert page.works[2].cover_url is None
    assert page.works[0].title.startswith("ABCD-001")
    assert "第一部示例" in page.works[0].title
    assert page.works[2].v_id == "gamma03"
    assert "没有番号的条目" in page.works[2].title


def test_empty_actor_page_is_a_real_empty_source():
    page = parse_actor_page(_read("javdb_actor_empty.html"), "https://javdb.com/actors/Empty1")
    assert page.empty is True
    assert page.works == []
    assert page.name == "空演员"


def test_nonzero_count_without_links_is_a_parse_failure():
    with pytest.raises(ParseFailure):
        parse_actor_page(_read("javdb_actor_mismatch.html"), "https://javdb.com/actors/BadPage")


def test_login_wall_fails_loudly():
    with pytest.raises(AuthRequired, match="login"):
        parse_actor_page(_read("javdb_actor_login.html"), "https://javdb.com/login")


def test_challenge_is_not_solved():
    with pytest.raises(ChallengeDetected, match="challenge"):
        parse_actor_page(_read("javdb_actor_challenge.html"), "https://javdb.com/actors/Ab12Cd")


def test_age_gate_fails_loudly():
    html = "<html><body><h1>年龄确认</h1><p>已满18岁才能继续</p></body></html>"
    with pytest.raises(AuthRequired, match="age gate"):
        parse_actor_page(html, "https://javdb.com/actors/Ab12Cd")


def test_search_keeps_censored_and_uncensored_cards():
    hits = parse_actor_search(_read("javdb_search.html"), "https://javdb.com/search?f=actor&q=x")
    assert [hit.actor_id for hit in hits] == ["Ab12Cd", "Ef34Gh"]
    assert hits[0].marker == "censored"
    assert hits[1].marker == "uncensored"
    assert hits[0].name == hits[1].name
    assert "Example Actor" in hits[0].aliases
    assert hits[0].url == "https://javdb.com/actors/Ab12Cd"


def test_fetch_uses_actor_path_without_a_query_and_can_send_a_cookie():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=_read("javdb_actor.html"))

    settings = replace(load_settings(), secrets={"JAVDB_COOKIE": "session=from-browser"})
    sub = subscription(
        kind="feed",
        provider="javdb",
        target="Ab12Cd",
        url="https://javdb.com/actors/Ab12Cd",
    )
    with _client(handler) as client:
        result = JavdbProvider().fetch(sub, client, settings)
    assert seen[0].url.path == "/actors/Ab12Cd"
    assert seen[0].url.query == b""
    assert seen[0].headers["cookie"] == "session=from-browser"
    assert [item.key for item in result.items] == ["ABCD-001", "FC2-PPV-100200", "v:gamma03"]
    assert result.items[0].cover_url == "https://images.example.test/covers/alpha01.jpg"
    assert result.items[1].cover_url is None
    assert result.display_name == "示例演员"
    blob = json.dumps([item.__dict__ for item in result.items], ensure_ascii=False)
    assert "/data/" not in blob


def test_challenge_pauses_later_actor_pages():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, text=_read("javdb_actor_challenge.html"))

    settings = load_settings()
    provider = JavdbProvider()
    with _client(handler) as client:
        first = subscription(kind="feed", provider="javdb", target="Ab12Cd", url="https://javdb.com/actors/Ab12Cd")
        second = subscription(kind="feed", provider="javdb", target="Ef34Gh", url="https://javdb.com/actors/Ef34Gh", id=2)
        with pytest.raises(ChallengeDetected):
            provider.fetch(first, client, settings)
        with pytest.raises(RateLimited, match="paused|challenge"):
            provider.fetch(second, client, settings)
    assert calls == ["/actors/Ab12Cd"]


def test_search_request_is_the_actor_filter():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["f"] == "actor"
        assert request.url.params["q"] == "示例演员"
        return httpx.Response(200, text=_read("javdb_search.html"))

    with _client(handler) as client:
        hits = search_actors(client, load_settings(), "示例演员")
    assert len(hits) == 2

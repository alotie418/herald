import pytest

from herald.errors import ConfigError
from herald.routing import parse_actor_ref, parse_follow_url


@pytest.mark.parametrize(
    ("raw", "provider", "target", "url"),
    [
        (
            "https://kemono.cr/patreon/user/42/post/9",
            "kemono",
            "patreon:42@kemono.cr",
            "https://kemono.cr/patreon/user/42",
        ),
        (
            "https://www.kemono.su/fanbox/user/77",
            "kemono",
            "fanbox:77@kemono.su",
            "https://kemono.su/fanbox/user/77",
        ),
        (
            "https://coomer.st/onlyfans/user/ada",
            "coomer",
            "onlyfans:ada@coomer.st",
            "https://coomer.st/onlyfans/user/ada",
        ),
        (
            "https://ada.fanbox.cc/posts/1",
            "fanbox",
            "ada",
            "https://www.fanbox.cc/@ada",
        ),
        (
            "https://www.fanbox.cc/@Ada_1",
            "fanbox",
            "Ada_1",
            "https://www.fanbox.cc/@Ada_1",
        ),
        (
            "https://www.patreon.com/Ada",
            "patreon",
            "ada",
            "https://www.patreon.com/ada",
        ),
        (
            "https://www.patreon.com/cw/Ada",
            "patreon",
            "cw:ada",
            "https://www.patreon.com/cw/ada",
        ),
        (
            "https://subscribestar.adult/North",
            "subscribestar",
            "adult:north",
            "https://subscribestar.adult/north",
        ),
        (
            "https://simpcity.su/threads/example.1/",
            "simpcity",
            "https://simpcity.su/threads/example.1/",
            "https://simpcity.su/threads/example.1/",
        ),
        (
            "https://rule34.xxx/index.php?page=account&s=profile&id=9",
            "rule34",
            "https://rule34.xxx/index.php?page=account&s=profile&id=9",
            "https://rule34.xxx/index.php?page=account&s=profile&id=9",
        ),
        (
            "https://f95zone.to/threads/game.123/",
            "f95",
            "https://f95zone.to/threads/game.123/",
            "https://f95zone.to/threads/game.123/",
        ),
    ],
)
def test_follow_urls(raw, provider, target, url):
    parsed = parse_follow_url(raw)
    assert parsed.provider == provider
    assert parsed.target == target
    assert parsed.url == url


def test_rejects_secrets_ports_and_unknown_hosts():
    with pytest.raises(ConfigError):
        parse_follow_url("https://user:secret@kemono.cr/patreon/user/1")
    with pytest.raises(ConfigError):
        parse_follow_url("https://kemono.cr:8443/patreon/user/1")
    with pytest.raises(ConfigError):
        parse_follow_url("http://kemono.cr/patreon/user/1")
    with pytest.raises(ConfigError, match="unsupported"):
        parse_follow_url("https://example.com/patreon/user/1")


def test_javdb_follow_url_points_at_feed():
    with pytest.raises(ConfigError, match="feed"):
        parse_follow_url("https://javdb.com/actors/Ab12Cd")


def test_actor_ref_uses_configured_base():
    actor_id, url = parse_actor_ref("https://javdb521.com/actors/Ab12Cd?sort=1", "https://javdb.com")
    assert actor_id == "Ab12Cd"
    assert url == "https://javdb.com/actors/Ab12Cd"
    assert parse_actor_ref("Ab12Cd", "https://javdb.com")[1].endswith("/actors/Ab12Cd")


def test_patreon_post_url_is_rejected():
    with pytest.raises(ConfigError, match="profile"):
        parse_follow_url("https://www.patreon.com/posts/spring-555")

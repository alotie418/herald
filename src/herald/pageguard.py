"""Detect login walls, age gates, and bot challenges in HTML. Never solves them.

``/cdn-cgi/challenge-platform`` is not, by itself, a bot wall. Cloudflare injects
``.../scripts/jsd/main.js`` (JavaScript Detection) on ordinary pages, including
JavDB pages that only show the over-18 modal. That asset, including the same
file under a ``/h/<id>/`` prefix, is ignored. Any other URL under the prefix
(orchestrate, turnstile, flow, and the like) is still a challenge. Captcha
widgets and interstitial copy are still walls. Herald does not solve them.
"""

from __future__ import annotations

CHALLENGE_MARKERS = (
    "cf-challenge",
    "cf_chl",
    "/cdn-cgi/challenge-platform",
    "just a moment",
    "attention required",
    "hcaptcha",
    "g-recaptcha",
    "recaptcha/api",
    "基于你的异常行为",
    "基於你的異常行為",
    "人机验证",
    "人機驗證",
)

_PLATFORM_PREFIX = "/cdn-cgi/challenge-platform"
# Characters that end a URL token in HTML or in an inline script.
_URL_END = frozenset(" \t\r\n\"'<>?#&),;`")

LOGIN_WORDS = ("登入", "登录", "登錄", "sign in", "log in")
AGE_MARKERS = (
    "年龄确认",
    "年齡確認",
    "已满18",
    "已滿18",
    "年满18",
    "年滿18",
    "age verification",
    "i am over 18",
    "over 18",
)


def looks_like_challenge(html: str, final_url: str = "") -> bool:
    sample = f"{final_url}\n{html}".lower()
    for marker in CHALLENGE_MARKERS:
        if marker == _PLATFORM_PREFIX:
            if _has_blocking_challenge_platform(sample):
                return True
            continue
        if marker.lower() in sample:
            return True
    return False


def looks_like_login(html: str, final_url: str = "") -> bool:
    lowered_url = final_url.lower()
    if any(piece in lowered_url for piece in ("/login", "/signin", "/sign-in")):
        return True
    lowered = html.lower()
    has_password = 'type="password"' in lowered or "type='password'" in lowered
    mentions_login = any(word.lower() in lowered for word in LOGIN_WORDS)
    return has_password and mentions_login


def looks_like_age_gate(html: str) -> bool:
    if looks_like_challenge(html):
        return False
    lowered = html.lower()
    return any(marker.lower() in lowered for marker in AGE_MARKERS)


def _has_blocking_challenge_platform(sample: str) -> bool:
    start = 0
    while True:
        index = sample.find(_PLATFORM_PREFIX, start)
        if index < 0:
            return False
        tail = sample[index + len(_PLATFORM_PREFIX) :]
        if not _is_jsd_asset(tail):
            return True
        start = index + len(_PLATFORM_PREFIX)


def _is_jsd_asset(tail: str) -> bool:
    """True when ``tail`` is only Cloudflare's benign ``scripts/jsd`` asset.

    Accepted shapes, up to the end of the URL token:

    - ``/scripts/jsd``
    - ``/scripts/jsd/main.js`` (one filename)
    - ``/h/<id>/scripts/jsd/main.js``
    """
    rest = tail
    if rest.startswith("/h/"):
        cursor = 3
        while cursor < len(rest) and _is_host_id_char(rest[cursor]):
            cursor += 1
        if cursor == 3 or cursor >= len(rest) or rest[cursor] != "/":
            return False
        rest = rest[cursor:]
    asset = "/scripts/jsd"
    if not rest.startswith(asset):
        return False
    rest = rest[len(asset) :]
    if rest.startswith("/"):
        rest = rest[1:]
        if not rest or rest[0] in _URL_END:
            return True
        cursor = 0
        while cursor < len(rest) and _is_asset_char(rest[cursor]):
            cursor += 1
        if cursor == 0:
            return False
        rest = rest[cursor:]
    if rest.startswith("/"):
        return False
    return not rest or rest[0] in _URL_END


def _is_host_id_char(char: str) -> bool:
    return ("a" <= char <= "z") or ("0" <= char <= "9")


def _is_asset_char(char: str) -> bool:
    return _is_host_id_char(char) or char in "._-"

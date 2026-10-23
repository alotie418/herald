"""Detect login walls and bot challenges in HTML. Never solves them."""

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

LOGIN_WORDS = ("登入", "登录", "登錄", "sign in", "log in")
AGE_MARKERS = (
    "年龄确认",
    "年齡確認",
    "已满18",
    "已滿18",
    "age verification",
    "i am over 18",
    "over 18",
)


def looks_like_challenge(html: str, final_url: str = "") -> bool:
    sample = f"{final_url}\n{html}".lower()
    return any(marker.lower() in sample for marker in CHALLENGE_MARKERS)


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

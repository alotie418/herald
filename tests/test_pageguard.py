import pytest

from herald.pageguard import looks_like_age_gate, looks_like_challenge

# Cloudflare injects this on ordinary HTML. It is not an interstitial.
JSD_PATHS = (
    "/cdn-cgi/challenge-platform/scripts/jsd/main.js",
    "/cdn-cgi/challenge-platform/h/g/scripts/jsd/main.js",
    "/cdn-cgi/challenge-platform/h/b/scripts/jsd/main.js?ray=abc",
    "https://javdb.com/cdn-cgi/challenge-platform/scripts/jsd/main.js",
)

REAL_SNIPPETS = (
    "/cdn-cgi/challenge-platform",
    "/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1",
    "/cdn-cgi/challenge-platform/h/b/turnstile/f/ov2/av0/rch/abc",
    "/cdn-cgi/challenge-platform/h/g/flow/ov1/123",
    "/cdn-cgi/challenge-platform/scripts/jsd/main.js/orchestrate/chl_page/v1",
    '<div class="cf-challenge">checking your browser</div>',
    "<script>cf_chl_opt</script>",
    "<title>Just a moment...</title>",
    "<title>Attention Required! | Cloudflare</title>",
    '<div class="hcaptcha"></div>',
    '<div class="g-recaptcha"></div>',
    '<script src="https://www.google.com/recaptcha/api.js"></script>',
    "<p>请完成人机验证</p>",
    "<p>人機驗證</p>",
    "<p>基于你的异常行为</p>",
    "<p>基於你的異常行為</p>",
)


@pytest.mark.parametrize("path", JSD_PATHS)
def test_jsd_script_path_is_not_a_challenge(path: str):
    html = f'<html><head><script src="{path}"></script></head><body><p>普通页面</p></body></html>'
    assert looks_like_challenge(html) is False


def test_inline_jsd_loader_is_not_a_challenge():
    html = (
        "<script>(function(){var a=document.createElement('script');"
        "a.src='/cdn-cgi/challenge-platform/scripts/jsd/main.js';"
        "document.head.appendChild(a);})();</script><p>已打开的演员页</p>"
    )
    assert looks_like_challenge(html) is False
    assert looks_like_challenge(
        "<html><body>ok</body></html>",
        "https://javdb.com/cdn-cgi/challenge-platform/scripts/jsd/main.js",
    ) is False


@pytest.mark.parametrize("snippet", REAL_SNIPPETS)
def test_real_challenge_markers_still_match(snippet: str):
    html = f"<html><body>{snippet}</body></html>"
    assert looks_like_challenge(html) is True


def test_jsd_script_does_not_hide_a_real_challenge_on_the_same_page():
    html = """
    <script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>
    <script src="/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1"></script>
    """
    assert looks_like_challenge(html) is True


def test_challenge_platform_url_in_the_final_location_still_counts():
    assert looks_like_challenge(
        "<html><body>ok</body></html>",
        "https://javdb.com/cdn-cgi/challenge-platform/h/g/flow/ov1/abc",
    ) is True


def test_over18_modal_with_only_the_jsd_script_is_an_age_gate():
    html = """
    <html><body>
      <script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>
      <div class="modal is-active">
        <p>年齡確認</p>
        <p>你是否已滿18歲？</p>
        <button>我已年滿18歲</button>
      </div>
    </body></html>
    """
    assert looks_like_challenge(html) is False
    assert looks_like_age_gate(html) is True


def test_button_only_over18_modal_is_an_age_gate():
    html = (
        '<script src="/cdn-cgi/challenge-platform/h/g/scripts/jsd/main.js"></script>'
        "<button>我已年滿18歲</button>"
    )
    assert looks_like_challenge(html) is False
    assert looks_like_age_gate(html) is True


def test_real_challenge_is_not_relabeled_as_an_age_gate():
    html = """
    <title>Just a moment...</title>
    <div class="cf-challenge"></div>
    <script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>
    <p>已滿18</p>
    """
    assert looks_like_challenge(html) is True
    assert looks_like_age_gate(html) is False

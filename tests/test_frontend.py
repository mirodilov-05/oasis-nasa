"""Static site checks: the intro is wired, skippable and served (no browser needed)."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import app

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
client = TestClient(app)


def test_intro_assets_are_served():
    for path, kind in (("/intro.js", "javascript"), ("/intro.css", "css"), ("/vendor/three.module.min.js", "javascript")):
        r = client.get(path)
        assert r.status_code == 200, path
        assert kind in r.headers["content-type"], path


def test_index_wires_a_skippable_intro():
    html = client.get("/").text
    assert 'id="intro"' in html and 'role="dialog"' in html
    assert 'id="intro-skip"' in html and 'id="intro-enter"' in html
    assert re.search(r'<script type="module" src="/intro\.js\?v=\d+"></script>', html)
    # the controller that can always dismiss the intro is inline, not in the module
    assert "ctl.finish" in html and "Escape" in html and "setTimeout" in html
    assert 'href="/?intro=1"' in html  # replay link


def test_intro_has_no_video_and_honours_reduced_motion():
    js = (FRONTEND / "intro.js").read_text(encoding="utf-8")
    assert "<video" not in js and ".mp4" not in js and ".webm" not in js
    assert "prefers-reduced-motion" in js
    assert "visibilityState" in js or "document.hidden" in js
    for lang in ("en", "uz", "ru"):
        assert re.search(rf"\n  {lang}: {{", js), lang


def test_every_page_links_exist():
    for page in FRONTEND.glob("*.html"):
        for href in re.findall(r'href="(/[^"#?]*)', page.read_text(encoding="utf-8")):
            target = "index.html" if href == "/" else href.lstrip("/")
            assert (FRONTEND / target).exists(), f"{page.name} -> {href}"


def test_i18n_keys_present_in_both_languages():
    i18n = (FRONTEND / "i18n.js").read_text(encoding="utf-8")
    uz, en = i18n.split("\n  en: {", 1)
    keys = lambda block: set(re.findall(r"^\s{4}([a-z0-9_]+):", block, re.M))
    assert keys(uz) == keys(en), keys(uz) ^ keys(en)

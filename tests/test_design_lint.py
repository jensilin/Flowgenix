"""Guard the visual language against drifting back to generic AI styling.

The rules live in `.cursor/rules/flowgenix-design.mdc`; this test enforces the
parts a machine can check.
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "ui" / "static"
CSS = (STATIC / "styles.css").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
FAVICON = (STATIC / "favicon.svg").read_text(encoding="utf-8")

BANNED_CSS = ["radial-gradient", "linear-gradient", "999px", "box-shadow", "backdrop-filter"]
BANNED_FONTS = ["Outfit", "Inter", "Space Grotesk", "Poppins"]


def _css_without_comments() -> str:
    return re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)


def test_stylesheet_has_no_gradients_glows_or_pills():
    css = _css_without_comments()
    found = [token for token in BANNED_CSS if token in css]
    assert not found, f"banned styling in styles.css: {found}"


def test_no_generic_ai_fonts():
    css = _css_without_comments()
    for font in BANNED_FONTS:
        assert not re.search(rf"\b{font}\b", css), f"{font} in styles.css"
        assert f"family={font.replace(' ', '+')}" not in HTML, f"{font} loaded in index.html"
    assert "IBM+Plex+Sans" in HTML and "IBM+Plex+Mono" in HTML


def test_radii_stay_small():
    css = _css_without_comments()
    for value in re.findall(r"border-radius:\s*([^;]+);", css):
        if value.strip().startswith("var("):
            continue
        px = [int(n) for n in re.findall(r"(\d+)px", value)]
        assert all(n <= 4 for n in px), f"border-radius {value!r} is larger than 4px"


def test_single_accent_colour():
    light = re.search(r":root\s*\{(.*?)\}", CSS, flags=re.S).group(1)
    accents = re.findall(r"--accent:\s*(#[0-9a-fA-F]{6})", light)
    assert accents == ["#c8501e"]


def test_favicon_is_flat():
    assert "Gradient" not in FAVICON
    assert "filter" not in FAVICON

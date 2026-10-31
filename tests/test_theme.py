"""Tests for the design tokens, the style sheet and the shared painting helpers."""
import re

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QLabel

from homebound.theme import painting
from homebound.theme.painting import (
    CLASSES, EFFECTS, SITE_TOKENS, MARKER_POLYGONS, class_badge_width, draw_character_marker, draw_chip,
    draw_class_badge, draw_dot_grid, draw_effect, draw_halo_text, draw_line_grid, draw_owner,
    draw_recent_kill, draw_site, draw_static, draw_zkill, edge_pens, effect_pixmap, halo_path, opacity,
    owner_width, site_pixmap, stale_brush, zkill_level,
)
from homebound.theme.qss import apply_theme, build_palette, build_qss, repolish
from homebound.theme.tokens import HEX_COLORS, OPACITY, PIXELS, TYPE, color, font, hex_to_rgba, qss_color


@pytest.fixture
def canvas(qt_application):
    """A painter on a 200x100 pixmap; yields (painter, pixmap) and ends the painter."""
    pixmap = QPixmap(200, 100)
    pixmap.fill(QColor("black"))
    painter = QPainter(pixmap)
    yield painter, pixmap
    painter.end()


def painted(pixmap: QPixmap) -> bool:
    """Whether anything other than the black background was drawn."""
    image = pixmap.toImage()
    return any(image.pixelColor(x, y) != QColor("black") for x in range(image.width()) for y in range(image.height()))


# ---------------------------------------------------------------- tokens

def test_alpha_is_last_in_tokens() -> None:
    # accent-dim is '#7cc4ff29' (CSS order): alpha 0x29, colour 7c c4 ff
    """Alpha is last in tokens."""
    assert hex_to_rgba(HEX_COLORS["accent-dim"]) == (0x7C, 0xC4, 0xFF, 0x29)
    assert qss_color("accent-dim") == "rgba(124,196,255,41)"
    assert qss_color("bg-0") == "#0a0d10"


def test_hex_to_rgba_without_alpha_is_opaque() -> None:
    """Hex to rgba without alpha is opaque."""
    assert hex_to_rgba("#102030") == (0x10, 0x20, 0x30, 255)


@pytest.mark.parametrize("text", ["#12345", "#1234567", "", "#"])
def test_hex_to_rgba_rejects_wrong_length(text) -> None:
    """Hex to rgba rejects wrong length."""
    with pytest.raises(ValueError):
        hex_to_rgba(text)


def test_hex_to_rgba_rejects_non_hex_and_non_text() -> None:
    """Hex to rgba rejects non hex and non text."""
    with pytest.raises(ValueError):
        hex_to_rgba("#gggggg")
    with pytest.raises(TypeError):
        hex_to_rgba(123)


def test_color_and_unknown_token(qt_application) -> None:
    """Color and unknown token."""
    assert color("bg-0") == QColor("#0a0d10")
    assert color("accent-dim").alpha() == 0x29
    with pytest.raises(KeyError):
        color("no-such-token")
    with pytest.raises(KeyError):
        qss_color("no-such-token")


def test_font_uses_token_and_pixel_override(qt_application) -> None:
    """Font uses token and pixel override."""
    spec = TYPE["ui-body"]
    assert font("ui-body").pixelSize() == spec["px"]
    assert font("ui-body", 20).pixelSize() == 20
    assert font("ui-body").weight() == QFont.Weight(spec["weight"])


def test_font_mono_hint_and_letter_spacing(qt_application) -> None:
    """Font mono hint and letter spacing."""
    mono = next(name for name, spec in TYPE.items() if spec["family"] == "mono")
    assert font(mono).styleHint() == QFont.StyleHint.Monospace
    spaced = next((name for name, spec in TYPE.items() if spec["spacing_em"]), None)
    if spaced:
        assert font(spaced, 10).letterSpacing() == pytest.approx(TYPE[spaced]["spacing_em"] * 10, abs=0.05)
    unspaced = next(name for name, spec in TYPE.items() if not spec["spacing_em"])
    assert font(unspaced).letterSpacing() == 0


def test_font_validation(qt_application) -> None:
    """Font validation."""
    with pytest.raises(ValueError):
        font("ui-body", 0)
    with pytest.raises(TypeError):
        font("ui-body", "12")
    with pytest.raises(KeyError):
        font("no-such-style")


def test_type_and_sizes() -> None:
    """Type and sizes."""
    assert TYPE["overlay-row"]["px"] >= 11 and TYPE["overlay-mono"]["px"] >= 11
    assert PIXELS["size-node-w"] == 168 and PIXELS["size-overlay-w"] == 320
    assert 0 < OPACITY[next(iter(OPACITY))] <= 1


# ---------------------------------------------------------------- qss

def test_qss_fully_resolved() -> None:
    """Qss fully resolved."""
    qss = build_qss()
    assert not re.search(r"\{[A-Za-z0-9_.-]+\}", qss)


def test_qss_icon_paths_exist() -> None:
    """Qss icon paths exist."""
    paths = re.findall(r"url\(([^)]+)\)", build_qss())
    assert paths
    from pathlib import Path
    assert all(Path(path).exists() for path in paths)


def test_build_palette_uses_tokens(qt_application) -> None:
    """Build palette uses tokens."""
    from PySide6.QtGui import QPalette
    palette = build_palette()
    assert palette.color(QPalette.ColorRole.Window) == color("bg-1")
    assert palette.color(QPalette.ColorRole.Highlight) == color("accent")
    assert palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text) == color("ink-disabled")


def test_apply_theme(qt_application) -> None:
    """Apply theme."""
    assert apply_theme(qt_application) is qt_application
    assert qt_application.styleSheet() == build_qss()
    assert qt_application.font().pixelSize() == TYPE["ui-body"]["px"]
    with pytest.raises(TypeError):
        apply_theme("not an application")
    assert isinstance(qt_application, QApplication)


def test_repolish(qt_application) -> None:
    """Repolish."""
    label = QLabel("x")
    assert repolish(label) is label
    with pytest.raises(TypeError):
        repolish("not a widget")


# ---------------------------------------------------------------- sites and effects

def test_site_icons_load_and_tint(qt_application) -> None:
    """Site icons load and tint."""
    for group, token in SITE_TOKENS.items():
        img = site_pixmap(group, 16, 1.0).toImage()
        lit = [img.pixelColor(x, y) for x in range(16) for y in range(16) if img.pixelColor(x, y).alpha() > 128]
        assert lit, group
        tint = color(token)
        # premultiplied alpha rounds by a step or two when read back
        assert all(abs(p.red() - tint.red()) <= 2 and abs(p.green() - tint.green()) <= 2
                   and abs(p.blue() - tint.blue()) <= 2 for p in lit), group
    assert site_pixmap(None).toImage() == site_pixmap("unknown").toImage()
    assert site_pixmap("no-such-group").toImage() == site_pixmap("unknown").toImage()


def test_site_pixmap_scaling_and_validation(qt_application) -> None:
    """Site pixmap scaling and validation."""
    assert site_pixmap("gas", 24, 1.0).width() == 24       # smooth scaling path
    assert site_pixmap("gas", 16, 2.0).width() == 32
    assert site_pixmap("gas", 16, 2.0).devicePixelRatio() == 2.0
    with pytest.raises(ValueError):
        site_pixmap("gas", 0)


@pytest.mark.parametrize("effect", EFFECTS)
def test_effect_pixmap_is_tinted(qt_application, effect) -> None:
    """Effect pixmap is tinted."""
    pixmap = effect_pixmap(effect, 14, 1.0)
    assert pixmap.width() == 14
    tint = color(f"effect-{effect}")
    image = pixmap.toImage()
    lit = [image.pixelColor(x, y) for x in range(14) for y in range(14) if image.pixelColor(x, y).alpha() > 200]
    assert lit
    assert all(abs(p.red() - tint.red()) <= 3 for p in lit)


def test_effect_pixmap_validation(qt_application) -> None:
    """Effect pixmap validation."""
    with pytest.raises(ValueError):
        effect_pixmap("supernova")
    with pytest.raises(ValueError):
        effect_pixmap("pulsar", 0)


def test_draw_effect_and_site(canvas) -> None:
    """Draw effect and site."""
    painter, pixmap = canvas
    assert draw_effect(painter, 0, 0, None) == 0.0
    assert draw_effect(painter, 0, 0, "") == 0.0
    assert not painted(pixmap)
    assert draw_effect(painter, 0, 0, "pulsar", 14) == 14.0
    assert draw_site(painter, 30, 0, "combat", 16) == 16.0
    assert draw_site(painter, 60, 0, None) == 16.0
    painter.end()
    assert painted(pixmap)
    painter.begin(pixmap)  # the fixture ends the painter again


# ---------------------------------------------------------------- badges

@pytest.mark.parametrize("label", sorted(CLASSES))
def test_draw_class_badge_every_class(canvas, label) -> None:
    """Draw class badge every class."""
    painter, pixmap = canvas
    width = draw_class_badge(painter, 5, 5, label, 0.7 if label in ("HS", "LS", "NS") else None)
    assert width == class_badge_width(label, 0.7 if label in ("HS", "LS", "NS") else None) >= 26
    painter.end()
    assert painted(pixmap)
    painter.begin(pixmap)


def test_k_space_badge_is_wider_with_security(qt_application) -> None:
    """K space badge is wider with security."""
    assert class_badge_width("HS", 0.9) > class_badge_width("HS")
    assert class_badge_width("C5", 0.9) == class_badge_width("C5")  # w-space shows no security


def test_unknown_class_badge_draws_nothing(canvas) -> None:
    """Unknown class badge draws nothing."""
    painter, pixmap = canvas
    assert draw_class_badge(painter, 0, 0, "XX") == 0.0
    painter.end()
    assert not painted(pixmap)
    painter.begin(pixmap)


def test_class_badge_validation(qt_application) -> None:
    """Class badge validation."""
    with pytest.raises(TypeError):
        class_badge_width(5)


def test_draw_static(canvas) -> None:
    """Draw static."""
    painter, pixmap = canvas
    known = draw_static(painter, 0, 0, "C5")
    unknown = draw_static(painter, 50, 0, "??")
    assert known > 0 and unknown > 0
    painter.end()
    assert painted(pixmap)
    painter.begin(pixmap)
    with pytest.raises(TypeError):
        draw_static(painter, 0, 0, 5)


# ---------------------------------------------------------------- characters, zKill, owners

@pytest.mark.parametrize("slot", range(1, 8))
def test_character_markers(canvas, slot) -> None:
    """Character markers."""
    painter, pixmap = canvas
    assert draw_character_marker(painter, 10, 10, slot) == 14
    assert draw_character_marker(painter, 40, 10, slot, 28) == 28
    painter.end()
    assert painted(pixmap)
    painter.begin(pixmap)


def test_character_marker_shapes_wrap_after_six(qt_application) -> None:
    """Character marker shapes wrap after six."""
    assert painting._character_marker_path(7) == painting._character_marker_path(1)
    assert set(MARKER_POLYGONS) == {2, 3, 4, 5}


def test_character_marker_validation(canvas) -> None:
    """Character marker validation."""
    painter, _ = canvas
    with pytest.raises(ValueError):
        draw_character_marker(painter, 0, 0, 0)
    with pytest.raises(TypeError):
        draw_character_marker(painter, 0, 0, "1")
    with pytest.raises(ValueError):
        draw_character_marker(painter, 0, 0, 1, 0)


@pytest.mark.parametrize(("kills_1h", "kills_24h", "level"), [
    (None, None, "none"), (0, 0, "none"), (1, 0, "low"), (0, 4, "low"), (0, 5, "med"), (2, 19, "med"),
    (0, 20, "high"), (3, 0, "high"), (3, None, "high"),
])
def test_zkill_level_thresholds(kills_1h, kills_24h, level) -> None:
    """Zkill level thresholds."""
    assert zkill_level(kills_1h, kills_24h) == level


def test_zkill_level_validation() -> None:
    """Zkill level validation."""
    with pytest.raises(ValueError):
        zkill_level(-1, 0)
    with pytest.raises(TypeError):
        zkill_level(0, "5")


def test_draw_zkill_and_recent(canvas) -> None:
    """Draw zkill and recent."""
    painter, pixmap = canvas
    plain = draw_zkill(painter, 0, 0, "med")
    with_recent = draw_zkill(painter, 40, 0, "high", recent=True)
    unknown_level = draw_zkill(painter, 100, 0, "bogus")
    assert plain == 13.5 and with_recent == plain + 16 and unknown_level == 13.5
    assert draw_recent_kill(painter, 150, 20) == 13.0
    painter.end()
    assert painted(pixmap)
    painter.begin(pixmap)


@pytest.mark.parametrize("home", [True, False])
def test_draw_owner(canvas, home) -> None:
    """Draw owner."""
    painter, pixmap = canvas
    width = draw_owner(painter, 5, 5, "ABC", 12, home=home)   # slot 12 wraps the ten owner colours
    assert width == owner_width("ABC")
    assert owner_width("ABCDE") > owner_width("A")
    painter.end()
    assert painted(pixmap)
    painter.begin(pixmap)


def test_owner_validation(canvas) -> None:
    """Owner validation."""
    painter, _ = canvas
    with pytest.raises(TypeError):
        owner_width(1)
    with pytest.raises(ValueError):
        draw_owner(painter, 0, 0, "ABC", 0)


def test_draw_chip(canvas) -> None:
    """Draw chip."""
    painter, pixmap = canvas
    plain = draw_chip(painter, 5, 5, "R", color("edge-reduced"))
    inked = draw_chip(painter, 40, 5, "CRIT", color("edge-critical"), ink=QColor("white"))
    assert 0 < plain < inked
    painter.end()
    assert painted(pixmap)
    painter.begin(pixmap)
    with pytest.raises(TypeError):
        draw_chip(painter, 0, 0, 5, QColor("red"))
    with pytest.raises(TypeError):
        draw_chip(painter, 0, 0, "R", "red")


# ---------------------------------------------------------------- edges, text, grids

def test_edge_pens(qt_application) -> None:
    """Edge pens."""
    pens = edge_pens()
    assert set(pens) == {"fresh", "reduced", "critical", "unknown", "frigate_only", "lifetime_4h",
                         "lifetime_1h", "selected"}
    width = PIXELS["edge-w-critical"]
    assert pens["critical"].dashPattern() == pytest.approx([8 / width, 4 / width])
    assert pens["unknown"].dashPattern() == pytest.approx([3 / PIXELS["edge-w-fresh"], 5 / PIXELS["edge-w-fresh"]])
    eol = PIXELS["edge-w-eol"]
    assert pens["lifetime_4h"].dashPattern() == pytest.approx([0.01, 9 / eol])
    assert pens["lifetime_1h"].dashPattern() == pytest.approx([0.01, 5 / eol])
    assert pens["selected"].color().alphaF() == pytest.approx(.35, abs=.01)
    assert pens["fresh"].widthF() == PIXELS["edge-w-fresh"]


def test_halo_text(canvas) -> None:
    """Halo text."""
    painter, pixmap = canvas
    text_font = font("ui-body")
    path = halo_path(10, 40, "Hello", text_font)
    assert not path.isEmpty()
    width = draw_halo_text(painter, 10, 40, "Hello", text_font, QColor("white"))
    assert width == QFontMetricsF(text_font).horizontalAdvance("Hello")
    painter.end()
    assert painted(pixmap)
    painter.begin(pixmap)
    with pytest.raises(TypeError):
        halo_path(0, 0, 5, text_font)


def test_stale_brush(qt_application) -> None:
    """Stale brush."""
    brush = stale_brush()
    assert isinstance(brush, QBrush)
    assert brush.color() == color("line-1")


def test_draw_dot_grid(canvas) -> None:
    """Draw dot grid."""
    painter, pixmap = canvas
    rect = QRectF(0, 0, 100, 50)
    assert draw_dot_grid(painter, rect, 10) == 50
    assert draw_dot_grid(painter, QRectF(-5, -5, 20, 20), 10) == 9   # grid snaps to multiples of the step
    default_step = PIXELS["sp-24"]
    assert draw_dot_grid(painter, rect) == len(range(0, 100, int(default_step))) * len(range(0, 50, int(default_step)))
    with pytest.raises(ValueError):
        draw_dot_grid(painter, rect, 0.5)


def test_draw_line_grid_counts(canvas) -> None:
    """Draw line grid counts."""
    painter, _ = canvas
    rect = QRectF(0, 0, 60, 30)
    # columns 0..6 and rows 0..3 at step 10: every third line is major
    assert draw_line_grid(painter, rect, 10, 3, minor=True) == 7 + 4
    assert draw_line_grid(painter, rect, 10, 3, minor=False) == 3 + 2
    with pytest.raises(ValueError):
        draw_line_grid(painter, rect, 0.1)
    with pytest.raises(ValueError):
        draw_line_grid(painter, rect, 10, 0)


def test_opacity_token() -> None:
    """Opacity token."""
    name = next(iter(OPACITY))
    assert opacity(name) == OPACITY[name]
    with pytest.raises(KeyError):
        opacity("no-such-token")



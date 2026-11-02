"""Tests for the design tokens, the style sheet and the shared painting helpers."""
import re

import pytest
from collections.abc import Iterator

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QFontMetricsF, QImage, QPainter, QPainterPath,
                           QPixmap)
from PySide6.QtWidgets import QApplication, QLabel

from homebound.mapdb.store import LIFETIME_1H, LIFETIME_4H, LIFETIME_EXPIRED, LIFETIME_STABLE
from homebound.theme import painting
from homebound.theme.painting import (
    CLASSES, EFFECTS, SITE_TOKENS, MARKER_POLYGONS, class_badge_width, draw_character_marker, draw_chip,
    draw_class_badge, draw_dot_grid, draw_effect, draw_halo_text, draw_line_grid, draw_owner,
    draw_recent_kill, draw_site, draw_static, draw_zkill, edge_pens, effect_pixmap, halo_path, opacity,
    owner_width, site_pixmap, stale_brush, zkill_level,
)
from homebound.theme.fonts import FONT_FILES, font_directory, load_fonts
from homebound.theme.qss import apply_theme, build_palette, build_qss, repolish
from homebound.theme.tokens import HEX_COLORS, OPACITY, PIXELS, TYPE, color, font, hex_to_rgba, qss_color


@pytest.fixture
def canvas(qt_application) -> Iterator[tuple[QPainter, QPixmap]]:
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
    """An 8-digit hex token is read in CSS order (#rrggbbaa) for both QColor values and style sheets."""
    # accent-dim is '#7cc4ff29' (CSS order): alpha 0x29, colour 7c c4 ff
    assert hex_to_rgba(HEX_COLORS["accent-dim"]) == (0x7C, 0xC4, 0xFF, 0x29)
    assert qss_color("accent-dim") == "rgba(124,196,255,41)"
    assert qss_color("bg-0") == "#0a0d10"


def test_hex_to_rgba_without_alpha_is_opaque() -> None:
    """A 6-digit hex colour gets full alpha (255)."""
    assert hex_to_rgba("#102030") == (0x10, 0x20, 0x30, 255)


@pytest.mark.parametrize("text", ["#12345", "#1234567", "", "#"])
def test_hex_to_rgba_rejects_wrong_length(text) -> None:
    """Hex text that is not 6 or 8 digits after the # raises ValueError."""
    with pytest.raises(ValueError):
        hex_to_rgba(text)


def test_hex_to_rgba_rejects_non_hex_and_non_text() -> None:
    """Non-hex digits raise ValueError; a non-string raises TypeError."""
    with pytest.raises(ValueError):
        hex_to_rgba("#gggggg")
    with pytest.raises(TypeError):
        hex_to_rgba(123)


def test_color_and_unknown_token(qt_application) -> None:
    """color() turns a token into a QColor, alpha included; unknown tokens raise KeyError in color() and qss_color()."""
    assert color("bg-0") == QColor("#0a0d10")
    assert color("accent-dim").alpha() == 0x29
    with pytest.raises(KeyError):
        color("no-such-token")
    with pytest.raises(KeyError):
        qss_color("no-such-token")


def test_font_uses_token_and_pixel_override(qt_application) -> None:
    """font() takes size and weight from the type token; an explicit pixel size overrides the size."""
    spec = TYPE["ui-body"]
    assert font("ui-body").pixelSize() == spec["px"]
    assert font("ui-body", 20).pixelSize() == 20
    assert font("ui-body").weight() == QFont.Weight(spec["weight"])


def test_font_mono_hint_and_letter_spacing(qt_application) -> None:
    """Mono styles hint Monospace; letter spacing is spacing_em times the pixel size, and 0 when unset."""
    mono = next(name for name, spec in TYPE.items() if spec["family"] == "mono")
    assert font(mono).styleHint() == QFont.StyleHint.Monospace
    spaced = next((name for name, spec in TYPE.items() if spec["spacing_em"]), None)
    if spaced:
        assert font(spaced, 10).letterSpacing() == pytest.approx(TYPE[spaced]["spacing_em"] * 10, abs=0.05)
    unspaced = next(name for name, spec in TYPE.items() if not spec["spacing_em"])
    assert font(unspaced).letterSpacing() == 0


def test_font_validation(qt_application) -> None:
    """A zero size raises ValueError, a text size TypeError and an unknown style KeyError."""
    with pytest.raises(ValueError):
        font("ui-body", 0)
    with pytest.raises(TypeError):
        font("ui-body", "12")
    with pytest.raises(KeyError):
        font("no-such-style")


def test_type_and_sizes() -> None:
    """Overlay text is at least 11 px, node and overlay widths match the spec, and opacities lie in (0, 1]."""
    assert TYPE["overlay-row"]["px"] >= 11 and TYPE["overlay-mono"]["px"] >= 11
    assert PIXELS["size-node-w"] == 168 and PIXELS["size-overlay-w"] == 320
    assert OPACITY and all(0 < value <= 1 for value in OPACITY.values())


# ---------------------------------------------------------------- qss

def test_qss_fully_resolved() -> None:
    """The built style sheet has no {placeholder} left unreplaced."""
    qss = build_qss()
    assert not re.search(r"\{[A-Za-z0-9_.-]+\}", qss)


def test_qss_icon_paths_exist() -> None:
    """Every url(...) in the style sheet points to an existing file."""
    paths = re.findall(r"url\(([^)]+)\)", build_qss())
    assert paths
    from pathlib import Path
    assert all(Path(path).exists() for path in paths)


def test_build_palette_uses_tokens(qt_application) -> None:
    """The palette maps window, highlight and disabled text to the bg-1, accent and ink-disabled tokens."""
    from PySide6.QtGui import QPalette
    palette = build_palette()
    assert palette.color(QPalette.ColorRole.Window) == color("bg-1")
    assert palette.color(QPalette.ColorRole.Highlight) == color("accent")
    assert palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text) == color("ink-disabled")


def test_apply_theme(qt_application) -> None:
    """apply_theme sets the style sheet and body font and returns the app; a non-application raises TypeError."""
    assert apply_theme(qt_application) is qt_application
    assert qt_application.styleSheet() == build_qss()
    assert qt_application.font().pixelSize() == TYPE["ui-body"]["px"]
    with pytest.raises(TypeError):
        apply_theme("not an application")
    assert isinstance(qt_application, QApplication)


def test_bundled_fonts_load_privately(qt_application) -> None:
    """Every bundled font file loads and adds Noto Sans or Hack; the licences ship next to them."""
    loaded = load_fonts()
    assert set(loaded) == set(FONT_FILES)
    assert all(families and families[0] in ("Noto Sans", "Hack") for families in loaded.values())
    assert load_fonts() is loaded  # only once
    assert {"Noto Sans", "Hack"} <= set(QFontDatabase.families())
    for licence in ("OFL-NotoSans.txt", "LICENSE-Hack.txt"):
        assert (font_directory() / licence).is_file()


def test_repolish(qt_application) -> None:
    """repolish returns the widget it was given; a non-widget raises TypeError."""
    label = QLabel("x")
    assert repolish(label) is label
    with pytest.raises(TypeError):
        repolish("not a widget")


# ---------------------------------------------------------------- sites and effects

def test_site_icons_load_and_tint(qt_application) -> None:
    """Every site group icon has lit pixels in its token colour; None and unknown groups fall back to the unknown icon."""
    for group, token in SITE_TOKENS.items():
        image = site_pixmap(group, 16, 1.0).toImage()
        lit = [image.pixelColor(x, y) for x in range(16) for y in range(16) if image.pixelColor(x, y).alpha() > 128]
        assert lit, group
        tint = color(token)
        # premultiplied alpha rounds by a step or two when read back
        assert all(abs(pixel.red() - tint.red()) <= 2 and abs(pixel.green() - tint.green()) <= 2
                   and abs(pixel.blue() - tint.blue()) <= 2 for pixel in lit), group
    assert site_pixmap(None).toImage() == site_pixmap("unknown").toImage()
    assert site_pixmap("no-such-group").toImage() == site_pixmap("unknown").toImage()


def test_site_pixmap_scaling_and_validation(qt_application) -> None:
    """Site pixmaps scale to the size times the device pixel ratio and keep the ratio; size 0 raises ValueError."""
    assert site_pixmap("gas", 24, 1.0).width() == 24       # smooth scaling path
    assert site_pixmap("gas", 16, 2.0).width() == 32
    assert site_pixmap("gas", 16, 2.0).devicePixelRatio() == 2.0
    with pytest.raises(ValueError):
        site_pixmap("gas", 0)


@pytest.mark.parametrize("effect", EFFECTS)
def test_effect_pixmap_is_tinted(qt_application, effect) -> None:
    """Each effect icon is drawn at the requested size with its lit pixels in the effect's colour."""
    pixmap = effect_pixmap(effect, 14, 1.0)
    assert pixmap.width() == 14
    tint = color(f"effect-{effect}")
    image = pixmap.toImage()
    lit = [image.pixelColor(x, y) for x in range(14) for y in range(14) if image.pixelColor(x, y).alpha() > 200]
    assert lit
    assert all(abs(pixel.red() - tint.red()) <= 3 for pixel in lit)


def test_effect_pixmap_validation(qt_application) -> None:
    """An unknown effect or a zero size raises ValueError."""
    with pytest.raises(ValueError):
        effect_pixmap("supernova")
    with pytest.raises(ValueError):
        effect_pixmap("pulsar", 0)


def test_draw_effect_and_site(canvas) -> None:
    """No effect draws nothing and takes no width; an effect or site icon draws and returns its size (16 by default)."""
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
    """Every class badge paints and returns the width class_badge_width predicts, at least 26 px."""
    painter, pixmap = canvas
    width = draw_class_badge(painter, 5, 5, label, 0.7 if label in ("HS", "LS", "NS") else None)
    assert width == class_badge_width(label, 0.7 if label in ("HS", "LS", "NS") else None) >= 26
    painter.end()
    assert painted(pixmap)
    painter.begin(pixmap)


def test_k_space_badge_is_wider_with_security(qt_application) -> None:
    """A security value widens a k-space badge but not a w-space one, which shows no security."""
    assert class_badge_width("HS", 0.9) > class_badge_width("HS")
    assert class_badge_width("C5", 0.9) == class_badge_width("C5")  # w-space shows no security


def test_unknown_class_badge_draws_nothing(canvas) -> None:
    """An unknown class label paints nothing and returns width 0."""
    painter, pixmap = canvas
    assert draw_class_badge(painter, 0, 0, "XX") == 0.0
    painter.end()
    assert not painted(pixmap)
    painter.begin(pixmap)


def test_class_badge_validation(qt_application) -> None:
    """A non-string class label raises TypeError."""
    with pytest.raises(TypeError):
        class_badge_width(5)


def test_draw_static(canvas) -> None:
    """Known and unknown static classes both paint with a positive width; a non-string class raises TypeError."""
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
    """Each marker slot paints and returns its size: 14 by default, or the size given."""
    painter, pixmap = canvas
    assert draw_character_marker(painter, 10, 10, slot) == 14
    assert draw_character_marker(painter, 40, 10, slot, 28) == 28
    painter.end()
    assert painted(pixmap)
    painter.begin(pixmap)


def test_character_marker_shapes_wrap_after_six(qt_application) -> None:
    """Slot 7 reuses the shape of slot 1; slots 2 to 5 are polygons."""
    assert painting._character_marker_path(7) == painting._character_marker_path(1)
    assert set(MARKER_POLYGONS) == {2, 3, 4, 5}


def test_character_marker_validation(canvas) -> None:
    """Slot 0 or size 0 raises ValueError; a text slot raises TypeError."""
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
    """Kills in the last hour and day map to the none/low/med/high levels at the documented thresholds."""
    assert zkill_level(kills_1h, kills_24h) == level


def test_zkill_level_validation() -> None:
    """Negative counts raise ValueError; text counts raise TypeError."""
    with pytest.raises(ValueError):
        zkill_level(-1, 0)
    with pytest.raises(TypeError):
        zkill_level(0, "5")


def test_draw_zkill_and_recent(canvas) -> None:
    """The zKill pip is 13.5 px wide for any level, 16 px more with the recent-kill mark; the mark alone is 13 px."""
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
    """Owner tags paint (home or not) at the width owner_width predicts, which grows with the text."""
    painter, pixmap = canvas
    width = draw_owner(painter, 5, 5, "ABC", 12, home=home)   # slot 12 wraps the ten owner colours
    assert width == owner_width("ABC")
    assert owner_width("ABCDE") > owner_width("A")
    painter.end()
    assert painted(pixmap)
    painter.begin(pixmap)


def test_owner_validation(canvas) -> None:
    """A non-string owner raises TypeError; slot 0 raises ValueError."""
    painter, _ = canvas
    with pytest.raises(TypeError):
        owner_width(1)
    with pytest.raises(ValueError):
        draw_owner(painter, 0, 0, "ABC", 0)


def test_draw_chip(canvas) -> None:
    """Chips paint and widen with their text; a non-string text or a non-QColor fill raises TypeError."""
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
    """edge_pens has one pen per connection state with the spec's dash patterns, widths and selection alpha."""
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


@pytest.mark.parametrize("lifetime", [LIFETIME_STABLE, LIFETIME_4H, LIFETIME_1H, LIFETIME_EXPIRED])
def test_stroke_edge_paints_each_lifetime(qt_application, lifetime) -> None:
    """stroke_edge draws on every lifetime; an expired hole is fainter than a stable one; a wider stroke covers more."""
    def render(life: int, mass: str = "fresh", wider: bool = False) -> QImage:
        image = QImage(60, 20, QImage.Format.Format_ARGB32)
        image.fill(QColor("black"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath(QPointF(5, 10))
        path.lineTo(55, 10)
        painting.stroke_edge(painter, path, mass, life, wider)
        painter.end()
        return image

    drawn = lambda img: [(x, y) for x in range(60) for y in range(20) if img.pixelColor(x, y) != QColor("black")]
    assert drawn(render(lifetime))
    assert render(LIFETIME_EXPIRED).pixelColor(30, 10).lightness() < render(LIFETIME_STABLE).pixelColor(30, 10).lightness()
    assert render(lifetime, "frigate_only", True).pixelColor(30, 10) != QColor("black")
    covered = lambda img: sum(img.pixelColor(x, y).lightnessF() for x in range(60) for y in range(20))
    assert covered(render(LIFETIME_STABLE, wider=True)) > covered(render(LIFETIME_STABLE))


def test_stroke_edge_rejects_bad_arguments(canvas) -> None:
    """stroke_edge raises TypeError for a wrong type and ValueError for an unknown pen."""
    painter, _ = canvas
    with pytest.raises(ValueError):
        painting.stroke_edge(painter, QPainterPath(), "nope", LIFETIME_STABLE)
    with pytest.raises(TypeError):
        painting.stroke_edge(painter, "path", "fresh", LIFETIME_STABLE)


def test_edge_pens_are_cached(qt_application) -> None:
    """edge_pens returns the same dict until the cache is cleared."""
    assert edge_pens() is edge_pens()
    first = edge_pens()
    edge_pens.cache_clear()
    assert edge_pens() is not first


def test_row_background(qt_application) -> None:
    """row_background tints a selected row, lightens a hovered one, leaves a plain one, and draws the bottom line."""
    def pixel(selected: bool, hover: bool, y: int) -> QColor:
        image = QImage(40, 24, QImage.Format.Format_ARGB32)
        image.fill(QColor("black"))
        painter = QPainter(image)
        painting.row_background(painter, QRectF(0, 0, 40, 19), selected, hover)
        painter.end()
        return image.pixelColor(20, y)

    assert pixel(True, False, 5) not in (QColor("black"), color("bg-4"))   # accent-dim blended onto black
    assert pixel(True, True, 5) == pixel(True, False, 5)
    assert pixel(False, True, 5) == color("bg-4")
    assert pixel(False, False, 5) == QColor("black")
    assert any(pixel(False, False, row) != QColor("black") for row in (18, 19, 20))   # the bottom line


def test_halo_text(canvas) -> None:
    """Halo text paints and returns the font's text advance; a non-string text raises TypeError."""
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
    """The stale brush is a QBrush in the line-1 colour."""
    brush = stale_brush()
    assert isinstance(brush, QBrush)
    assert brush.color() == color("line-1")


def test_draw_dot_grid(canvas) -> None:
    """The dot grid returns its dot count, snapped to the step (24 px by default); a step below 1 raises ValueError."""
    painter, pixmap = canvas
    rect = QRectF(0, 0, 100, 50)
    assert draw_dot_grid(painter, rect, 10) == 50
    assert draw_dot_grid(painter, QRectF(-5, -5, 20, 20), 10) == 9   # grid snaps to multiples of the step
    default_step = PIXELS["sp-24"]
    assert draw_dot_grid(painter, rect) == len(range(0, 100, int(default_step))) * len(range(0, 50, int(default_step)))
    with pytest.raises(ValueError):
        draw_dot_grid(painter, rect, 0.5)


def test_draw_line_grid_counts(canvas) -> None:
    """The line grid counts all lines with minor=True, only every third (major) one without; bad steps raise ValueError."""
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
    """opacity() returns the token's value; an unknown token raises KeyError."""
    name = next(iter(OPACITY))
    assert opacity(name) == OPACITY[name]
    with pytest.raises(KeyError):
        opacity("no-such-token")



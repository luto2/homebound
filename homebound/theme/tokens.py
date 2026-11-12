"""Design tokens from design-system/tokens.json, exposed as Qt-friendly values."""
import json
import re
from importlib.resources import files

from PySide6.QtGui import QColor, QFont

from ..validation import require_integer, require_text

THEME = "dark"

_TOKENS = json.loads(files("homebound.theme").joinpath("tokens.json").read_text())

RAW_COLORS: dict[str, object] = {token["name"]: token["value"] for token in _TOKENS["color"]["tokens"]}
_REFERENCE = re.compile(r"\{(.+)\}")


def _resolve(value: object) -> str:
    """Resolve a colour token value: pick the theme variant and follow ``{other-token}`` references."""
    if isinstance(value, dict):
        value = value.get(THEME) or next(iter(value.values()))
    if not isinstance(value, str):
        raise TypeError(f"a colour token value must be text, not {value!r}")
    match = _REFERENCE.fullmatch(value)
    return _resolve(RAW_COLORS[match.group(1)]) if match else value


HEX_COLORS: dict[str, str] = {name: _resolve(value) for name, value in RAW_COLORS.items()}  # '#rrggbb' or '#rrggbbaa'
PIXELS: dict[str, float] = {
    token["name"]: float(str(token["value"]).rstrip("px"))
    for family in ("spacing", "radius", "border", "size")
    for token in _TOKENS[family]["tokens"]
}
OPACITY: dict[str, float] = {token["name"]: float(token["value"]) for token in _TOKENS["opacity"]["tokens"]}

FAMILIES = {
    key: [name.strip().strip('"') for name in value.split(",") if name.strip() not in ("sans-serif", "monospace")]
    for key, value in _TOKENS["type"]["families"].items()
}
TYPE: dict[str, dict] = {}
for _group in _TOKENS["type"]["groups"]:
    for _style in _group["styles"]:
        TYPE[_style["name"]] = {
            "family": _style.get("family", _group["family"]),
            "px": int(_style["fontSize"].rstrip("px")),
            "weight": int(_style["fontWeight"]),
            "spacing_em": float(_style.get("letterSpacing", "0em").rstrip("em")),
        }


def hex_to_rgba(hex_color: str) -> tuple[int, int, int, int]:
    """Parse CSS-style ``#rrggbb`` / ``#rrggbbaa`` (alpha LAST, unlike Qt's ``#aarrggbb``).

    Raises:
        ValueError: If the text is not 6 or 8 hex digits after the ``#``.
    """
    require_text(hex_color, "hex_color")
    digits = hex_color.lstrip("#")
    if len(digits) not in (6, 8):
        raise ValueError(f"expected #rrggbb or #rrggbbaa, got {hex_color!r}")
    red, green, blue = int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)
    alpha = int(digits[6:8], 16) if len(digits) == 8 else 255
    return red, green, blue, alpha


def color(name: str) -> QColor:
    """Return a colour token as a QColor.

    Raises:
        KeyError: If there is no such token.
    """
    return QColor(*hex_to_rgba(HEX_COLORS[name]))


def qss_color(name: str) -> str:
    """Return a colour token for a style sheet: ``#rrggbb``, or ``rgba(...)`` with alpha."""
    red, green, blue, alpha = hex_to_rgba(HEX_COLORS[name])
    return f"rgba({red},{green},{blue},{alpha})" if alpha < 255 else f"#{red:02x}{green:02x}{blue:02x}"


def font(style: str, pixel_size: int | None = None) -> QFont:
    """Return the QFont of a type token, e.g. ``font('mono-sig')``.

    Args:
        style: The type token name.
        pixel_size: A size that overrides the token's.

    Raises:
        KeyError: If there is no such type token.
        ValueError: If the size is below 1.
    """
    require_integer(pixel_size, "pixel_size", minimum=1, optional=True)
    spec = TYPE[style]
    size = pixel_size or spec["px"]
    result = QFont(FAMILIES[spec["family"]])
    result.setPixelSize(size)
    result.setWeight(QFont.Weight(spec["weight"]))
    if spec["family"] == "mono":
        result.setStyleHint(QFont.StyleHint.Monospace)
    if spec["spacing_em"]:
        result.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spec["spacing_em"] * size)
    return result

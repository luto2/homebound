"""Design tokens from design-system/tokens.json, exposed as Qt-friendly values."""
import json
import re
from importlib.resources import files

THEME = "dark"

_T = json.loads(files("homebound.theme").joinpath("tokens.json").read_text())

COLORS_RAW: dict[str, object] = {t["name"]: t["value"] for t in _T["color"]["tokens"]}


def _resolve(v):
    if isinstance(v, dict):
        v = v.get(THEME) or next(iter(v.values()))
    m = re.fullmatch(r"\{(.+)\}", v)
    return _resolve(COLORS_RAW[m.group(1)]) if m else v


HEX: dict[str, str] = {k: _resolve(v) for k, v in COLORS_RAW.items()}  # '#rrggbb' or '#rrggbbaa'
PX: dict[str, float] = {
    t["name"]: float(str(t["value"]).rstrip("px"))
    for fam in ("spacing", "radius", "border", "size")
    for t in _T[fam]["tokens"]
}
OP: dict[str, float] = {t["name"]: float(t["value"]) for t in _T["opacity"]["tokens"]}

FAMILIES = {
    k: [f.strip().strip('"') for f in v.split(",") if f.strip() not in ("sans-serif", "monospace")]
    for k, v in _T["type"]["families"].items()
}
TYPE: dict[str, dict] = {}
for _g in _T["type"]["groups"]:
    for _s in _g["styles"]:
        TYPE[_s["name"]] = {
            "family": _s.get("family", _g["family"]),
            "px": int(_s["fontSize"].rstrip("px")),
            "weight": int(_s["fontWeight"]),
            "spacing_em": float(_s.get("letterSpacing", "0em").rstrip("em")),
        }


def hex_to_rgba(h: str) -> tuple[int, int, int, int]:
    """Parse CSS-style #rrggbb / #rrggbbaa (alpha LAST, unlike Qt's #aarrggbb)."""
    h = h.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    a = int(h[6:8], 16) if len(h) == 8 else 255
    return r, g, b, a


def color(name: str):
    from PySide6.QtGui import QColor

    return QColor(*hex_to_rgba(HEX[name]))


def qss_color(name: str) -> str:
    r, g, b, a = hex_to_rgba(HEX[name])
    return f"rgba({r},{g},{b},{a})" if a < 255 else f"#{r:02x}{g:02x}{b:02x}"


def font(style: str, px: int | None = None):
    """QFont for a type token, e.g. font('mono-sig')."""
    from PySide6.QtGui import QFont

    s = TYPE[style]
    f = QFont(FAMILIES[s["family"]])
    f.setPixelSize(px or s["px"])
    f.setWeight(QFont.Weight(s["weight"]))
    if s["family"] == "mono":
        f.setStyleHint(QFont.StyleHint.Monospace)
    if s["spacing_em"]:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, s["spacing_em"] * (px or s["px"]))
    return f

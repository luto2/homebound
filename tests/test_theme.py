import re

from homebound.theme.qss import build_qss
from homebound.theme.tokens import HEX, PX, TYPE, hex_to_rgba, qss_color


def test_alpha_is_last_in_tokens():
    # accent-dim is '#7cc4ff29' (CSS order): alpha 0x29, colour 7c c4 ff
    assert hex_to_rgba(HEX["accent-dim"]) == (0x7C, 0xC4, 0xFF, 0x29)
    assert qss_color("accent-dim") == "rgba(124,196,255,41)"
    assert qss_color("bg-0") == "#0a0d10"


def test_qss_fully_resolved():
    qss = build_qss()
    assert not re.search(r"\{[A-Za-z0-9_.-]+\}", qss)


def test_type_and_sizes():
    assert TYPE["overlay-row"]["px"] >= 11 and TYPE["overlay-mono"]["px"] >= 11
    assert PX["size-node-w"] == 168 and PX["size-overlay-w"] == 320

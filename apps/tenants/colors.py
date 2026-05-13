"""Derive a full CSS theme palette from a primary + accent hex pair.

The default Crimson Black palette in ``static/css/custom-v15.css`` defines
17 primary-related variables (50-900 scale plus hover/active/dark/light/
subtle/ring) and 3 accent variables. When a tenant picks a custom colour
through the Branding settings, every variable in that set needs to be
re-derived -- otherwise only the few that are explicitly overridden change
and the rest of the UI remains the original red.

WCAG accessibility:
    ``derive_palette`` also picks a readable foreground colour (white or
    near-black) for text that sits directly on the tenant primary. The
    chosen foreground is exposed as ``text_on_primary`` and emitted as
    ``--crm-text-on-primary`` by ``base.html``. If neither foreground hits
    a 4.5:1 contrast ratio against the primary, a warning is logged so the
    operator can be alerted to an unreadable colour pick.
"""

from __future__ import annotations

import colorsys
import logging
import re

logger = logging.getLogger(__name__)

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")
DEFAULT_PRIMARY = "#C1121F"
DEFAULT_ACCENT = "#E11D2D"
WHITE = "#FFFFFF"
NEAR_BLACK = "#0B0B0B"
WCAG_AA_NORMAL = 4.5


def _normalize(hex_str: str | None, fallback: str) -> str:
    """Return a 7-char ``#RRGGBB`` string, falling back if the input is bad."""
    if not hex_str:
        return fallback
    match = _HEX_RE.match(hex_str.strip())
    if not match:
        return fallback
    return "#" + match.group(1).upper()


def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    h = hex_str.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hls(r: int, g: int, b: int) -> tuple[float, float, float]:
    return colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)


def _hls_to_hex(h: float, l: float, s: float) -> str:
    l = max(0.0, min(1.0, l))
    s = max(0.0, min(1.0, s))
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return "#{:02X}{:02X}{:02X}".format(round(r * 255), round(g * 255), round(b * 255))


def _relative_luminance(r: int, g: int, b: int) -> float:
    """WCAG 2.x relative luminance for an sRGB colour."""

    def _channel(c: int) -> float:
        c_lin = c / 255.0
        return c_lin / 12.92 if c_lin <= 0.03928 else ((c_lin + 0.055) / 1.055) ** 2.4

    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def _contrast_ratio(l1: float, l2: float) -> float:
    """WCAG contrast ratio between two relative luminances."""
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


def pick_on_color(bg_hex: str) -> tuple[str, float]:
    """Return ``(foreground_hex, contrast_ratio)`` for text on ``bg_hex``.

    Picks whichever of WHITE / NEAR_BLACK gives the higher contrast ratio
    against the background. The returned ratio is the winning ratio.
    """
    r, g, b = _hex_to_rgb(bg_hex)
    bg_l = _relative_luminance(r, g, b)
    wr, wg, wb = _hex_to_rgb(WHITE)
    nr, ng, nb = _hex_to_rgb(NEAR_BLACK)
    white_ratio = _contrast_ratio(_relative_luminance(wr, wg, wb), bg_l)
    black_ratio = _contrast_ratio(_relative_luminance(nr, ng, nb), bg_l)
    if white_ratio >= black_ratio:
        return WHITE, white_ratio
    return NEAR_BLACK, black_ratio


def derive_palette(primary: str | None, accent: str | None) -> dict[str, str]:
    """Return every CSS custom-property value used by the theme.

    Keys map 1:1 to the ``--crm-primary*`` / ``--crm-accent*`` names used
    in ``custom-v15.css`` (with hyphens replaced by underscores for safe
    template variable access).
    """
    primary_hex = _normalize(primary, DEFAULT_PRIMARY)
    accent_hex = _normalize(accent, DEFAULT_ACCENT)

    p_r, p_g, p_b = _hex_to_rgb(primary_hex)
    p_h, p_l, p_s = _rgb_to_hls(p_r, p_g, p_b)

    def shade(lightness_pct: float) -> str:
        return _hls_to_hex(p_h, lightness_pct / 100.0, p_s)

    a_r, a_g, a_b = _hex_to_rgb(accent_hex)

    text_on_primary, on_primary_contrast = pick_on_color(primary_hex)
    text_on_accent, on_accent_contrast = pick_on_color(accent_hex)
    if on_primary_contrast < WCAG_AA_NORMAL:
        logger.warning(
            "Tenant primary colour %s fails WCAG AA contrast against both "
            "white and near-black (best ratio %.2f:1, needs %.1f:1). "
            "Text-on-primary will use %s but may be hard to read.",
            primary_hex,
            on_primary_contrast,
            WCAG_AA_NORMAL,
            text_on_primary,
        )

    return {
        # Primary scale (Tailwind-style lightness anchors)
        "primary": primary_hex,
        "primary_50": shade(97),
        "primary_100": shade(92),
        "primary_200": shade(84),
        "primary_300": shade(74),
        "primary_400": shade(58),
        "primary_500": primary_hex,
        "primary_600": shade(max(p_l * 100 - 5, 32)),
        "primary_700": shade(max(p_l * 100 - 12, 25)),
        "primary_800": shade(max(p_l * 100 - 18, 18)),
        "primary_900": shade(max(p_l * 100 - 24, 12)),
        # Derived single-tones
        "primary_hover": shade(min(p_l * 100 + 6, 68)),
        "primary_active": shade(max(p_l * 100 - 6, 14)),
        "primary_dark": shade(max(p_l * 100 - 18, 10)),
        "primary_light": f"rgba({p_r}, {p_g}, {p_b}, 0.10)",
        "primary_subtle": f"rgba({p_r}, {p_g}, {p_b}, 0.06)",
        "primary_ring": f"rgba({p_r}, {p_g}, {p_b}, 0.22)",
        "primary_rgb": f"{p_r}, {p_g}, {p_b}",
        # Accent
        "accent": accent_hex,
        "accent_hover": primary_hex,
        "accent_light": f"rgba({a_r}, {a_g}, {a_b}, 0.10)",
        "accent_rgb": f"{a_r}, {a_g}, {a_b}",
        # Readable foreground for text/icons sitting directly on the
        # tenant primary or accent. Emitted as --crm-text-on-primary
        # / --crm-text-on-accent so component rules can stop hardcoding
        # `color: #FFFFFF` on primary-coloured surfaces.
        "text_on_primary": text_on_primary,
        "text_on_accent": text_on_accent,
    }

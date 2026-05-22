"""
Theme leakage checker.

Guards against new hardcoded hex colour literals slipping into the
codebase outside the design-system token system.

Compares against a baseline file (scripts/.theme_baseline.json) and
fails only on NET INCREASES per file -- pre-existing leaks aren't
flagged on every run, but a new commit that adds hex literals is.

Refresh the baseline with:
    python3 scripts/check_theme.py --baseline

Strict mode (any leak fails, ignores baseline):
    python3 scripts/check_theme.py --strict

Exit codes:
    0  clean (or no NEW leaks vs baseline)
    1  new leaks detected
    2  configuration / I/O error
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = ROOT / "scripts" / ".theme_baseline.json"

# Files that may legitimately contain raw hex (excluded from scan).
ALLOWLIST_FILES = {
    ROOT / "apps/tenants/colors.py",                 # palette derivation
    ROOT / "scripts/check_theme.py",                 # this file (regex patterns)
    ROOT / "templates/pages/settings/tenant.html",   # colour picker UI
    ROOT / "static/js/theme.js",                     # may carry fallback hex
    # Legacy "v9" CSS snapshot — NOT loaded by base.html (custom-v15.css is
    # the live file). Kept around as a committed reference copy; no point
    # sweeping ~20k lines that nothing serves.
    ROOT / "static/css/custom.css",
}

ALLOWLIST_DIRS = [
    ROOT / "templates" / "tickets" / "email",
    ROOT / "templates" / "notifications" / "email",
    ROOT / "templates" / "auth" / "email",
    ROOT / "templates" / "knowledge" / "email",
    ROOT / "tests",
    ROOT / ".venv",
    ROOT / "env",
    ROOT / "node_modules",
    ROOT / ".git",
]

SCAN_GLOBS = [
    "static/css/**/*.css",
    "static/js/**/*.js",
    "templates/**/*.html",
]

# Hex literal: # then 3/6/8 hex digits, bounded so #XXXXXX inside a longer
# hex string is not double-counted.
HEX_RE = re.compile(r"(?<![0-9a-fA-F#])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
HTML_ENTITY_RE = re.compile(r"&#\d+;")
INPUT_COLOR_RE = re.compile(r"""<input[^>]*\btype\s*=\s*["']color["'][^>]*>""", re.I)
DATA_HEX_ATTR_RE = re.compile(r"""\b(data-[a-z-]+)\s*=\s*["']\s*#[0-9a-fA-F]{3,8}\s*["']""", re.I)
DJANGO_TAG_RE = re.compile(r"\{%.*?%\}|\{\{.*?\}\}", re.S)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
SCRIPT_BLOCK_RE = re.compile(r"(<script(?:\s[^>]*)?>)(.*?)(</script>)", re.S | re.I)
CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
CSS_ROOT_BLOCK_RE = re.compile(r"^:root\s*\{[^}]*\}", re.M | re.S)
CSS_DATA_BS_BLOCK_RE = re.compile(r'^\[data-bs-theme="(?:dark|light)"\]\s*\{[^}]*\}', re.M | re.S)
# JS line comment — // followed by anything up to EOL, but NOT preceded by a
# character that would signal "this // is inside a string / URL". Lookbehind
# excludes ':' (protocol like https://), quotes (string literal containing //),
# '=', '(', ',', '[' (string-literal contexts). Block comments use CSS_COMMENT_RE.
JS_LINE_COMMENT_RE = re.compile(r"""(?<![:'"=(,\[])//[^\n]*""")


def is_in_allowlist_dir(path: Path) -> bool:
    return any(d in path.parents for d in ALLOWLIST_DIRS)


def _blank_keep_newlines(m: re.Match) -> str:
    """Replacement function: blank matched region but preserve newlines."""
    return "".join("\n" if c == "\n" else " " for c in m.group(0))


def mask_js_comments(text: str) -> str:
    """Blank JS // line and /* */ block comments, preserving newlines.

    Used inside <script> blocks AND on .js files so that hex literals inside
    JS comments don't trigger false positives, but hex inside JS string
    literals / object-dict values (e.g. `style.color = '#FF0000'` or
    `COLORS = ['#FF0000', '#00FF00']`) still gets flagged.
    """
    text = CSS_COMMENT_RE.sub(_blank_keep_newlines, text)
    text = JS_LINE_COMMENT_RE.sub(_blank_keep_newlines, text)
    return text


def mask_protected(text: str, suffix: str) -> str:
    """Replace protected regions with same-length whitespace so line numbers stay aligned."""
    text = HTML_ENTITY_RE.sub(_blank_keep_newlines, text)

    if suffix == ".css":
        text = CSS_COMMENT_RE.sub(_blank_keep_newlines, text)
        text = CSS_ROOT_BLOCK_RE.sub(_blank_keep_newlines, text)
        text = CSS_DATA_BS_BLOCK_RE.sub(_blank_keep_newlines, text)
    elif suffix == ".html":
        text = HTML_COMMENT_RE.sub(_blank_keep_newlines, text)
        text = DJANGO_TAG_RE.sub(_blank_keep_newlines, text)
        text = INPUT_COLOR_RE.sub(_blank_keep_newlines, text)
        text = DATA_HEX_ATTR_RE.sub(_blank_keep_newlines, text)
        # Inside <script> blocks: blank the <script> tag itself (so any hex
        # attribute on it can't appear elsewhere) and mask JS comments in
        # the body, then leave the body otherwise visible to HEX_RE. Hex
        # inside JS object dicts and string literals is now flagged.
        text = SCRIPT_BLOCK_RE.sub(
            lambda m: (
                _blank_keep_newlines_str(m.group(1))
                + mask_js_comments(m.group(2))
                + _blank_keep_newlines_str(m.group(3))
            ),
            text,
        )
    elif suffix == ".js":
        text = mask_js_comments(text)
    return text


def _blank_keep_newlines_str(s: str) -> str:
    return "".join("\n" if c == "\n" else " " for c in s)


def scan_file(path: Path) -> list[tuple[int, str]]:
    try:
        src = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    masked = mask_protected(src, path.suffix)
    hits = []
    for m in HEX_RE.finditer(masked):
        ln = masked.count("\n", 0, m.start()) + 1
        hits.append((ln, m.group(0)))
    return hits


def iter_targets():
    for glob in SCAN_GLOBS:
        for p in sorted(ROOT.glob(glob)):
            if p in ALLOWLIST_FILES:
                continue
            if is_in_allowlist_dir(p):
                continue
            yield p


def collect_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in iter_targets():
        hits = scan_file(p)
        if hits:
            counts[str(p.relative_to(ROOT))] = len(hits)
    return counts


def load_baseline() -> dict[str, int]:
    if not BASELINE_PATH.exists():
        return {}
    try:
        return json.loads(BASELINE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def write_baseline(counts: dict[str, int]) -> None:
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(json.dumps(counts, indent=2, sort_keys=True) + "\n")


def main(argv: list[str]) -> int:
    mode = "delta"
    if "--baseline" in argv:
        mode = "baseline"
    elif "--strict" in argv:
        mode = "strict"

    current = collect_counts()

    if mode == "baseline":
        write_baseline(current)
        total = sum(current.values())
        print(f"Baseline written to {BASELINE_PATH.relative_to(ROOT)}")
        print(f"Baseline total: {total} hex literals across {len(current)} files.")
        return 0

    if mode == "strict":
        total = sum(current.values())
        if total == 0:
            print("STRICT OK: zero off-token hex literals.")
            return 0
        print(f"\nSTRICT FAIL: {total} hex literals across {len(current)} files.")
        for f, n in sorted(current.items(), key=lambda kv: -kv[1])[:10]:
            print(f"  {n:5d}  {f}")
        return 1

    baseline = load_baseline()
    if not baseline:
        print("WARNING: no baseline found. Run `python3 scripts/check_theme.py --baseline` to seed.")
        print(f"Current total: {sum(current.values())} hex across {len(current)} files.")
        return 0

    regressions = []
    for f, n in current.items():
        b = baseline.get(f, 0)
        if n > b:
            regressions.append((f, b, n))

    if not regressions:
        print(f"OK: no new theme leakage. ({sum(current.values())} pre-existing hex literals tracked against baseline)")
        return 0

    print(f"\nFAIL: {len(regressions)} file(s) gained off-token hex literals since baseline.\n")
    for f, b, n in sorted(regressions, key=lambda x: x[2] - x[1], reverse=True):
        print(f"  {f}: {b} -> {n}  (+{n - b})")
        hits = scan_file(ROOT / f)
        for ln, hx in hits[:5]:
            print(f"     L{ln:5d}  {hx}")
        if len(hits) > 5:
            print(f"     ... and {len(hits) - 5} more (see file)")

    print("\nFixes:")
    print("  - route hex through a CSS custom property (var(--crm-*) / var(--status-*))")
    print("  - OR add file to ALLOWLIST_FILES / ALLOWLIST_DIRS in scripts/check_theme.py")
    print("  - OR put the value inside an :root / [data-bs-theme] token block")
    print("  - If a leak is INTENTIONAL and approved, refresh the baseline:")
    print("        python3 scripts/check_theme.py --baseline")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""
Shared email utilities for normalization and parsing.

These are pure functions with no side effects, no database access,
and no tenant context dependency. Safe to call from anywhere.
"""

import re

# ---------------------------------------------------------------------------
# Message-ID normalization
# ---------------------------------------------------------------------------


def normalize_message_id(raw_id):
    """
    Strip angle brackets and whitespace from a single Message-ID.

    Handles all variations:
      "<id@host>"   → "id@host"
      "id@host"     → "id@host"
      " <id@host> " → "id@host"
      ""            → ""
    """
    if not raw_id:
        return ""
    return raw_id.strip().strip("<>").strip()


def normalize_references(raw_references):
    """
    Normalize a References header string.

    Input:  "<id1@host> <id2@host>  <id3@host>"
    Output: "id1@host id2@host id3@host"

    Handles angle brackets, extra whitespace, and empty input.
    """
    if not raw_references:
        return ""
    ids = raw_references.split()
    return " ".join(normalize_message_id(rid) for rid in ids if rid.strip())


# ---------------------------------------------------------------------------
# RFC 2822 header extraction (with folding support)
# ---------------------------------------------------------------------------


def extract_header(raw_headers, header_name):
    """
    Extract a specific header value from raw email headers.

    Handles RFC 2822 header folding (continuation lines that start with
    whitespace are appended to the previous header).

    Returns the header value with angle brackets stripped, or "" if not found.
    """
    if not raw_headers:
        return ""

    # Unfold headers: join lines starting with whitespace to previous line
    unfolded = re.sub(r"\r?\n([ \t]+)", r" ", raw_headers)

    target = header_name.lower() + ":"
    for line in unfolded.split("\n"):
        if line.lower().startswith(target):
            value = line.split(":", 1)[1].strip()
            return normalize_message_id(value)
    return ""


# ---------------------------------------------------------------------------
# Sender parsing
# ---------------------------------------------------------------------------


_SENDER_RE = re.compile(r"^(.+?)\s*<(.+?)>$")


def parse_sender(sender_raw):
    """
    Parse sender from "Display Name <email@example.com>" format.

    Returns (name, email). If no angle brackets, treats the whole
    string as a bare email address.
    """
    if not sender_raw:
        return "", ""
    match = _SENDER_RE.match(sender_raw.strip())
    if match:
        return match.group(1).strip().strip('"'), match.group(2).strip()
    email = sender_raw.strip().strip("<>")
    return "", email


# ---------------------------------------------------------------------------
# Quoted reply stripping
# ---------------------------------------------------------------------------

# Patterns that indicate the start of a quoted section.
# Order matters: first match wins and truncates the body.
_QUOTE_DELIMITERS = [
    # "On Mon, Jan 1, 2025, User wrote:" (Gmail, Apple Mail)
    re.compile(r"^on .+wrote:\s*$", re.IGNORECASE),
    # "On {date}, at {time}, {name} wrote:" (Apple Mail variant)
    re.compile(r"^on .+, at .+, .+ wrote:\s*$", re.IGNORECASE),
    # "--- Original Message ---" or "--- Forwarded message ---"
    re.compile(r"^-{3,}\s*(original|forwarded)\s+(message|mail)\s*-{3,}", re.IGNORECASE),
    # "---------- Forwarded message ----------" (Gmail)
    re.compile(r"^-{5,}\s*forwarded message\s*-{5,}", re.IGNORECASE),
    # Outlook-style header block: "From: ... Sent: ..."
    re.compile(r"^from:\s+.+$", re.IGNORECASE),
    # Underscores separator (some enterprise clients)
    re.compile(r"^_{5,}\s*$"),
]


def strip_quoted_reply(body_text):
    """
    Strip the quoted reply portion from an email body.

    Handles:
    - Lines starting with ">" (inline quoting)
    - "On ... wrote:" blocks (Gmail, Apple Mail)
    - "--- Original Message ---" separators
    - "---------- Forwarded message ----------" (Gmail)
    - Outlook header blocks ("From: ... Sent: ...")
    - Underscore separators

    Returns the new content only, with trailing whitespace removed.
    """
    if not body_text:
        return ""

    lines = body_text.split("\n")
    result = []

    for line in lines:
        stripped = line.strip()

        # Skip individual quoted lines (> prefix)
        if stripped.startswith(">"):
            continue

        # Check if this line starts a quoted block
        is_delimiter = False
        for pattern in _QUOTE_DELIMITERS:
            if pattern.match(stripped):
                is_delimiter = True
                break

        if is_delimiter:
            break

        result.append(line)

    return "\n".join(result).rstrip()

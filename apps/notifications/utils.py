"""
Shared helpers for the notifications layer.

``is_undeliverable_email`` is used by every outbound-email call site to
silently skip addresses that are guaranteed to bounce. This keeps our
own mailbox clean (every bounce round-trips back through IMAP and
lands in the inbox) and avoids wasted retries for obviously-fake
recipients on seeded tenants and test accounts.
"""

# TLDs reserved by RFC 2606 / RFC 6761 for examples, tests, and internal
# names that are not routable on the public internet.
_UNDELIVERABLE_TLDS = frozenset({
    "local",       # mDNS / Bonjour link-local names
    "localhost",
    "test",
    "example",
    "invalid",
})

# Second-level domains reserved for documentation (RFC 2606).
_UNDELIVERABLE_DOMAINS = frozenset({
    "example.com",
    "example.net",
    "example.org",
})


def is_undeliverable_email(address: str | None) -> bool:
    """Return True for addresses we know will never deliver."""
    if not address or "@" not in address:
        return True
    domain = address.rsplit("@", 1)[1].strip().lower()
    if not domain:
        return True
    if domain in _UNDELIVERABLE_DOMAINS:
        return True
    last_label = domain.rsplit(".", 1)[-1]
    return last_label in _UNDELIVERABLE_TLDS

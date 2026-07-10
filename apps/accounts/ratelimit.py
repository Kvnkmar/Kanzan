"""Cache-based rate limiting for the server-rendered auth views.

``login_page`` / ``register_page`` are plain Django views that bypass DRF (and
therefore its throttles), so brute-force and verification-email-flood protection
is enforced here with a Redis-backed counter.

Fail-OPEN on cache errors: a cache outage must never lock every user out of
signing in. The counters are best-effort — approximate limiting is fine.
"""

from django.core.cache import cache

# (limit, window_seconds). A bucket is blocked once its count reaches ``limit``.
LOGIN_IP = (30, 15 * 60)      # 30 failed sign-ins / 15 min / IP
LOGIN_EMAIL = (8, 15 * 60)    # 8 failed sign-ins / 15 min / account
REGISTER_IP = (12, 60 * 60)   # 12 sign-ups / hour / IP (verification-email flood guard)


def client_ip(request):
    """Best-effort client IP, honouring a single X-Forwarded-For hop."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def _key(bucket, ident):
    return f"authrl:{bucket}:{ident}"


def is_blocked(bucket, ident, policy):
    """True if (bucket, ident) has already reached its limit within the window."""
    limit, _ = policy
    try:
        return (cache.get(_key(bucket, ident)) or 0) >= limit
    except Exception:
        return False


def record_hit(bucket, ident, policy):
    """Increment the counter for (bucket, ident); return the new count."""
    _, window = policy
    key = _key(bucket, ident)
    try:
        count = (cache.get(key) or 0) + 1
        cache.set(key, count, window)
        return count
    except Exception:
        return 0


def reset(bucket, ident):
    """Clear a bucket (e.g. after a successful sign-in)."""
    try:
        cache.delete(_key(bucket, ident))
    except Exception:
        pass

"""Health/readiness probes for load balancers and orchestrators.

These endpoints are dependency-light, unauthenticated, and tenant-agnostic
(exempt from TenantMiddleware + SubscriptionMiddleware), so a probe never needs
a resolvable subdomain or an active subscription.

- ``/healthz/`` — liveness: the process is up and can serve a request. No I/O.
- ``/readyz/``  — readiness: the backing services (DB + cache) are reachable.
                  Returns 503 with a per-check breakdown when one is down, so an
                  orchestrator pulls the instance out of rotation instead of
                  routing traffic to a broken node.
"""

from django.db import connection
from django.core.cache import cache
from django.http import JsonResponse


def liveness(request):
    """Cheap always-200 liveness check (no external dependencies)."""
    return JsonResponse({"status": "ok"})


def readiness(request):
    """Readiness check that verifies DB and cache connectivity."""
    checks = {}
    ok = True

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = "ok"
    except Exception as exc:  # pragma: no cover - failure path
        checks["database"] = f"error: {exc.__class__.__name__}"
        ok = False

    try:
        cache.set("healthcheck:readyz", "1", 5)
        checks["cache"] = "ok" if cache.get("healthcheck:readyz") == "1" else "error: miss"
        ok = ok and checks["cache"] == "ok"
    except Exception as exc:  # pragma: no cover - failure path
        checks["cache"] = f"error: {exc.__class__.__name__}"
        ok = False

    return JsonResponse(
        {"status": "ok" if ok else "unavailable", "checks": checks},
        status=200 if ok else 503,
    )

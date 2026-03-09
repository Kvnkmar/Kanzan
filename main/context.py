"""
Async-safe tenant context for row-level multi-tenancy.

Uses ``contextvars.ContextVar`` instead of ``threading.local`` so the
tenant context propagates correctly in async views, Channels consumers,
and any code running under ``asyncio``.

Usage:
    from main.context import get_current_tenant, set_current_tenant

The TenantMiddleware sets the current tenant on each request.
The TenantAwareManager uses it to auto-filter querysets.
"""

import contextvars

_current_tenant: contextvars.ContextVar = contextvars.ContextVar(
    "current_tenant", default=None
)


def set_current_tenant(tenant):
    """Set the current tenant in context-local storage."""
    _current_tenant.set(tenant)


def get_current_tenant():
    """Get the current tenant from context-local storage."""
    return _current_tenant.get()


def clear_current_tenant():
    """Clear the current tenant from context-local storage."""
    _current_tenant.set(None)

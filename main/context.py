"""
Async-safe tenant context for row-level multi-tenancy.

Uses ``contextvars.ContextVar`` instead of ``threading.local`` so the
tenant context propagates correctly in async views, Channels consumers,
and any code running under ``asyncio``.

Usage:
    from main.context import get_current_tenant, set_current_tenant

    # In Celery tasks or service functions, use the context manager
    # to guarantee cleanup even on exceptions:
    from main.context import tenant_context
    with tenant_context(tenant):
        ...  # all tenant-scoped queries work here

The TenantMiddleware sets the current tenant on each request.
The TenantAwareManager uses it to auto-filter querysets.
"""

import contextvars
from contextlib import contextmanager

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


@contextmanager
def tenant_context(tenant):
    """
    Context manager that sets and clears tenant context.

    Use in Celery tasks and service functions to guarantee the tenant
    context is always cleaned up, preventing leakage across tasks that
    share the same worker thread/process.

    Usage::

        with tenant_context(tenant):
            # TenantAwareManager auto-filters to this tenant
            tickets = Ticket.objects.all()
        # tenant context is cleared here, even if an exception occurred
    """
    previous = _current_tenant.get()
    _current_tenant.set(tenant)
    try:
        yield tenant
    finally:
        _current_tenant.set(previous)

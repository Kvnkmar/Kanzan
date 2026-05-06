"""
Global-logout enforcement.

Each host (bare domain + every tenant subdomain) stores its own
Django session because browsers refuse to share cookies across the
``.localhost`` pseudo-TLD. That means a plain ``logout(request)`` only
kills the session on the host it was invoked from — every OTHER live
session for that user on other hosts would stay valid.

``SessionVersionMiddleware`` closes that gap. The project login views
stamp ``User.auth_version`` into the session at login time; sessions
from login paths that don't stamp (e.g. Django admin) are adopted on
their first authenticated request. The logout view bumps
``User.auth_version``. This middleware runs on every subsequent
authenticated request and invalidates the session whenever the stored
version is older than the user's current version — i.e. a global
logout happened somewhere else.
"""

from django.contrib.auth import logout

SESSION_AUTH_VERSION_KEY = "auth_version"


class SessionVersionMiddleware:
    """Revoke sessions whose stamped auth_version is below the user's current version."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            stamped = request.session.get(SESSION_AUTH_VERSION_KEY)
            current = getattr(user, "auth_version", None)
            if current is not None:
                if stamped is None:
                    # Fresh session from a login path that didn't stamp
                    # (e.g. Django admin's stock login view). Adopt the
                    # current version rather than logging the user out.
                    request.session[SESSION_AUTH_VERSION_KEY] = current
                elif stamped < current:
                    # Stale stamp: a global logout elsewhere bumped the
                    # user's auth_version. Revoke this session.
                    logout(request)
        return self.get_response(request)

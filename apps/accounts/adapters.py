"""django-allauth adapters.

The application performs its own email/password registration through
``apps.tenants.frontend_views.register_page`` and ``AuthViewSet.register`` —
both of which deliberately create the account **inactive** and only activate it
after the email-verification link is followed.

allauth is included only for *social* login (Google / Microsoft / OIDC). Its
built-in local signup view (`/accounts/signup/`) is a second, weaker door: with
``ACCOUNT_EMAIL_VERIFICATION="optional"`` it creates an immediately-active,
logged-in account without proving email ownership — re-opening the exact bypass
the first-party paths were written to close.

``NoLocalSignupAccountAdapter.is_open_for_signup`` returns False so allauth's
local signup is refused. Social signup is governed by the *social* account
adapter and is unaffected, so SSO continues to work.
"""

from allauth.account.adapter import DefaultAccountAdapter


class NoLocalSignupAccountAdapter(DefaultAccountAdapter):
    """Disable allauth's built-in email/password signup.

    Local registration must go through the app's own verified-signup flow;
    allauth remains mounted only for social authentication.
    """

    def is_open_for_signup(self, request):
        return False

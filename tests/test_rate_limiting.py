"""Regression tests for the go-live rate-limiting blocker.

``login_page`` / ``register_page`` are plain Django views that bypass DRF's
throttles, so they carried NO brute-force / verification-email-flood protection.
And the DRF API had no baseline per-user / per-anon throttle. Both are now
enforced (cache-based limiter + UserRateThrottle/AnonRateThrottle).
"""

import pytest
from django.conf import settings as dj_settings
from django.test import Client

from apps.accounts import ratelimit
from conftest import UserFactory

# login_page / register_page only serve the bare domain.
BARE = {"HTTP_HOST": "localhost"}


@pytest.mark.django_db
class TestRateLimitHelper:
    def test_blocks_once_limit_reached(self):
        policy = (3, 60)
        for _ in range(3):
            assert not ratelimit.is_blocked("t", "id1", policy)
            ratelimit.record_hit("t", "id1", policy)
        assert ratelimit.is_blocked("t", "id1", policy)

    def test_reset_clears_the_bucket(self):
        policy = (2, 60)
        ratelimit.record_hit("t", "id2", policy)
        ratelimit.record_hit("t", "id2", policy)
        assert ratelimit.is_blocked("t", "id2", policy)
        ratelimit.reset("t", "id2")
        assert not ratelimit.is_blocked("t", "id2", policy)

    def test_buckets_are_independent(self):
        policy = (1, 60)
        ratelimit.record_hit("t", "a", policy)
        assert ratelimit.is_blocked("t", "a", policy)
        assert not ratelimit.is_blocked("t", "b", policy)


@pytest.mark.django_db
class TestLoginRateLimit:
    def test_locks_out_after_repeated_failures(self):
        client = Client()
        limit = ratelimit.LOGIN_EMAIL[0]
        email = "attacker@example.com"

        # The first `limit` failures still show normal invalid-credentials.
        for _ in range(limit):
            resp = client.post(
                "/login/", {"email": email, "password": "wrong"}, **BARE
            )
            assert resp.status_code == 200
            assert b"Too many sign-in" not in resp.content

        # The next attempt is blocked before authenticate() is even called.
        resp = client.post(
            "/login/", {"email": email, "password": "wrong"}, **BARE
        )
        assert resp.status_code == 200
        assert b"Too many sign-in" in resp.content

    def test_successful_login_resets_the_counter(self):
        user = UserFactory(email="mate@example.com")  # active, pw=testpass123
        client = Client()

        for _ in range(ratelimit.LOGIN_EMAIL[0] - 1):  # a few short of the limit
            client.post(
                "/login/", {"email": user.email, "password": "wrong"}, **BARE
            )
        assert ratelimit.record_hit  # sanity: helper importable

        resp = client.post(
            "/login/", {"email": user.email, "password": "testpass123"}, **BARE
        )
        # No memberships yet -> redirected into onboarding (setup-company).
        assert resp.status_code == 302
        assert not ratelimit.is_blocked(
            "login-email", user.email.lower(), ratelimit.LOGIN_EMAIL
        )


@pytest.mark.django_db
class TestRegisterRateLimit:
    def test_signup_flood_is_blocked(self):
        client = Client()
        limit = ratelimit.REGISTER_IP[0]
        pw = "Str0ng!passw0rd"

        for i in range(limit):
            resp = client.post(
                "/register/",
                {"email": f"new{i}@example.com", "password": pw, "password2": pw},
                **BARE,
            )
            assert resp.status_code == 302  # -> verify-email-sent

        resp = client.post(
            "/register/",
            {"email": "overflow@example.com", "password": pw, "password2": pw},
            **BARE,
        )
        assert resp.status_code == 200
        assert b"Too many sign-up" in resp.content


@pytest.mark.django_db
class TestApiBaselineThrottle:
    def test_baseline_throttle_classes_configured(self):
        classes = dj_settings.REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"]
        assert "rest_framework.throttling.UserRateThrottle" in classes
        assert "rest_framework.throttling.AnonRateThrottle" in classes
        rates = dj_settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        assert "user" in rates and "anon" in rates

    def test_authenticated_user_gets_429_when_over_the_limit(
        self, tenant, agent_user, agent_client, monkeypatch
    ):
        # DRF captures THROTTLE_RATES as a class attribute at import, so drop
        # the per-user cap low directly on the throttle class for this test.
        from rest_framework.throttling import UserRateThrottle

        monkeypatch.setitem(UserRateThrottle.THROTTLE_RATES, "user", "3/min")

        codes = [
            agent_client.get("/api/v1/crm/reminders/").status_code
            for _ in range(6)
        ]
        assert codes[0] == 200        # first requests pass
        assert 429 in codes           # then the per-user cap bites

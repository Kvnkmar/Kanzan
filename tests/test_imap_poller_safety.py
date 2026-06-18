"""
Regression tests for the IMAP poller's "never backfill" safety guarantee.

Context: the support mailbox (kvnkmar012@gmail.com) is shared with a human
operator who receives personal/third-party mail we must never ingest. A
previous version of ``_read_uidnext`` returned ``1`` when UIDNEXT couldn't
be parsed, which combined with an unreadable UIDVALIDITY to make
``UID 1:*`` match every message in the inbox. These tests pin the fix:
when the poller cannot establish a safe watermark, it aborts rather than
backfilling.
"""

from unittest.mock import MagicMock, patch

import pytest

from apps.inbound_email import imap_poller
from apps.inbound_email.models import IMAPPollState, InboundEmail


class _FakeIMAP:
    """Minimal stand-in for imaplib.IMAP4_SSL with injectable responses."""

    def __init__(self, untagged_responses=None, select_resp=(b"OK",)):
        self.untagged_responses = untagged_responses or {}
        self._select_resp = select_resp
        self.uid_calls = []
        self.closed = False
        self.logged_out = False

    def login(self, user, password):
        return ("OK", [b"Logged in"])

    def select(self, mailbox, readonly=False):
        return ("OK", list(self._select_resp))

    def uid(self, command, *args):
        self.uid_calls.append((command, args))
        if command == "SEARCH":
            return ("OK", [b""])
        return ("OK", [])

    def close(self):
        self.closed = True

    def logout(self):
        self.logged_out = True


@pytest.fixture
def imap_settings(settings):
    settings.IMAP_HOST = "imap.gmail.com"
    settings.IMAP_USER = "kvnkmar012@gmail.com"
    settings.IMAP_PASSWORD = "shhh"
    settings.IMAP_PORT = 993
    settings.IMAP_USE_SSL = True
    settings.IMAP_MAILBOX = "INBOX"


@pytest.mark.django_db
class TestNoBackfillSafety:
    def test_aborts_when_uidvalidity_unreadable(self, imap_settings):
        """No UIDVALIDITY in any response → poll returns 0, no state row created."""
        fake = _FakeIMAP(untagged_responses={})  # empty: UIDVALIDITY nowhere

        with patch.object(imap_poller, "_connect", return_value=fake):
            result = imap_poller.poll_once()

        assert result == 0
        assert IMAPPollState.objects.count() == 0
        # Critically, SEARCH was NEVER issued — we didn't risk UID 1:*.
        assert all(cmd != "SEARCH" for cmd, _ in fake.uid_calls)
        assert InboundEmail.objects.count() == 0

    def test_aborts_on_first_run_when_uidnext_unreadable(self, imap_settings):
        """UIDVALIDITY present but UIDNEXT missing → first-run aborts, no search."""
        fake = _FakeIMAP(
            untagged_responses={
                "UIDVALIDITY": [b"1234567890"],
                # Deliberately no UIDNEXT, no OK with [UIDNEXT ...]
                "OK": [b"[UIDVALIDITY 1234567890] UIDs valid."],
            }
        )

        with patch.object(imap_poller, "_connect", return_value=fake):
            result = imap_poller.poll_once()

        assert result == 0
        # State row exists (created by _load_state) but watermark stayed at 0.
        state = IMAPPollState.objects.get()
        assert state.uid_validity == 1234567890
        assert state.last_uid == 0
        # Again — no SEARCH was issued. The old code would have run UID 1:*.
        assert all(cmd != "SEARCH" for cmd, _ in fake.uid_calls)

    def test_happy_path_anchors_watermark_without_backfill(self, imap_settings):
        """Both codes readable → watermark anchored at UIDNEXT-1, no messages fetched."""
        fake = _FakeIMAP(
            untagged_responses={
                "UIDVALIDITY": [b"1234567890"],
                "UIDNEXT": [b"372"],
                "OK": [
                    b"[UIDVALIDITY 1234567890] UIDs valid.",
                    b"[UIDNEXT 372] Predicted next UID.",
                ],
            }
        )

        with patch.object(imap_poller, "_connect", return_value=fake):
            result = imap_poller.poll_once()

        assert result == 0
        state = IMAPPollState.objects.get()
        assert state.uid_validity == 1234567890
        assert state.last_uid == 371  # UIDNEXT - 1: historical mail skipped
        assert InboundEmail.objects.count() == 0

    def test_recovers_from_uidvalidity_only_in_ok_response(self, imap_settings):
        """imaplib versions that leave codes inside ``OK`` still parse correctly."""
        fake = _FakeIMAP(
            untagged_responses={
                # Neither UIDVALIDITY nor UIDNEXT as top-level keys.
                "OK": [
                    b"[UIDVALIDITY 1234567890] UIDs valid.",
                    b"[UIDNEXT 372] Predicted next UID.",
                ],
            }
        )

        with patch.object(imap_poller, "_connect", return_value=fake):
            result = imap_poller.poll_once()

        assert result == 0
        state = IMAPPollState.objects.get()
        assert state.uid_validity == 1234567890
        assert state.last_uid == 371

    def test_ambient_digits_do_not_fool_reader(self, imap_settings):
        """
        The old implementation extracted ALL digits from untagged bytes, so
        text like ``UIDs valid.`` next to a stray ``1`` could be misread as
        UIDVALIDITY=1. The new reader requires a bracketed ``[NAME N]`` or a
        bare numeric value — ambient prose must not match.
        """
        fake = _FakeIMAP(
            untagged_responses={
                "UIDVALIDITY": [b"UIDs valid in mailbox 1."],
                "UIDNEXT": [b"predicted 1 next"],
            }
        )

        with patch.object(imap_poller, "_connect", return_value=fake):
            result = imap_poller.poll_once()

        # Both codes are unparseable → must abort, not return 1.
        assert result == 0
        assert IMAPPollState.objects.count() == 0


class _FetchIMAP(_FakeIMAP):
    """Fake IMAP that returns a fixed RFC822 body for UID FETCH."""

    def __init__(self, raw_bytes):
        super().__init__()
        self._raw = raw_bytes

    def uid(self, command, *args):
        self.uid_calls.append((command, args))
        if command == "FETCH":
            return ("OK", [(b"1 (RFC822 {N}", self._raw), b")"])
        return ("OK", [])


def _raw_message(slug, message_id):
    return (
        f"From: Customer <cust@example.com>\r\n"
        f"To: support+{slug}@kanzen.io\r\n"
        f"Subject: Shared mailing-list mail\r\n"
        f"Message-ID: <{message_id}>\r\n"
        f"\r\n"
        f"Body here\r\n"
    ).encode()


@pytest.mark.django_db
class TestCrossTenantDedup:
    """Regression: IMAP dedup must be scoped per-tenant (inbound-email-1).

    Two tenants can legitimately receive the same Message-ID (mailing lists,
    forwards); a global dedup silently dropped the second tenant's copy.
    """

    def _make_tenant(self, slug):
        from apps.tenants.models import Tenant

        return Tenant.objects.create(name=slug.title(), slug=slug, is_active=True)

    def test_same_message_id_ingests_for_each_tenant(self, imap_settings):
        from unittest.mock import patch as _patch

        tenant_a = self._make_tenant("alpha")
        tenant_b = self._make_tenant("beta")
        mid = "shared-list-123@mailinglist"

        # Tenant A already ingested this Message-ID.
        InboundEmail.objects.create(
            tenant=tenant_a,
            message_id=mid,
            recipient_email="support+alpha@kanzen.io",
            sender_email="cust@example.com",
            direction=InboundEmail.Direction.INBOUND,
            sender_type=InboundEmail.SenderType.CUSTOMER,
        )

        fake = _FetchIMAP(_raw_message("beta", mid))
        with _patch("apps.inbound_email.tasks.process_inbound_email_task.delay"):
            created = imap_poller._ingest_one(fake, b"1")

        # Tenant B's copy must NOT be skipped by tenant A's row.
        assert created is True
        assert InboundEmail.objects.filter(tenant=tenant_b, message_id=mid).count() == 1
        assert InboundEmail.objects.filter(message_id=mid).count() == 2

    def test_duplicate_within_same_tenant_is_skipped(self, imap_settings):
        from unittest.mock import patch as _patch

        tenant_b = self._make_tenant("beta")
        mid = "dup-456@mailinglist"
        InboundEmail.objects.create(
            tenant=tenant_b,
            message_id=mid,
            recipient_email="support+beta@kanzen.io",
            sender_email="cust@example.com",
            direction=InboundEmail.Direction.INBOUND,
            sender_type=InboundEmail.SenderType.CUSTOMER,
        )

        fake = _FetchIMAP(_raw_message("beta", mid))
        with _patch("apps.inbound_email.tasks.process_inbound_email_task.delay"):
            created = imap_poller._ingest_one(fake, b"1")

        assert created is False
        assert InboundEmail.objects.filter(tenant=tenant_b, message_id=mid).count() == 1


@pytest.mark.django_db
class TestResponseCodeParsing:
    def test_reads_dedicated_key(self):
        fake = _FakeIMAP(untagged_responses={"UIDVALIDITY": [b"42"]})
        assert imap_poller._read_uidvalidity(fake, "INBOX") == 42

    def test_reads_bracketed_from_ok_response(self):
        fake = _FakeIMAP(
            untagged_responses={"OK": [b"[UIDNEXT 123] Predicted next UID"]}
        )
        assert imap_poller._read_uidnext(fake, "INBOX") == 123

    def test_returns_none_when_absent(self):
        fake = _FakeIMAP(untagged_responses={"EXISTS": [b"5"]})
        assert imap_poller._read_uidvalidity(fake, "INBOX") is None
        assert imap_poller._read_uidnext(fake, "INBOX") is None

    def test_falls_back_to_select_response(self):
        fake = _FakeIMAP(untagged_responses={})
        select_resp = [b"[UIDVALIDITY 999] hello", b"OK"]
        assert imap_poller._read_uidvalidity(fake, "INBOX", select_resp) == 999

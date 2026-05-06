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

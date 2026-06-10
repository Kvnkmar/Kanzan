"""Inbox Hub routing engine.

Maps a parked :class:`~apps.inbox_hub.models.HubEmail` to a Department + Queue
(and optional category/priority) by consulting the tenant's ordered
:class:`~apps.inbox_hub.models.RoutingRule` set, falling back to the tenant's
configured default department when nothing matches.

Match semantics (per RoutingRule.match JSON):
    {
        "sender_domain":   ["acme.com"],      # sender's email domain
        "recipient_local": ["billing"],       # local-part the mail was sent to
        "keyword":         ["invoice"],       # substring in subject OR body
        "subject_regex":   "^\\[urgent\\]",   # regex against the subject
    }
Keys present in a rule are AND-combined; values within a key are OR-combined.
An empty/absent ``match`` matches nothing (so it can never become an accidental
catch-all — the default-department fallback owns the "everything else" case).

Called from :func:`apps.inbox_hub.services._post_park_hooks` on commit. Uses
``unscoped`` + explicit ``tenant=`` filters so it is correct regardless of
whether a tenant context is bound at call time.
"""

import logging
import re
from collections import namedtuple

from django.db import transaction

from apps.comments.models import ActivityLog
from apps.inbox_hub.models import Department, RoutingRule
from apps.tenants.live import broadcast_live_event

logger = logging.getLogger(__name__)

RoutingDecision = namedtuple(
    "RoutingDecision",
    ["department", "queue", "category", "priority", "matched_rule"],
)


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def _rule_matches(rule, *, sender_email, recipient_email, subject, body):
    """Return True when every present key in ``rule.match`` is satisfied."""
    match = rule.match or {}
    if not isinstance(match, dict) or not match:
        return False

    sender_domain = sender_email.split("@")[-1].lower() if "@" in sender_email else ""
    recipient_local = recipient_email.split("@")[0].lower() if "@" in recipient_email else ""
    haystack = f"{subject}\n{body}".lower()

    if "sender_domain" in match:
        domains = [d.lower().lstrip("@") for d in _as_list(match["sender_domain"])]
        if not any(
            sender_domain == d or sender_domain.endswith("." + d)
            for d in domains if d
        ):
            return False

    if "recipient_local" in match:
        locals_ = [r.lower() for r in _as_list(match["recipient_local"])]
        if recipient_local not in locals_:
            return False

    if "keyword" in match:
        keywords = [k.lower() for k in _as_list(match["keyword"]) if k]
        if not any(k in haystack for k in keywords):
            return False

    if match.get("subject_regex"):
        try:
            if not re.search(match["subject_regex"], subject or "", re.IGNORECASE):
                return False
        except re.error:
            logger.warning("RoutingRule %s has an invalid subject_regex; skipping that clause.", rule.pk)
            return False

    return True


class RoutingEngine:
    """Stateless façade over the rule-evaluation + persistence flow."""

    @staticmethod
    def classify_and_route(hub_email):
        """Resolve and persist department/queue/category/priority for ``hub_email``.

        Returns the :class:`RoutingDecision`. Idempotent enough to re-run: it
        always recomputes from the rules and overwrites the routing fields.
        State is left untouched (routing never changes ``state``; assignment
        does). Writes EMAIL_CATEGORISED + EMAIL_QUEUED audit rows and broadcasts
        ``hub_email.transitioned`` on commit.
        """
        decision = RoutingEngine.evaluate(hub_email)
        RoutingEngine._apply(hub_email, decision)
        return decision

    @staticmethod
    def evaluate(hub_email):
        """Pure decision step — compute the RoutingDecision without persisting."""
        tenant = hub_email.tenant
        inbound = hub_email.inbound

        sender_email = (inbound.sender_email or "").strip()
        recipient_email = (inbound.recipient_email or "").strip()
        subject = inbound.subject or ""
        body = inbound.body_text or ""

        rules = (
            RoutingRule.unscoped
            .filter(tenant=tenant, is_active=True)
            .order_by("order", "id")
        )

        department = None
        queue = None
        category = ""
        priority = None
        matched_rule = None

        for rule in rules:
            if not _rule_matches(
                rule,
                sender_email=sender_email,
                recipient_email=recipient_email,
                subject=subject,
                body=body,
            ):
                continue
            matched_rule = rule
            if rule.department_id:
                department = rule.department
            if rule.queue_id:
                queue = rule.queue
            if rule.category:
                category = rule.category
            if rule.priority:
                priority = rule.priority
            if rule.stop_on_match:
                break

        # Fallback department when no rule resolved one.
        if department is None:
            department = RoutingEngine._default_department(tenant)

        # Resolve queue from the department default when a rule didn't pin one.
        if queue is None and department is not None and department.default_queue_id:
            queue = department.default_queue

        return RoutingDecision(
            department=department,
            queue=queue,
            category=category,
            priority=priority,
            matched_rule=matched_rule,
        )

    @staticmethod
    def _default_department(tenant):
        settings_obj = getattr(tenant, "settings", None)
        dept = getattr(settings_obj, "inbox_hub_default_department", None) if settings_obj else None
        if dept is not None and getattr(dept, "is_active", True):
            return dept
        # Last resort: a single active department (common small-tenant case).
        active = list(Department.unscoped.filter(tenant=tenant, is_active=True)[:2])
        return active[0] if len(active) == 1 else None

    @staticmethod
    def _apply(hub_email, decision):
        from apps.inbox_hub.services import _hub_email_payload, _write_activity_log

        update_fields = []
        if decision.department is not None and hub_email.department_id != decision.department.id:
            hub_email.department = decision.department
            update_fields.append("department")
        if decision.queue is not None and hub_email.queue_id != decision.queue.id:
            hub_email.queue = decision.queue
            update_fields.append("queue")
        if decision.category and hub_email.category != decision.category:
            hub_email.category = decision.category
            update_fields.append("category")
        if decision.priority and hub_email.priority != decision.priority:
            hub_email.priority = decision.priority
            update_fields.append("priority")

        if not update_fields:
            logger.debug("RoutingEngine: no routing changes for HubEmail %s.", hub_email.pk)
            return

        update_fields.append("updated_at")
        hub_email.save(update_fields=update_fields)

        dept_label = decision.department.name if decision.department else "(none)"
        queue_label = decision.queue.name if decision.queue else "(none)"
        rule_note = f" via rule '{decision.matched_rule.name}'" if decision.matched_rule else " (default)"

        _write_activity_log(
            hub_email=hub_email,
            actor=None,
            action=ActivityLog.Action.EMAIL_CATEGORISED,
            description=f"Routed to {dept_label}{rule_note}",
            changes={
                "department": str(decision.department.id) if decision.department else None,
                "category": decision.category or "",
                "priority": decision.priority or "",
                "rule": str(decision.matched_rule.id) if decision.matched_rule else None,
            },
        )
        if "queue" in update_fields:
            _write_activity_log(
                hub_email=hub_email,
                actor=None,
                action=ActivityLog.Action.EMAIL_QUEUED,
                description=f"Queued in {queue_label}",
                changes={"queue": str(decision.queue.id) if decision.queue else None},
            )

        payload = _hub_email_payload(hub_email)
        payload["department_id"] = str(hub_email.department_id) if hub_email.department_id else None
        broadcast_live_event(
            tenant=hub_email.tenant,
            event="hub_email.transitioned",
            payload=payload,
        )

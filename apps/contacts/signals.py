"""Signal handlers that broadcast Contacts changes onto the live channel.

Drives real-time updates for the CRM list/detail pages: new contact,
edits, archives, group membership shifts, and lead-score recalculations
all surface immediately to every connected client in the tenant.

We intentionally skip ``ContactEvent`` rows -- they're a high-frequency
append-only audit stream; broadcasting each one would flood the channel.
A page that needs to react to those still gets a ``contact.updated`` from
the ``Contact.last_activity_at`` save that the event-recorder triggers.
"""

from __future__ import annotations

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.contacts.models import Account, Company, Contact, ContactGroup
from apps.tenants.live import broadcast_live_event


def _serialise_contact(c: Contact) -> dict:
    full_name = f"{c.first_name or ''} {c.last_name or ''}".strip() or c.email
    return {
        "id":             str(c.id),
        "first_name":     c.first_name,
        "last_name":      c.last_name,
        "full_name":      full_name,
        "email":          c.email,
        "phone":          c.phone,
        "company_id":     str(c.company_id) if c.company_id else None,
        "account_id":     str(c.account_id) if c.account_id else None,
        "job_title":      c.job_title,
        "is_active":      c.is_active,
        "email_bouncing": c.email_bouncing,
        "lead_score":     c.lead_score,
        "last_activity_at": c.last_activity_at.isoformat() if c.last_activity_at else None,
        "updated_at":     c.updated_at.isoformat() if c.updated_at else None,
    }


def _serialise_company(co: Company) -> dict:
    return {
        "id":            str(co.id),
        "name":          co.name,
        "industry":      getattr(co, "industry", None),
        "size":          getattr(co, "size", None),
        "is_active":     getattr(co, "is_active", True),
        "updated_at":    co.updated_at.isoformat() if co.updated_at else None,
    }


def _serialise_account(a: Account) -> dict:
    return {
        "id":            str(a.id),
        "name":          a.name,
        "mrr":           str(getattr(a, "mrr", "") or ""),
        "health_score":  getattr(a, "health_score", None),
        "is_active":     getattr(a, "is_active", True),
        "updated_at":    a.updated_at.isoformat() if a.updated_at else None,
    }


def _serialise_group(g: ContactGroup) -> dict:
    return {
        "id":   str(g.id),
        "name": g.name,
    }


# ── Contact ────────────────────────────────────────────────────────────

@receiver(post_save, sender=Contact)
def broadcast_contact_save(sender, instance, created, **kwargs):
    broadcast_live_event(
        tenant=instance.tenant,
        event="contact.created" if created else "contact.updated",
        payload=_serialise_contact(instance),
    )


@receiver(post_delete, sender=Contact)
def broadcast_contact_delete(sender, instance, **kwargs):
    broadcast_live_event(
        tenant=instance.tenant,
        event="contact.deleted",
        payload={"id": str(instance.id)},
    )


# ── Company ────────────────────────────────────────────────────────────

@receiver(post_save, sender=Company)
def broadcast_company_save(sender, instance, created, **kwargs):
    broadcast_live_event(
        tenant=instance.tenant,
        event="company.created" if created else "company.updated",
        payload=_serialise_company(instance),
    )


@receiver(post_delete, sender=Company)
def broadcast_company_delete(sender, instance, **kwargs):
    broadcast_live_event(
        tenant=instance.tenant,
        event="company.deleted",
        payload={"id": str(instance.id)},
    )


# ── Account ────────────────────────────────────────────────────────────

@receiver(post_save, sender=Account)
def broadcast_account_save(sender, instance, created, **kwargs):
    broadcast_live_event(
        tenant=instance.tenant,
        event="account.created" if created else "account.updated",
        payload=_serialise_account(instance),
    )


@receiver(post_delete, sender=Account)
def broadcast_account_delete(sender, instance, **kwargs):
    broadcast_live_event(
        tenant=instance.tenant,
        event="account.deleted",
        payload={"id": str(instance.id)},
    )


# ── ContactGroup ───────────────────────────────────────────────────────

@receiver(post_save, sender=ContactGroup)
def broadcast_contactgroup_save(sender, instance, created, **kwargs):
    broadcast_live_event(
        tenant=instance.tenant,
        event="contact_group.created" if created else "contact_group.updated",
        payload=_serialise_group(instance),
    )


@receiver(post_delete, sender=ContactGroup)
def broadcast_contactgroup_delete(sender, instance, **kwargs):
    broadcast_live_event(
        tenant=instance.tenant,
        event="contact_group.deleted",
        payload={"id": str(instance.id)},
    )

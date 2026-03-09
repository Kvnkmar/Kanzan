import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.accounts.models import Profile, TenantMembership

logger = logging.getLogger(__name__)


@receiver(post_save, sender=TenantMembership)
def create_profile_on_membership(sender, instance, created, **kwargs):
    """
    Automatically create a tenant-scoped Profile for the user when a
    TenantMembership is created, if one does not already exist.
    """
    if created:
        profile, was_created = Profile.objects.get_or_create(
            user=instance.user,
            tenant=instance.tenant,
        )
        if was_created:
            logger.info(
                "Auto-created profile for user %s in tenant %s.",
                instance.user.email,
                instance.tenant,
            )

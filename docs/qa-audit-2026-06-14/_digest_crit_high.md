
==========================================================================================
## Critical — 19 raw
==========================================================================================

### [tenant-isolation-1] Cross-Tenant Message-ID Deduplication Bypass in IMAP Poller  (DISMISSED(fp))
- category: Data Integrity | confidence: high | dimension: Multi-tenancy & tenant isolation
- location: /home/kavin/crm/apps/inbound_email/imap_poller.py:337
- description: The IMAP poller checks for duplicate message_ids using `InboundEmail.objects.filter(message_id=message_id).exists()` without including a `tenant=` filter. Since InboundEmail is NOT a TenantScopedModel, it uses a plain Django manager without automatic tenant scoping. This allows an attacker to craft an email with a message_id from one tenant and have it accepted by another tenant, bypassing deduplication and potentially creating multiple tickets from the same email across tenants.
- expected: Deduplication should be scoped to the current tenant. The check should be `InboundEmail.objects.filter(tenant=tenant, message_id=message_id).exists()` where tenant is derived during the IMAP ingest process.
- actual: The dedup check is cross-tenant: `InboundEmail.objects.filter(message_id=message_id).exists()` at line 337. The tenant is resolved AFTER InboundEmail creation in the async processing task, leaving a window where the check doesn't know which tenant owns the mailbox.
- fix: Move tenant resolution to the IMAP ingestion layer (before creating InboundEmail), or embed the tenant in the IMAP poll state so each mailbox is bound to a specific tenant. Then update line 337 to: `if InboundEmail.objects.filter(tenant=tenant, message_id=message_id).exists():`
- VERDICT: refuted (High) — The finding's severity claim and attack scenario are incorrect. Analysis of /home/kavin/crm/apps/inbound_email/imap_poller.py:337 and services.py:307-331 reveals a real bug, but not the one described.

**Actual behavior:** InboundEmail.objects.filter(message_id=message_id).exists() at line 337 checks GLOBALLY (no tenant filter) because InboundEmail is a TimestampedModel (plain Manager) not a Te

### [tenant-isolation-2] Missing Tenant Context in InboundEmail Creation (IMAP & SMTP)  (DISMISSED(fp))
- category: Data Integrity | confidence: high | dimension: Multi-tenancy & tenant isolation
- location: /home/kavin/crm/apps/inbound_email/imap_poller.py:360, apps/inbound_email/smtp_server.py:113
- description: InboundEmail.objects.create() calls in both the IMAP poller and SMTP server do not set the tenant field at creation time. The tenant is resolved later in the async processing task (services.py line 307-318). Until that happens, the InboundEmail row has tenant=NULL, making it invisible to tenant-scoped queries. If a crash occurs during this window, or if concurrent access happens before tenant backfill, cross-tenant data could leak or be misattributed. Additionally, the nullable tenant field means some rows can permanently exist without a tenant.
- expected: All InboundEmail rows should have tenant set immediately upon creation, before any async processing begins. The tenant should be resolved during IMAP/SMTP ingestion (synchronously) and passed to create().
- actual: Rows are created with tenant=NULL and only backfilled later in process_inbound_email_task when tenant is resolved from the recipient address. This creates a window where tenant-unscoped queries can see the row.
- fix: Refactor tenant resolution to happen in the IMAP/SMTP ingest layer before creating InboundEmail. Move resolve_tenant_from_address() into both imap_poller.py:_ingest_one() and smtp_server.py:_ingest(). If resolution fails, reject the email at SMTP time (550 response) or skip ingestion at IMAP time. Always pass tenant= to InboundEmail.objects.create().
- VERDICT: refuted (Medium) — The finding accurately identifies that InboundEmail rows are created with tenant=NULL and backfilled later (verified at imap_poller.py:360 and smtp_server.py:113→services.py:318). However, the claimed severity of "Critical" and the risk of "cross-tenant data leakage" is refuted by examining the actual query patterns. 

Key findings:
1. InboundEmail explicitly does NOT inherit from TenantScopedMode

### [tickets-core-1] Missing cross-tenant validation for contact, company, and pipeline_stage in create/update serializers  (OK)
- category: Data Integrity | confidence: high | dimension: Tickets CRUD, serializers, viewset actions
- location: apps/tickets/serializers.py:516-617 (TicketCreateSerializer)
- description: TicketCreateSerializer validates that `status` and `queue` belong to the current tenant (lines 562-582), but provides NO validation for `contact`, `company`, `assignee`, or `pipeline_stage` (all ForeignKey fields to TenantScopedModel resources). An attacker can supply a UUID for contact/company from a different tenant, and the serializer will accept it.
- expected: The serializer should validate that `contact`, `company`, and `pipeline_stage` belong to the current tenant before accepting them, similar to validate_queue (line 572).
- actual: No validation occurs. Attackers can cross-link tickets to other tenants' data.
- fix: Add validate_contact, validate_company, validate_assignee, and validate_pipeline_stage methods checking `value.tenant_id == request.tenant.id`.
- VERDICT: confirmed (High) — I independently read the cited code and verified the vulnerability. The contact, company, and pipeline_stage fields ARE properly protected by TenantAwareManager filtering in their default FK validation (lines 524-526, auto-generated from Meta.fields at lines 530-555, which use the default managers Contact.objects, Company.objects, PipelineStage.objects that are all TenantAwareManager instances). T

### [inbound-email-1] Cross-Tenant Email Deduplication Bypass in IMAP Poller  (OK) [DUP-OF tenant-isolation-1]
- category: Data Integrity | confidence: high | dimension: Inbound email pipeline (SMTP/IMAP)
- location: apps/inbound_email/imap_poller.py:337
- description: The IMAP poller checks for duplicate message_ids using `InboundEmail.objects.filter(message_id=message_id).exists()` without filtering by tenant. Since InboundEmail has a nullable tenant FK and is NOT a TenantScopedModel, this query performs a cross-tenant lookup. If two different tenants both receive an email with the same Message-ID (e.g., from a mailing list or automated system), the second tenant will incorrectly skip ingesting it, thinking it was already processed for a different tenant.
- expected: Each tenant should only check for duplicate Message-IDs within their own scope. An email with the same Message-ID can be legitimately ingested for different tenants.
- actual: The dedup check queries across all tenants, causing cross-tenant email loss when the same message reaches multiple tenants via IMAP.
- fix: Change line 337 to filter by tenant: `if InboundEmail.objects.filter(tenant=None, message_id=message_id).exists():` to only check cross-tenant messages (which lack tenant resolution). For tenant-specific dedup, the dedup should happen in `process_inbound_email` after tenant resolution, or filter by both tenant and message_id here after tenant resolution.
- VERDICT: confirmed (Critical) — The finding is confirmed by examining the actual code. The vulnerability exists in the execution order of the IMAP poller vs the asynchronous tenant resolution:

1. **Line 337 in apps/inbound_email/imap_poller.py** performs a deduplication check: `InboundEmail.objects.filter(message_id=message_id).exists()` - This query has NO tenant filter.

2. **InboundEmail model (apps/inbound_email/models.py:6

### [inbox-hub-engine-1] SLA Response Breach Always Fires (first_responded_at Never Written)  (OK) [DUP-OF inbox-hub-access-3]
- category: Data Integrity | confidence: high | dimension: Inbox Hub engine (routing/assignment/SLA/state)
- location: apps/inbox_hub/tasks.py:49
- description: The response SLA breach detection checks `if not he.response_breached and he.first_responded_at is None`, but `first_responded_at` is a model field that is never written anywhere in the codebase. It exists in the schema but is never populated by any service. This means the condition `he.first_responded_at is None` is always True, causing every response deadline breach to fire without exception. There is no concept of 'marking SLA met' when an agent responds — the response deadline simply expires with no distinction between breached and handled.
- expected: Response breach should only fire when: response deadline expires AND no actual agent response has been recorded (first_responded_at being set by reply/comment activity), OR the field should be documented as unused.
- actual: Response breach always fires on deadline expiration because first_responded_at is always NULL and the condition is never satisfied to prevent firing.
- fix: (1) Implement a mechanism to stamp first_responded_at when an agent replies (via reassign_hub_email or a reply action). (2) Update the breach check to only flag when first_responded_at is truly NULL after the deadline. (3) Alternatively, document the field as read-never-written and remove it from the check (only use response_breached flag as a one-shot). (4) Consider adding a reply/comment action to HubEmail to record agent activity.
- VERDICT: confirmed (High) — The finding is confirmed. Reading /home/kavin/crm/apps/inbox_hub/tasks.py lines 49-58, the condition `if not he.response_breached and he.first_responded_at is None:` checks a field that is never written anywhere in the codebase. Grep confirms zero write sites for `HubEmail.first_responded_at` (appears only in migrations, model definition, and the single read in tasks.py:49). The services.py fil

### [messaging-1] Cross-tenant mention disclosure via global User query without tenant membership validation  (OK)
- category: Security | confidence: high | dimension: Messaging / conversations
- location: apps/messaging/mentions.py:74
- description: notify_mentions() resolves mentioned users from the global User table without validating tenant membership. A user can mention any UUID from any tenant and trigger notifications and ID leakage across tenant boundaries.
- expected: Only query users who are active members of message.conversation.tenant via TenantMembership.
- actual: Line 74: User.objects.filter(id__in=user_ids) with no tenant filter. The tenant parameter passed to notify_mentions() is unused.
- fix: Change line 74 to: User.objects.filter(id__in=user_ids).filter(tenantmembership__tenant=tenant, tenantmembership__is_active=True). Apply same fix in views.py:398 and consumers.py:317.
- VERDICT: confirmed (unchanged) — The vulnerability is real across all three locations. Code review confirms: (1) mentions.py line 74: `User.objects.filter(id__in=user_ids)` with no tenant filter - queries all users globally; (2) views.py line 398: identical pattern in `perform_create`; (3) consumers.py line 317: same issue in `_notify_and_process_mentions`; (4) NotificationConsumer.connect() (consumers.py lines 37-59) has ONLY au

### [messaging-2] Direct message creation with unvalidated cross-tenant user UUIDs  (OK) [DUP-OF messaging-1]
- category: Security | confidence: high | dimension: Messaging / conversations
- location: apps/messaging/serializers.py:337-346 (_create_direct validation)
- description: ConversationCreateSerializer.validate() for DIRECT type only checks self-messaging, not tenant membership. Any user can create DM conversations with users from any other tenant.
- expected: validate() should enforce TenantMembership.objects.filter(user_id=attrs['user_id'], tenant=request.tenant, is_active=True).exists()
- actual: Lines 337-346 only check user_id != request.user.pk. No tenant membership validation occurs before _create_direct() is called.
- fix: Add to DIRECT validation block: 'if not TenantMembership.objects.filter(user_id=attrs["user_id"], tenant=request.tenant, is_active=True).exists(): raise ValidationError({"user_id": "User is not a member of this tenant."})'
- VERDICT: confirmed (unchanged) — The finding is confirmed through direct code inspection:

1. ConversationCreateSerializer.validate() at lines 337-346 only checks (a) user_id is provided and (b) user_id != current user. It does NOT validate TenantMembership (apps/messaging/serializers.py:337-346).

2. The _create_direct() method at line 383 calls ConversationParticipant.objects.bulk_create() at line 411-420 with user_id=other_use

### [messaging-3] Manual group conversation creation with unvalidated cross-tenant user_ids  (OK) [DUP-OF authn-3]
- category: Security | confidence: high | dimension: Messaging / conversations
- location: apps/messaging/serializers.py:482-503 (_create_group manual path)
- description: _create_group() accepts user_ids without validating tenant membership when NOT seeded from UserGroup. Malicious users can mix participants from multiple tenants.
- expected: Validate all user_ids are members of request.tenant before calling bulk_create at line 497.
- actual: Lines 484-503 accept user_ids directly. No TenantMembership check exists in manual path (only in group_id seeded path at line 444).
- fix: After line 483, add: 'if not group_id and participant_ids: valid_ids = set(TenantMembership.objects.filter(user_id__in=participant_ids, tenant=tenant, is_active=True).values_list("user_id", flat=True)); if participant_ids - valid_ids: raise serializers.ValidationError({"user_ids": "Some users are not members of this tenant."})'
- VERDICT: confirmed (High) — CONFIRMED. Code review of apps/messaging/serializers.py:482-503 (_create_group manual path) shows participant_ids are accepted from user input with NO validation that they belong to request.tenant. Lines 442-481 (group_id path) DO validate via UserGroup.objects.get(pk=group_id, tenant=tenant). Lines 497-503 bulk_create ConversationParticipant rows without checking TenantMembership. Contrast: views

### [billing-1] VoIP Plan Flags Never Seeded - All Plans VoIP Disabled  (OK)
- category: Business Logic | confidence: high | dimension: Billing / subscriptions / Stripe
- location: apps/billing/management/commands/seed_plans.py:20-90
- description: Seed command initializes Free Pro Enterprise plans but omits has_voip has_call_recording max_calls_per_month. All fields default to False False NULL. Every tenant using seeded plan has VoIP completely disabled.
- expected: Seeded plans include has_voip=True for Pro and Enterprise, max_calls_per_month set to appropriate values
- actual: All seeded plans have has_voip=False model default, max_calls_per_month=NULL. VoIP feature broken for all new tenants.
- fix: Add has_voip has_call_recording max_calls_per_month to each plan dict in PLANS list. Example: Enterprise should have has_voip=True has_call_recording=True max_calls_per_month=None
- VERDICT: confirmed (Critical) — The claim is definitively confirmed by code inspection. The `seed_plans.py` command (lines 20-90) defines three plan dictionaries that **omit** the keys `has_voip`, `has_call_recording`, and `max_calls_per_month`. When `update_or_create(tier=tier, defaults=plan_data)` is called, these absent keys are not provided, so the Plan model field defaults apply: `has_voip=False` (line 60 of models.py), `ha

### [billing-2] OneToOne Subscription FK Blocks Stripe Plan Upgrades  (OK)
- category: Data Integrity | confidence: high | dimension: Billing / subscriptions / Stripe
- location: apps/billing/webhooks.py:135-138 and apps/billing/models.py:107-111
- description: Subscription model uses OneToOneField to Tenant meaning only one Subscription per Tenant allowed. Webhook handler uses update_or_create on stripe_subscription_id. When tenant upgrades Stripe subscription new subscription with different ID tries to INSERT with same tenant FK violating OneToOne constraint.
- expected: Plan upgrades succeed seamlessly. New subscription cleanly replaces old one without errors.
- actual: Stripe webhook handler fails with IntegrityError. Subscription state becomes inconsistent. Tenant loses access or sees billing errors.
- fix: Change Subscription.tenant from OneToOneField to ForeignKey allowing multiple subscriptions per tenant. Alternatively in webhook handler query by tenant first then DELETE old subscription before creating new one. Or change update_or_create lookup from stripe_subscription_id to tenant.
- VERDICT: confirmed (Critical) — I opened apps/billing/models.py:107-111 and confirmed Subscription.tenant is a OneToOneField, creating a unique database constraint on tenant_id. I opened apps/billing/webhooks.py:135-138 and traced the webhook handler flow: when a tenant upgrades plans in Stripe, the old subscription (sub_abc123) receives a .deleted webhook, which marks it CANCELED but does NOT delete the row or clear its tenant_

### [attachments-1] Missing Object-Level Authorization on Attachment Retrieval  (OK)
- category: Security | confidence: high | dimension: Attachments & file upload/download
- location: apps/attachments/views.py:164, AttachmentViewSet.permission_classes
- description: The AttachmentViewSet uses only IsAuthenticated and IsTenantMember permission classes, with no object-level authorization checks. Both permission classes have has_object_permission() that returns True unconditionally. This allows any authenticated tenant member to download attachments attached to ANY object in the tenant, regardless of whether they have permission to view that object. For example, an agent assigned only to Ticket A can download attachments from Ticket B (which they shouldn't see), or from internal-only comments they don't have access to.
- expected: Agent-X should receive a 403 Forbidden response because they don't have permission to view Ticket-B or its attachments.
- actual: Agent-X receives a 200 OK response with the attachment metadata and file_url, allowing them to download the file.
- fix: Implement a custom object-level permission check in AttachmentViewSet that verifies the user has permission to access the target object (via the content_type and object_id fields). Create a custom permission class (e.g., HasTargetObjectPermission) that checks: for Tickets, use tickets/access.py agent_visible_tickets_q logic; for Comments, verify the user can see the parent object and isn't blocked by is_internal flag; for Messages, verify conversation membership; for other models, implement appropriate checks. Override check_object_permissions() in AttachmentViewSet to enforce this.
- VERDICT: confirmed (High) — CONFIRMED: The AttachmentViewSet at apps/attachments/views.py:164 uses permission_classes=[IsAuthenticated, IsTenantMember] with no object-level authorization checks. IsAuthenticated and IsTenantMember do NOT override has_object_permission() (verified in apps/accounts/permissions.py:189-244), so they default to returning True for object-level checks in DRF. The get_queryset() method (line 175-178)

### [attachments-2] Missing Authorization Check on Attachment Upload Target  (OK)
- category: Security | confidence: high | dimension: Attachments & file upload/download
- location: apps/attachments/serializers.py:106-124, AttachmentUploadSerializer.validate()
- description: The AttachmentUploadSerializer validates that the target object exists and belongs to the current tenant, but does NOT check if the uploading user has permission to modify that object. This allows any tenant member to upload attachments to objects they shouldn't have access to. For example, an agent can upload attachments to tickets they're not assigned to, or to internal comments they don't have access to.
- expected: The upload should fail with a 403 Forbidden response indicating the user doesn't have permission to attach files to that object.
- actual: The upload succeeds (201 Created) and the attachment is associated with Ticket-B.
- fix: Add authorization checks to AttachmentUploadSerializer.validate() that verify the user can modify the target object. Reuse the same logic as the retrieval fix above. For Tickets, verify the user can see the ticket via agent_visible_tickets_q (agents can only attach to their assigned tickets or unassigned tickets they created, managers/admins can attach to any ticket). For Comments, verify the user can modify comments on the parent object. For Messages, verify conversation membership.
- VERDICT: confirmed (Critical) — I independently verified the finding by reading: (1) AttachmentViewSet in apps/attachments/views.py:164 uses only [IsAuthenticated, IsTenantMember]; (2) AttachmentUploadSerializer.validate() in apps/attachments/serializers.py:106-124 validates tenant membership of target but not user's permission to modify it; (3) There are no object-level permission checks (get_object() override, check_object_per

### [attachments-3] No Authorization Required for Static Media File Downloads  (OK)
- category: Security | confidence: high | dimension: Attachments & file upload/download
- location: main/urls.py:46-47, Django static()/media serving; main/settings/base.py: MEDIA_URL, MEDIA_ROOT
- description: Attachment files are stored in MEDIA_ROOT and served via /media/ URL without any authorization checks. Once a user obtains the file_url from the AttachmentViewSet API (which lacks object-level authorization), they can directly download the file without any further permission checks. This is because Django's static.serve() in DEBUG mode and standard webserver configuration (nginx, Apache) do not perform authentication. The tenant-scoping in the file path (tenants/{tenant_id}/attachments/...) only isolates between tenants, not between users within a tenant.
- expected: The webserver should deny the request or the file serving should be proxied through a Django view that performs authorization checks.
- actual: The file is served by the webserver without any authorization checks beyond HTTP authentication.
- fix: Implement a custom file download view that performs authorization before serving files. Replace direct file URLs with a proxied endpoint (e.g., /api/v1/attachments/{id}/download/) that: (1) loads the Attachment record, (2) verifies user has permission to access the target object, (3) uses Django's FileResponse or X-Accel-Redirect (nginx) / X-Sendfile (Apache) to efficiently serve the file. This keeps authorization logic centralized in Django.
- VERDICT: confirmed (High) — The finding is confirmed but with corrected severity. Code review shows: (1) Attachment extends TenantScopedModel with auto-filtering by TenantAwareManager (main/models.py:29-48, main/managers.py:24-46), preventing cross-tenant leakage. (2) AttachmentViewSet.get_queryset() (apps/attachments/views.py:175-178) uses Attachment.objects which respects tenant scoping. (3) However, permission_classes are

### [custom-fields-agents-1] Company custom fields never synced to CustomFieldValue  (OK)
- category: Data Integrity | confidence: high | dimension: Custom fields & agent presence
- location: apps/custom_fields/signals.py (lines 20-43) + apps/contacts/signals.py
- description: The custom_fields sync signal receivers exist ONLY for Ticket (sync_ticket_custom_fields) and Contact (sync_contact_custom_fields), but NOT for Company. The Company model has a `custom_data` JSONField (apps/contacts/models.py:87-91) and is declared in ModuleType.COMPANY (custom_fields/models.py:34), but no post_save signal in contacts/signals.py connects Company saves to sync_custom_field_values(). This means Company custom field values are never indexed in CustomFieldValue rows, making them unsearchable/unfilterable via the API and invisible to the query-based EAV layer.
- expected: After a Company.save(), CustomFieldValue rows should be created/updated to mirror the custom_data dict, via the sync_custom_field_values() service (matching the Ticket and Contact behavior).
- actual: Company saves do not trigger sync_custom_field_values(). CustomFieldValue rows are never created, so Company custom fields exist in the schema but produce no queryable data.
- fix: Add a post_save signal receiver for Company in apps/contacts/signals.py (mirroring sync_ticket_custom_fields and sync_contact_custom_fields), or add Company receiver to apps/custom_fields/signals.py. Call sync_custom_field_values(instance, module='company') on Company.post_save when custom_data is present.
- VERDICT: confirmed (High) — The finding is definitively confirmed through code inspection and empirical testing. (1) Company model has a custom_data JSONField (apps/contacts/models.py:87-91). (2) CustomFieldDefinition supports module='company' (apps/custom_fields/models.py:34). (3) CompanySerializer includes custom_data (apps/contacts/serializers.py:55). (4) Signal receivers exist for Ticket and Contact custom field sync (ap

### [settings-secrets-1] DJANGO_DEBUG split-default footgun: unset env var defaults to True in __init__.py but False in base.py  (OK)
- category: Security | confidence: high | dimension: Settings, secrets & infra config
- location: main/settings/__init__.py:9 vs main/settings/base.py:17
- description: The settings init file defaults DJANGO_DEBUG to True when unset (line 9), but base.py defaults it to False (line 17). This creates a critical split: an unset DJANGO_DEBUG env var loads dev.py which sets ALLOWED_HOSTS=['*'], CSRF_COOKIE_SECURE=False, SESSION_COOKIE_SECURE=False, and no HSTS headers. A production deployment missing the DJANGO_DEBUG=False env var silently loads dev settings instead of prod.py, exposing DEBUG mode in production with all the security implications: SQL exception details in error pages, static file serving from memory, disabled CSRF token checking via ALLOWED_HOSTS=*, etc.
- expected: An unset DJANGO_DEBUG should fail safely or default consistently. Either: (a) base.py and __init__.py should share the same default (False), or (b) __init__.py should fail loudly if DJANGO_DEBUG is unset, forcing explicit configuration.
- actual: __init__.py defaults True, base.py defaults False. Unset env var silently loads dev.py with ALLOWED_HOSTS=['*'].
- fix: Change main/settings/__init__.py line 9 to: `if env.bool("DJANGO_DEBUG", default=False):` to align with base.py. This makes unset DJANGO_DEBUG fail safely to prod mode. Alternatively, raise an error if DJANGO_DEBUG is not set in production.
- VERDICT: confirmed (Critical) — The finding is confirmed by reading the actual code:

1. **main/settings/__init__.py line 9**: `if env.bool("DJANGO_DEBUG", default=True):` — defaults to True when unset.

2. **main/settings/base.py line 17**: `DEBUG = env.bool("DJANGO_DEBUG", default=False)` — defaults to False, but this is overwritten by the imported dev.py/prod.py.

3. **Import sequence when DJANGO_DEBUG is unset**:
   - __init

### [settings-secrets-2] Committed .env file contains real email credentials and webhook secrets  (DISMISSED(fp))
- category: Security | confidence: high | dimension: Settings, secrets & infra config
- location: .env:16-17, 26, 33
- description: The committed .env file at the repository root contains sensitive credentials: EMAIL_HOST_USER (kvnkmar012@gmail.com), EMAIL_HOST_PASSWORD (fgadwnhczjaoqlun), IMAP_PASSWORD (fgadwnhczjaoqlun), and INBOUND_EMAIL_WEBHOOK_SECRET (test-secret-123). These are plaintext secrets committed to git history (likely public repo), compromising email account access and inbound email webhook security. Any attacker with git history access can send emails as this user or intercept inbound email webhooks.
- expected: .env should NOT be committed. A .env.example template should exist (it does) with placeholder values. The actual .env should be in .gitignore.
- actual: .env is committed with real email credentials and webhook secrets in plaintext.
- fix: 1. Add .env to .gitignore immediately. 2. Rotate the compromised email account password and WEBHOOK_SECRET. 3. Rewrite git history to remove the .env file and all its plaintext secrets using 'git filter-repo' or 'git filter-branch'. 4. Document the rotation in a security incident log.
- VERDICT: refuted (High) — The .env file EXISTS on disk with REAL, VALID email/IMAP credentials (confirmed via live SMTP/IMAP login tests), BUT it is NOT committed to the git repository. Multiple git checks confirm: git ls-files shows no .env entry, git ls-tree -r HEAD has no .env blob, git rev-list --all -- .env returns empty, and git log shows no commits adding .env. The .gitignore file is properly configured (line 1-2: "

### [frontend-js-1] Dead link in command palette: '/contacts/new/' doesn't exist  (OK)
- category: Business Logic | confidence: high | dimension: Frontend JavaScript
- location: static/js/command-palette.js:28
- description: The command palette has a 'New Contact' action that links to '/contacts/new/', but the actual frontend route is '/contacts/create/'. This causes navigation to fail when users click on 'New Contact' from the command palette.
- expected: Navigation to /contacts/create/ (the actual contact creation page)
- actual: Navigation to /contacts/new/ (which does not exist as a route)
- fix: Change line 28 from url: '/contacts/new/' to url: '/contacts/create/' to match the frontend_urls.py route mapping.
- VERDICT: confirmed (Medium) — Code inspection confirms the defect. Line 28 of static/js/command-palette.js hardcodes url: '/contacts/new/', but apps/tenants/frontend_urls.py:29 defines the actual route as path("contacts/create/", ...). No '/contacts/new/' route exists anywhere in frontend_urls.py. The finding is factually accurate. However, severity is overstated: this breaks only a convenience quick-action shortcut, not a cor

### [data-model-integrity-1] Ticket assignee cross-tenant vulnerability  (OK)
- category: Security | confidence: high | dimension: Data model & migrations integrity
- location: apps/tickets/models.py:579-589
- description: Ticket.clean() validates assignee TenantMembership but save() never calls full_clean(), allowing assignee FK to point to users not in tenant.
- expected: Only TenantMembers assignable.
- actual: Any user in any tenant assignable.
- fix: Add save() override calling full_clean() or TicketSerializer validation.
- VERDICT: confirmed (High) — The vulnerability is CONFIRMED. Code review shows: (1) Ticket.clean() at apps/tickets/models.py:579-589 explicitly validates assignee is a TenantMembership, documenting the system invariant. (2) Ticket.save() at line 591 does NOT call full_clean(), so this validation is skipped on creation. (3) TicketCreateSerializer at apps/tickets/serializers.py:516+ includes assignee field but has NO validate_a

### [data-model-integrity-2] NewsPost CASCADE author deletion data loss  (OK)
- category: Data Integrity | confidence: high | dimension: Data model & migrations integrity
- location: apps/newsfeed/models.py:29-32
- description: NewsPost.author CASCADE deletes all posts when user removed, no audit trail.
- expected: Posts preserved or orphaned.
- actual: Hard-deleted silently.
- fix: Change to SET_NULL or PROTECT or implement soft-delete.
- VERDICT: confirmed (Medium) — The finding is confirmed by direct code inspection. NewsPost.author is defined with on_delete=models.CASCADE at apps/newsfeed/models.py:29-33, explicitly:

```python
author = models.ForeignKey(
    settings.AUTH_USER_MODEL,
    on_delete=models.CASCADE,
    related_name="news_posts",
)
```

The CLAUDE.md project documentation explicitly acknowledges this at line 389: "`author` **CASCADE** — deleti

==========================================================================================
## High — 75 raw
==========================================================================================

### [tenant-isolation-3] InboundEmail Not TenantScopedModel—Requires Manual Tenant Filtering Everywhere  (OK)
- category: Data Integrity | confidence: high | dimension: Multi-tenancy & tenant isolation
- location: /home/kavin/crm/apps/inbound_email/models.py:22-75 (model definition)
- description: InboundEmail inherits from TimestampedModel, not TenantScopedModel, so it does not get the automatic tenant-aware `.objects` manager. Every query on InboundEmail.objects must include an explicit `tenant=` filter to avoid cross-tenant leakage. This design is error-prone—a developer unfamiliar with this exception could use `.objects` without filtering and unknowingly leak data across tenants. The imap_poller.py:337 critical finding above is direct evidence this design has already been violated in production code.
- expected: InboundEmail should either (a) inherit from TenantScopedModel so filtering is automatic, or (b) have a custom manager that requires explicit tenant= filters and raises an error if omitted.
- actual: InboundEmail uses a plain Django manager. The design relies entirely on developer discipline.
- fix: Refactor InboundEmail to inherit from TenantScopedModel and make tenant required (non-nullable: null=False). This automatically secures all queries via the fail-closed TenantAwareManager. If nullable tenant is truly needed for some reason, create a custom manager that enforces `tenant=` in all queries or allows only `.unscoped` queries with explicit checking.
- VERDICT: confirmed (Critical) — The finding is CONFIRMED via direct code inspection. I verified:

1. **InboundEmail model inheritance** (/home/kavin/crm/apps/inbound_email/models.py:22): Inherits from TimestampedModel, NOT TenantScopedModel. Uses plain Django Manager (verified via Python import check).

2. **The specific bug at imap_poller.py:337**: 
   ```python
   if InboundEmail.objects.filter(message_id=message_id).exists

### [tenant-isolation-4] Missing Tenant Context in convert_to_ticket Service  (OK)
- category: Business Logic | confidence: high | dimension: Multi-tenancy & tenant isolation
- location: /home/kavin/crm/apps/inbox_hub/services.py:152-155
- description: The convert_to_ticket() function calls _create_ticket_from_email(...) at line 153 without wrapping in tenant_context(). While the function has access to the tenant variable (line 135), it does not set it in the context. This means if _create_ticket_from_email or any nested service internally uses TenantAwareManager without explicit tenant= filters, those queries could fail or return empty querysets. Since _create_ticket_from_email is also called from the direct API path (api_views.py), auditing both call sites is necessary.
- expected: All service functions that perform TenantAwareManager queries should either (a) be wrapped in `with tenant_context(tenant):`, or (b) have explicit documentation stating they require a bound tenant context and accept it as a parameter.
- actual: convert_to_ticket at lines 152-155 calls _create_ticket_from_email without tenant_context. While the DRF view that calls convert_to_ticket has request.tenant set, the service function itself does not ensure context.
- fix: Wrap the _create_ticket_from_email call in tenant_context: `with tenant_context(tenant): ticket = _create_ticket_from_email(...)`
- VERDICT: confirmed (Medium) — The finding is confirmed as a code-quality and reliability issue, though not a current active bug. Analysis:

CONFIRMED FACTS from code review:
1. `_create_ticket_from_email()` at apps/inbound_email/services.py:383 calls `TicketStatus.objects.filter(is_default=True)` at line 398, which uses TenantAwareManager.
2. TenantAwareManager (main/managers.py:35-46) calls `get_current_tenant()` and returns 

### [tenant-isolation-5] Admin Tenant Scoping Broken for /admin/ Accessed via Bare Domain  (DISMISSED(fp))
- category: Security | confidence: high | dimension: Multi-tenancy & tenant isolation
- location: /home/kavin/crm/apps/tenants/middleware.py:119-144
- description: The TenantMiddleware special-cases /admin/ paths and sets request.tenant=None when the bare domain is used (no subdomain). This allows superuser-only access but breaks the TenantFilteredAdmin mixin: its get_queryset() uses request.tenant for filtering, but since it's None, all TenantScopedModel queries return empty results. A superuser trying to manage tenant data via /admin/ on the bare domain will see no data, potentially leading them to believe the database is empty and take destructive actions.
- expected: The admin site should either (a) resolve tenant from subdomain when present and show that tenant's data, (b) use .unscoped in all admin querysets and show cross-tenant data (for superusers), or (c) require superusers to access admin via a subdomain.
- actual: Admin site sets request.tenant=None when accessed at the bare domain. TenantFilteredAdmin.get_queryset() uses this None value to filter, resulting in empty queryset.
- fix: In TenantFilteredAdmin.get_queryset(), check if request.tenant is None AND the user is a superuser, then use .unscoped: `if tenant is None and request.user.is_superuser: return model.unscoped.all()` instead of failing to a filtered (empty) queryset.
- VERDICT: refuted (unchanged) — The finding claims that accessing `/admin/` on the bare domain results in empty queryset due to `request.tenant=None` causing `TenantFilteredAdmin.get_queryset()` to filter through `TenantAwareManager` which returns `.none()`. However, the actual code explicitly guards against this: (1) `TenantFilteredAdmin.get_queryset()` at line 46-47 explicitly checks `if hasattr(model, "unscoped"): qs = model.

### [tenant-isolation-6] Feature B Missing Tenant Context in API create_ticket  (DISMISSED(fp))
- category: Business Logic | confidence: high | dimension: Multi-tenancy & tenant isolation
- location: /home/kavin/crm/apps/inbound_email/api_views.py:308-314
- description: Feature B's create_ticket action calls find_or_create_contact(tenant, ...) and _create_ticket_from_email(...) within transaction.atomic() but without wrapping in tenant_context(). While the DRF view has request.tenant set via middleware, the service functions themselves should have explicit context for defensive correctness. If any on_commit() handlers within _create_ticket_from_email spawn Celery tasks or use deferred execution that relies on implicit context, the context could be lost.
- expected: Wrap the atomic block in tenant_context(tenant) to ensure all nested work, including on_commit handlers, runs with context.
- actual: The API view at lines 308-314 calls services without tenant_context().
- fix: Wrap the atomic transaction in tenant_context: `with tenant_context(tenant): with transaction.atomic(): contact, _ = find_or_create_contact(...); ticket = _create_ticket_from_email(...)`
- VERDICT: refuted (Low) — The finding claims Feature B's create_ticket action is missing tenant_context() and risks losing context for on_commit handlers. However, audit of the code reveals:

1. TenantMiddleware (apps/tenants/middleware.py:179) sets `set_current_tenant(tenant)` BEFORE the view executes
2. The path /api/v1/inbound-email/ is NOT in EXEMPT_PATH_PREFIXES, so middleware runs normally for this endpoint
3. Contex

### [authn-1] Race condition in fire_due_reminders without select_for_update  (OK) [DUP-OF tenant-isolation-7]
- category: Reliability | confidence: high | dimension: Authentication (JWT / API key / session / SSO)
- location: /home/kavin/crm/apps/crm/tasks.py:102-104
- description: The fire_due_reminders task stamps the due_notified_at watermark using filter().update() without select_for_update() locking. Two celery workers executing concurrently could both read the same reminder and both update the watermark independently, resulting in duplicate notifications.
- expected: Each reminder fires exactly once when it comes due.
- actual: Due reminders can fire multiple times if two workers process the same reminder before both watermark updates complete.
- fix: Wrap the watermark check and update in select_for_update() block or move inside a transaction with row-level locking.
- VERDICT: confirmed (Medium) — The race condition is CONFIRMED by direct code inspection. The code at `/home/kavin/crm/apps/crm/tasks.py:70-104` shows: (1) `.iterator()` materializes all due reminders from the database query before any watermark updates occur (lines 70-84); (2) No `select_for_update()` locking is used on the Reminder model (line 102 uses plain `.filter().update()`); (3) The task runs on a prefork worker pool

### [authn-2] DashboardView bypasses tenant RBAC with IsAuthenticated-only permission  (OK)
- category: Security | confidence: high | dimension: Authentication (JWT / API key / session / SSO)
- location: /home/kavin/crm/apps/analytics/views.py
- description: The DashboardView has only IsAuthenticated permission, no HasTenantPermission check. This bypasses role-based access control; admins cannot deny dashboard access via roles.
- expected: Only authenticated members of the current tenant can access DashboardView via HasTenantPermission.
- actual: DashboardView has only IsAuthenticated, relying entirely on queryset filtering for isolation.
- fix: Add HasTenantPermission to DashboardView.permission_classes and define analytics.view codename gated by role.
- VERDICT: confirmed (High) — The DashboardView at /home/kavin/crm/apps/analytics/views.py:182-311 has only IsAuthenticated permission (line 196), with no HasTenantPermission or IsTenantMember check. This is confirmed as a documented gap in CLAUDE.md ("⚠️ `DashboardView` has only `IsAuthenticated` (no `HasTenantPermission` — bypasses resource RBAC; tenant isolation still holds via scoped querysets)"). 

The vulnerability is

### [authn-3] Invitation tokens are reusable if races occur during acceptance  (OK)
- category: Security | confidence: high | dimension: Authentication (JWT / API key / session / SSO)
- location: /home/kavin/crm/apps/accounts/views.py:686-758
- description: Invitation tokens are checked only against is_accepted boolean, never marked consumed. Same token could be used multiple times if race conditions occur during acceptance.
- expected: Each token consumed exactly once upon successful acceptance.
- actual: Tokens have no consumption timestamp; only is_accepted checked.
- fix: Add consumed_at timestamp to Invitation. Check and set atomically in accept_invitation. Enforce email matching before acceptance.
- VERDICT: confirmed (High) — The finding correctly identifies a race condition vulnerability in the invitation acceptance flow (/home/kavin/crm/apps/accounts/views.py:686-758). The code uses a before-check-before-act pattern without atomicity: line 695 checks the in-memory `is_accepted` property, but this check is not atomic with the save on lines 740-741 that sets `accepted_at`. Two concurrent requests with the same token

### [authz-rbac-1] Repeated dictionary key in ACTION_MAP causes silent overwrites  (DISMISSED(fp))
- category: Code Quality | confidence: high | dimension: Authorization & RBAC
- location: apps/accounts/permissions.py:46, 93
- description: The ACTION_MAP dictionary has two entries with the key 'mark_all_read': line 46 maps it to 'view' (for Emails), and line 93 maps it to 'view' (for Newsfeed). Python silently overwrites the first with the second. This creates unpredictable permission checking behavior if different resources reuse the same DRF action name.
- expected: Each action name should be unique within ACTION_MAP, or the code should document and handle collisions explicitly.
- actual: The second definition (Newsfeed line 93) silently overwrites the first (Emails line 46).
- fix: Rename one of the actions to be unique (e.g., 'mark_emails_all_read' vs 'mark_newsfeed_all_read') OR move action-to-verb mappings into respective viewsets to avoid collisions.
- VERDICT: refuted (Low) — The duplicate key "mark_all_read" in ACTION_MAP (lines 46 and 93) is confirmed to exist in the code. However, the claimed severity of "High" with "unpredictable permission checking behavior" is refuted. Both entries map to the identical value "view", so the silent overwrite causes zero functional impact. Verification: (1) I read apps/accounts/permissions.py lines 46 and 93 — both are "mark_all_rea

### [authz-rbac-2] Raw role.hierarchy_level used instead of effective_role in 20+ code sites, breaking temp-role grants  (OK)
- category: Security | confidence: high | dimension: Authorization & RBAC
- location: apps/agents/services.py:226, apps/analytics/services.py:52, apps/kanban/serializers.py:188, apps/tickets/views.py:576, and 16+ other files
- description: Throughout the codebase, ~20 code sites filter by raw membership.role.hierarchy_level instead of membership.effective_role.hierarchy_level. When a user is granted a temporary elevated role (e.g., Agent→Manager), the effective_role property correctly returns the temp role, but these 20 sites still check the permanent role. This means a temp-promoted agent has inconsistent scoping: API permission classes see them as elevated, but list querysets, analytics, kanban filtering, and email auto-assignment still treat them at their original level. This violates the principle that a temporary role grant should apply uniformly.
- expected: Temporary role elevation should apply consistently across API, frontend querysets, analytics, badge counts, and auto-assignment.
- actual: Temporary role elevation applies only to API permission checks; list/detail querysets, analytics, kanban, and email auto-assignment ignore it and use permanent role.
- fix: Find-and-replace all membership.role.hierarchy_level with membership.effective_role.hierarchy_level (20+ sites). Add a linter rule to enforce 'must use effective_role' pattern.
- VERDICT: confirmed (High) — The finding is confirmed via direct code inspection. I verified: (1) effective_role is a real property (apps/accounts/models.py:285-289) returning temp role if active; (2) temp roles are a real feature with grant/revoke endpoints (apps/agents/views.py:518-720); (3) API permission classes consistently use effective_role.hierarchy_level in 6 sites (apps/accounts/permissions.py:169,180,209,266,288; a

### [authz-rbac-3] IsTenantMember returns True when tenant=None, allowing authenticated users to bypass tenant scoping  (OK)
- category: Security | confidence: high | dimension: Authorization & RBAC
- location: apps/accounts/permissions.py:233-244
- description: IsTenantMember.has_permission explicitly returns True when request.tenant is None (lines 239-241), claiming this is for 'main site, public endpoints'. However, any viewset using ONLY [IsAuthenticated, IsTenantMember] without HasTenantPermission will allow all authenticated users through when tenant=None, with no permission check. If the endpoint is a write action and querysets are not properly scoped, this leaks data across tenants or contexts.
- expected: IsTenantMember should fail when tenant=None, except on intentionally public endpoints.
- actual: IsTenantMember explicitly allows through when tenant=None, relying on viewsets/querysets to enforce multi-tenancy downstream.
- fix: Change IsTenantMember to return False when tenant=None, OR add explicit documentation with code examples showing safe vs. unsafe permission stacks. Option 1 is safer: endpoints that truly need to work without tenant context should use AllowAny or a custom permission.
- VERDICT: confirmed (Medium) — Reading the actual code confirms the finding. At apps/accounts/permissions.py:233-244, `IsTenantMember.has_permission()` explicitly returns True when request.tenant is None (line 239-241), with the comment claiming this is for 'main site, public endpoints'. 

The vulnerability is REAL but PARTIALLY MITIGATED. Evidence:
1. apps/crm/views.py:120 contains `Ticket.unscoped.filter(pk=activity.ticket_id

### [authz-rbac-4] Expired temporary roles are never automatically cleared from the database  (OK)
- category: Reliability | confidence: high | dimension: Authorization & RBAC
- location: apps/accounts/models.py:274-282, apps/agents/views.py:610-623, no cleanup task exists
- description: When a temporary role is granted with an expiry time, the expiry is checked on-the-fly via has_active_temporary_role property, which checks if temporary_role_expires_at > now(). However, there is NO background task that periodically clears expired temporary roles by setting the fields back to NULL. The database row carries stale data indefinitely, and if there is ever a logic error in the expiry check or a future refactor that caches the value, the escalation will persist. This is also a data hygiene issue: the audit trail shows a grant that is 'no longer active' but the row is never cleaned up.
- expected: Either a Beat task runs every 1-5 minutes and clears rows where temporary_role_expires_at <= now(), OR a pre_save signal auto-clears if now() > temporary_role_expires_at.
- actual: Expired temporary roles remain in the database indefinitely with no cleanup mechanism.
- fix: Add a Celery Beat task 'cleanup_expired_temporary_roles' (schedule: 300s) that does: TenantMembership.unscoped.filter(temporary_role_expires_at__lt=now()).update(temporary_role=None, temporary_role_expires_at=None, temporary_role_granted_by=None, temporary_role_granted_at=None). Also clear temporary_permissions for those rows.
- VERDICT: confirmed (Medium) — I independently verified the finding by reading the actual code:

1. **TenantMembership model (apps/accounts/models.py:274-282)**: The `has_active_temporary_role` property correctly checks expiry on-the-fly via `timezone.now()`, but there is no automatic cleanup logic in the model itself (no save() override).

2. **Temporary role grant logic (apps/agents/views.py:610-623)**: When a temporary role 

### [authz-rbac-5] Feature B (create-ticket-from-email overrides) unreachable via Inbox Hub convert endpoint  (OK)
- category: Business Logic | confidence: high | dimension: Authorization & RBAC
- location: apps/inbound_email/api_views.py:45-154, apps/inbox_hub/services.py, apps/inbox_hub/serializers.py (not widened)
- description: Feature B widens _create_ticket_from_email and convert_to_ticket to accept subject/description/category/due_date/tags overrides, validated by _build_ticket_overrides. However, only InboundEmailViewSet.create_ticket (Emails page) uses these overrides. The Inbox Hub's convert-to-ticket endpoint (HubEmailViewSet) uses ConvertToTicketSerializer which only forwards queue/status/assignee/priority. The 5 new override keys are accepted by the service but unreachable via the Hub API, creating an inconsistency: Hub triage agents cannot override subject/description/etc., but Emails-page agents can.
- expected: Both Hub convert and Emails create_ticket should support the same override fields.
- actual: Only Emails-page create_ticket supports the full override set; Hub convert silently ignores subject/description/category/due_date/tags.
- fix: Widen ConvertToTicketSerializer to include all 5 new fields and pass them to convert_to_ticket. OR document that Hub only supports 4 fields and mark the others as 'not supported via this endpoint'.
- VERDICT: confirmed (Medium) — The code confirms the finding exactly as stated. Feature B (create-ticket-from-email overrides) widened the service layer `convert_to_ticket` to accept 9 optional parameters (subject, description, category, due_date, tags, plus the original 4). The Emails-page endpoint at `POST /api/v1/inbound-email/{id}/create-ticket/` (apps/inbound_email/api_views.py:242-323) calls `_build_ticket_overrides` to v

### [inbox-hub-access-3] first_responded_at Never Written, Causing Response SLA to Always Trigger  (OK)
- category: Business Logic | confidence: high | dimension: Inbox Hub access gate & permissions
- location: apps/inbox_hub/tasks.py:49-57 (check_hub_sla_breaches function) vs. apps/inbox_hub/models.py:183 (HubEmail.first_responded_at field)
- description: The HubEmail.first_responded_at field is defined in the model but is never written by any code in the repo. The SLA breach check at task.py:49 tests `if not he.response_breached and he.first_responded_at is None`, which will always be True until response_breached is flagged. This means the response-breach check has no concept of 'the customer replied, so the breach is moot'. The deadline alone triggers the breach, regardless of whether the email has been responded to. The field is dead.
- expected: When an email receives an agent response, first_responded_at should be stamped, and the response-breach check should skip flagging if first_responded_at is set.
- actual: first_responded_at is never written to by any code. The response-breach logic is: `if first_responded_at is None and now >= deadline: flag breach`. Since first_responded_at is always NULL, every email in an active state breaches its response deadline eventually (unless response_breached is already True).
- fix: Implement the missing write-site in the reply logic (not yet in MVP): when an agent replies to the customer, set first_responded_at=now and optionally update the response_breached flag. Or remove the field and accept that this is a deadline-only SLA. Current state is a broken feature: the column exists but is non-functional.
- VERDICT: confirmed (unchanged) — I confirmed this defect through source code inspection:

**Model Definition:** HubEmail.first_responded_at is declared at apps/inbox_hub/models.py:183 as DateTimeField(null=True, blank=True).

**Read Site:** apps/inbox_hub/tasks.py:49 checks `if not he.response_breached and he.first_responded_at is None:` — this condition guards the response-breach logic at line 50-58.

**Write Sites:** I performe

### [inbox-hub-access-4] Partial Index Condition in HubEmail Does Not Match SLA Breach Task  (OK)
- category: Performance | confidence: high | dimension: Inbox Hub access gate & permissions
- location: apps/inbox_hub/models.py:240-244 (ih_email_active_sla_due index condition) and apps/inbox_hub/tasks.py:32-37 (active_states list)
- description: The partial index on sla_response_due_at filters for states [NEW, ASSIGNED, IN_PROGRESS, PENDING_AGENT] but the check_hub_sla_breaches task scans active_states = [NEW, ASSIGNED, IN_PROGRESS, PENDING_AGENT, ESCALATED]. The task can hit rows in ESCALATED state that are not covered by the index, causing a full table scan for that state.
- expected: The index condition and the task's active_states list should match, or the task should only scan states covered by the index.
- actual: Index condition: Q(state__in=['new', 'assigned', 'in_progress', 'pending_agent']). Task scans: [NEW, ASSIGNED, IN_PROGRESS, PENDING_AGENT, ESCALATED]. ESCALATED rows are not in the partial index, so the planner cannot use it for them.
- fix: Update the index condition in models.py line 242 to include ESCALATED: `condition=Q(state__in=['new', 'assigned', 'in_progress', 'pending_agent', 'escalated'])`
- VERDICT: confirmed (unchanged) — Verified against actual source code. Index condition at apps/inbox_hub/models.py:242 explicitly filters to state__in=["new", "assigned", "in_progress", "pending_agent"]. Task at apps/inbox_hub/tasks.py:32-37 explicitly scans [NEW, ASSIGNED, IN_PROGRESS, PENDING_AGENT, ESCALATED]. The partial index does not cover ESCALATED rows, forcing the query planner to fall back to sequential scan or alternati

### [tickets-core-2] Ticket.clean() assignee-membership validation never invoked during create/update  (OK)
- category: Data Integrity | confidence: high | dimension: Tickets CRUD, serializers, viewset actions
- location: apps/tickets/models.py:579-589, serializers.py:588-617, views.py:597-602
- description: Ticket.clean() validates assignee is a tenant member (lines 579-589), but neither serializer.create() nor perform_create() calls full_clean(), bypassing the check. An assignee from another tenant can be set without error.
- expected: Call ticket.full_clean() after super().create() in the serializer, or add validate_assignee in the serializer.
- actual: The model validation is defined but never executed at the API boundary.
- fix: In TicketCreateSerializer.create() (line 611), add `ticket.full_clean()` before `return ticket`. Also validate in perform_update path (views.py:640).
- VERDICT: confirmed (unchanged) — The Ticket.clean() method at apps/tickets/models.py:579-589 validates that assignee is a tenant member, but this validation is never invoked. TicketCreateSerializer.create() (lines 588-617) calls super().create(validated_data) without calling ticket.full_clean(). ModelSerializer.save() does NOT invoke full_clean() per DRF design. TicketCreateSerializer has no validate_assignee() method (only valid

### [tickets-core-3] perform_update does not validate contact, company, pipeline_stage before save on PATCH/PUT  (DISMISSED(fp))
- category: Data Integrity | confidence: high | dimension: Tickets CRUD, serializers, viewset actions
- location: apps/tickets/views.py:604-669 (TicketViewSet.perform_update)
- description: perform_update validates only status/priority/assignee through service functions. Other ForeignKey fields (contact, company, pipeline_stage) bypass validation and are saved directly via serializer.save() on line 640, allowing cross-tenant updates.
- expected: Validate contact/company/pipeline_stage before save, or rely on serializer-level validators.
- actual: No validation occurs for these fields during update.
- fix: Add field-level validators to the serializer (validate_contact, validate_company, validate_pipeline_stage) to check tenant_id before returning.
- VERDICT: refuted (unchanged) — After examining the code, the finding is refuted because contact, company, and pipeline_stage ARE properly validated. Here's why:

1. **Auto-generated validation**: The TicketCreateSerializer (used for both create and update) includes contact, company, and pipeline_stage in Meta.fields (lines 541, 542, 545) but doesn't define them as explicit field classes. DRF auto-generates PrimaryKeyRelatedFiel

### [tickets-core-5] Queue.default_assignee auto-assignment bypasses tenant membership check  (OK) [DUP-OF tickets-core-2]
- category: Data Integrity | confidence: high | dimension: Tickets CRUD, serializers, viewset actions
- location: apps/tickets/serializers.py:594-600 (TicketCreateSerializer.create)
- description: When no explicit assignee is supplied and queue.auto_assign=True, the queue's default_assignee is used without validating that the user is a member of the ticket's tenant. An admin could set a queue's default_assignee to a cross-tenant user, leading to tickets assigned to them.
- expected: Validate queue.default_assignee membership when creating/updating the queue, or validate it in the serializer before using it.
- actual: No validation; cross-tenant default_assignee is silently used.
- fix: Add a clean() method to Queue model to ensure default_assignee (if set) is a member of the queue's tenant.
- VERDICT: confirmed (High) — I independently verified the finding by reading the actual code:

1. **Queue model** (apps/tickets/models.py:140-175): Queue.default_assignee is an unvalidated FK to the global User model (not tenant-scoped).

2. **QueueSerializer** (apps/tickets/serializers.py:59-89): Has NO validation on the default_assignee field. It accepts any User ID during create/update operations.

3. **TicketCreateSeriali

### [tickets-core-7] bulk_action delete permission check uses raw role instead of effective_role  (OK)
- category: Security | confidence: high | dimension: Tickets CRUD, serializers, viewset actions
- location: apps/tickets/views.py:1376-1391 (TicketViewSet.bulk_action)
- description: The delete action in bulk_action checks `membership.role.hierarchy_level > 20` instead of `effective_role.hierarchy_level`. Users with temporary Manager elevation are denied despite their temp permissions.
- expected: Use `effective_role.hierarchy_level` to respect temporary promotions.
- actual: The code checks `role.hierarchy_level` (raw role), bypassing temp permissions.
- fix: Change line 1386 to `membership.effective_role.hierarchy_level > 20`.
- VERDICT: confirmed (High) — The code at apps/tickets/views.py:1386 explicitly checks `membership.role.hierarchy_level > 20` (raw role) instead of `membership.effective_role.hierarchy_level`. This contradicts the established pattern in apps/accounts/permissions.py where HasTenantPermission (line 169) and IsTicketAccessible (line 209) both use effective_role.hierarchy_level. The effective_role property (models.py:284-289) is d

### [tickets-services-sla-1] merge_tickets and split_ticket lose lock immediately after select_for_update().exists()  (OK)
- category: Reliability | confidence: high | dimension: Tickets services, SLA & business hours
- location: apps/tickets/services.py:1516-1518 (merge_tickets), 1660 (split_ticket)
- description: The docstring claims both tickets are 'locked with select_for_update() for the duration of the transaction to prevent concurrent modifications.' However, the lock is lost immediately after the .exists() call because the queryset is not assigned or iterated. The .exists() call closes the database cursor and releases the lock before any subsequent data movement occurs. The comment at line 1516-1518 and 1660 shows a call to select_for_update().filter(...).exists() but the result is discarded, so the lock is never held across the merge/split operations.
- expected: Both tickets should be locked for the entire duration of the transaction, preventing concurrent modifications to comments, activities, and attachments.
- actual: The lock is released immediately after the .exists() call, allowing concurrent modifications to slip in between the lock and the first .update() call.
- fix: Assign the queryset to a variable to keep the lock alive, or iterate over it in a for loop. For merge_tickets: 'ticket_pks = list(Ticket.unscoped.select_for_update().filter(pk__in=[primary.pk, secondary.pk]).values_list('pk', flat=True))' before the comment/activity moves. Or simpler: re-fetch under select_for_update() before each .update() call.
- VERDICT: confirmed (unchanged) — Reading apps/tickets/services.py lines 1478-1518 and 1624-1660:

**merge_tickets (line 1478 @transaction.atomic):**
- Line 1516-1518: `Ticket.unscoped.select_for_update().filter(pk__in=[primary.pk, secondary.pk]).exists()` 
- The `.exists()` call executes the SELECT...FOR UPDATE query, acquires the row lock at the DB level, evaluates the boolean result, and the cursor closes → **lock is released i

### [tickets-services-sla-4] First-response SLA breach uses same stamp as response-already-happened check, creating race condition  (DISMISSED(fp))
- category: Data Integrity | confidence: high | dimension: Tickets services, SLA & business hours
- location: apps/tickets/services.py:1384-1426 (record_first_response) and tasks.py:94-99 (_check_ticket_sla)
- description: In record_first_response(), the atomic UPDATE stamps first_responded_at=now only if it was NULL (line 1392-1398). If the update succeeds, it then calls _check_first_response_breach() which compares first_responded_at > sla_first_response_due. However, the SLA breach task (_check_ticket_sla) also checks 'if not ticket.sla_response_breached and ticket.first_responded_at is None' (line 95). This means if the breach is checked BETWEEN the record_first_response() stamp and the _check_first_response_breach() call, the task will skip it (because first_responded_at is no longer None), and if the response was actually late, no signal fires. Conversely, if _check_first_response_breach() runs first but the ticket gets refreshed, the condition might double-check incorrectly.
- expected: If a response arrives after the deadline, sla_first_response_breached should always be set and the signal should fire exactly once.
- actual: Due to the race between the .update(first_responded_at=) and the task's check, the breach detection may be skipped, resulting in a late response that is NOT flagged as breached.
- fix: In _check_ticket_sla (tasks.py:95), change the condition from 'if not ticket.sla_response_breached and ticket.first_responded_at is None' to 'if not ticket.sla_response_breached and ticket.sla_first_response_due' (check only the deadline, not whether a response exists). The record_first_response() function will atomically set first_responded_at and call the breach check inline, making the check deterministic.
- VERDICT: refuted (Low) — After reviewing apps/tickets/services.py:1384-1426 (record_first_response and _check_first_response_breach) and apps/tickets/tasks.py:84-122 (_check_ticket_sla), the alleged race condition does not exist. The finding mischaracterizes the architecture:

1. **The design is intentional, not buggy:** record_first_response() atomically sets first_responded_at (line 1392-1398 with WHERE first_responded_

### [tickets-services-sla-5] Feature B (create-ticket-overrides): legacy path may not be atomic when called from inbound_email/services.py  (DISMISSED(fp))
- category: Data Integrity | confidence: high | dimension: Tickets services, SLA & business hours
- location: apps/inbound_email/services.py:450-460 (process_inbound_email)
- description: Feature B documentation states the legacy _create_ticket_from_email path is now 'wrapped in transaction.atomic()'. However, when called from process_inbound_email (the non-Hub path), there is no explicit transaction.atomic() wrapper visible around the call at line ~453. If _create_ticket_from_email() succeeds but an exception occurs later (e.g., attachment processing), the ticket is committed but the InboundEmail status may remain PROCESSING or mismapped, orphaning the ticket.
- expected: The entire process_inbound_email flow should be atomic. If any step fails, the ticket creation rolls back or the flow completes cleanly.
- actual: The legacy _create_ticket_from_email call may not be wrapped in an explicit transaction.atomic() in process_inbound_email, allowing partial failures.
- fix: Verify the entire process_inbound_email function (or at least the ticket-creation and attachment sections) is wrapped in a single transaction.atomic() block. If not, add it. The API endpoint (create_ticket action) DOES wrap the call correctly (seen at line in api_views.py), but the background task should match.
- VERDICT: refuted (Low) — The finding claims that Feature B's legacy path (when called from process_inbound_email) lacks transaction atomicity and "an exception occurs later (e.g., attachment processing fails)" resulting in partial failures. This is factually incorrect:

1. **Transaction coverage**: The entire process_inbound_email function is decorated with @transaction.atomic (line 258 of apps/inbound_email/services.py),

### [tickets-signals-1] Kanban card ordering racy: concurrent card creation can produce duplicate ordering within a column  (OK)
- category: Reliability | confidence: high | dimension: Tickets signals & dual-write logging
- location: apps/tickets/signals.py:511, 569, 622
- description: When creating or moving a kanban card, the code calculates the new order position via `target_column.cards.count()`. This is a TOCTOU (time-of-check-time-of-use) race: between the count and the insert, another concurrent save could insert a card with the same order value. Lines 511, 569, and 622 all exhibit this pattern.
- expected: Each card in a column should have a unique, sequential order. No two cards should share the same order value within a single column.
- actual: Without database-level locking or atomic increment, concurrent creates can produce duplicate order values, resulting in undefined sort order for cards in that column.
- fix: Use atomic database operations: either (a) use `F('order')` expressions with `select_for_update()` on the column, or (b) delegate ordering to a post-INSERT database trigger, or (c) use a unique_together constraint + handle IntegrityError by re-querying the max order.
- VERDICT: confirmed (Medium) — I read the three cited locations directly:

(1) **Line 511** (`apps/tickets/signals.py:511`) in `create_kanban_card_on_ticket_save`: `new_order = target_column.cards.count()` followed by `CardPosition.objects.create(..., order=new_order)`. This is within a post_save signal and lacks database-level locking.

(2) **Line 569** (`apps/tickets/signals.py:569`) in `sync_kanban_card_on_status_change`: Id

### [tickets-signals-2] Pipeline stage → kanban column sync matches by NAME, silently breaks on column rename  (OK)
- category: Business Logic | confidence: high | dimension: Tickets signals & dual-write logging
- location: apps/tickets/signals.py:618
- description: When a ticket's pipeline_stage changes, the code tries to find the target kanban column by matching `Column.name__iexact=new_stage_name` (line 618). If an admin renames a column, the signal will fail to find the matching column and silently do nothing. This means renamed columns break the pipeline→kanban sync, and old cards stay in the wrong columns forever.
- expected: When a column is renamed, the mapping between stage names and column names should be preserved via a FK or the sync should fail with a warning.
- actual: Column renames silently break the stage→column sync. No error is logged. Cards stay in the wrong columns.
- fix: Replace name-based matching with a FK: add a `pipeline_stage` FK to Column (nullable, for non-pipeline boards). Change line 616-619 to filter by `board=board, pipeline_stage=instance.pipeline_stage` instead of `board=board, name__iexact=new_stage_name`. Alternatively, log a WARNING when the name match fails.
- VERDICT: confirmed (High) — I verified the actual code at apps/tickets/signals.py lines 602-639 (sync_kanban_card_on_pipeline_stage_change signal). The signal attempts to find a Column by matching `Column.objects.filter(board=board, name__iexact=new_stage_name).first()` at lines 616-619. If no matching column is found (e.g., after an admin renames the column), `target_column` becomes None. The condition at line 621 checks `i

### [tickets-signals-3] Race condition in pre_save signal: status comparison fetches old ticket without locking  (OK) [DUP-OF tickets-services-sla-1]
- category: Data Integrity | confidence: medium | dimension: Tickets signals & dual-write logging
- location: apps/tickets/signals.py:58
- description: The `handle_ticket_status_change` signal handler fetches the previous ticket state via `Ticket.unscoped.get(pk=instance.pk)` without `select_for_update()`. Between the fetch and save, another process can modify the ticket, causing stale data. This could lead to incorrect activity logs, missed SLA pause/resume events, or incorrect resolved_at/closed_at timestamps.
- expected: The pre_save handler should lock the row to see the current state, or accept this as a known limitation.
- actual: Unprotected read of previous state. Concurrent saves can cause the signal to see stale data, leading to incorrect activity logs.
- fix: Change line 58 to use `select_for_update()`: `previous = Ticket.unscoped.select_for_update().get(pk=instance.pk)`. This ensures the signal handler sees the same version that's about to be overwritten.
- VERDICT: confirmed (Medium) — The race condition is real and confirmed by code review. At apps/tickets/signals.py:58, the `handle_ticket_status_change` pre_save signal reads the previous ticket state without `select_for_update()`. This snapshot (`previous`) is used for three purposes: (1) storing old field values for activity logging (lines 63-78), (2) detecting if status changed (line 81), and (3) determining old SLA pause st

### [inbox-hub-engine-2] SLA Response Breach Auto-Escalates Every Deadline (Deduplication Missing)  (OK)
- category: Business Logic | confidence: high | dimension: Inbox Hub engine (routing/assignment/SLA/state)
- location: apps/inbox_hub/tasks.py:55
- description: When check_hub_sla_breaches detects a response breach (which, as noted above, always happens at deadline), it calls escalate_hub_email(). The breach flag (response_breached) prevents the breach from firing multiple times, but escalate_hub_email itself has no deduplication. This means on the first breach, escalation_count increments, escalated_to is set, and state transitions (if legal). On the second task run, response_breached is already True so the breach block is skipped (line 49 guard). However, if check_hub_sla_breaches is somehow called again for a NEW hub_email instance (race condition, transaction rollback, or cross-process state desync), escalation could re-fire.
- expected: Either: (a) re-fetch response_breached from the DB before the check, or (b) include escalation deduplication in the escalate_hub_email function (e.g., only escalate if escalation_count == 0).
- actual: The response_breached guard prevents re-firing within a single task run, but stale in-memory state or concurrent calls could cause duplicate escalations.
- fix: Refactor the check to use `HubEmail.unscoped.filter(pk=he.pk, response_breached=False)` to ensure fresh DB state. Or: add an escalation_breached flag (like response_breached) to prevent escalate_hub_email from firing more than once.
- VERDICT: confirmed (High) — The finding identifies a real race condition, though the description of "stale in-memory state" is partially misleading. The actual issue: (1) check_hub_sla_breaches.py line 40-51 reads response_breached, then UPDATEs it, with NO row lock; (2) two concurrent task instances can both read response_breached=False before either writes True; (3) both will then call escalate_hub_email() at line 55, whic

### [inbox-hub-engine-3] Partial Index Excludes ESCALATED State While SLA Task Scans It  (OK) [DUP-OF inbox-hub-access-4]
- category: Performance | confidence: high | dimension: Inbox Hub engine (routing/assignment/SLA/state)
- location: apps/inbox_hub/models.py:242
- description: The `ih_email_active_sla_due` partial index is conditioned on `state__in=['new', 'assigned', 'in_progress', 'pending_agent']`, explicitly excluding ESCALATED. However, the check_hub_sla_breaches task (line 32-37) includes ESCALATED in its active_states list. This means queries seeking active SLA breaches for escalated emails bypass the partial index and trigger a full table scan (or a fallback composite index), degrading performance at scale. The comment on line 238-239 claims the index is used by the task; the mismatch contradicts this.
- expected: If ESCALATED emails are scanned for SLA breaches, the partial index condition should include ESCALATED: `state__in=['new', 'assigned', 'in_progress', 'pending_agent', 'escalated']`.
- actual: The partial index excludes ESCALATED, causing query misses for the task's filtering logic.
- fix: Add ESCALATED to the partial index condition in models.py line 242.
- VERDICT: confirmed (Medium) — The defect is confirmed by direct code inspection. 

EVIDENCE:
1. Partial index definition (apps/inbox_hub/models.py:240-244): 
   ```
   models.Index(
       fields=["tenant", "sla_response_due_at"],
       condition=Q(state__in=["new", "assigned", "in_progress", "pending_agent"]),
       name="ih_email_active_sla_due",
   )
   ```
   This creates: CREATE INDEX ... WHERE state IN ('new', 'assigne

### [inbox-hub-engine-4] escalation_count Incremented Unconditionally on Illegal Transitions  (OK)
- category: Business Logic | confidence: high | dimension: Inbox Hub engine (routing/assignment/SLA/state)
- location: apps/inbox_hub/services.py:275
- description: In escalate_hub_email, escalation_count is always incremented (line 275) before checking if the state transition to ESCALATED is legal (line 280). This means even if the transition is illegal (e.g., from DISMISSED or CONVERTED), the counter increments. A terminal email can have escalation_count bumped repeatedly by external event processing or race conditions, creating audit trail noise and incorrect escalation metrics.
- expected: Either: (a) escalation_count is only incremented when the state transition is legal, or (b) include escalation in the response_breached flag check so it fires exactly once per breach.
- actual: escalation_count is incremented every time escalate_hub_email is called, regardless of whether the state transitions or whether the email is terminal.
- fix: Move the escalation_count increment inside the `if can_transition(...)` block (line 280-282) so it only counts successful state transitions. Or: add an 'escalation_attempted' flag to deduplicate escalations.
- VERDICT: confirmed (Medium) — The finding is technically correct: in apps/inbox_hub/services.py at line 275, escalation_count is unconditionally incremented before the can_transition() check at line 280 validates whether the state transition to ESCALATED is legal. If the transition is illegal (e.g., from DISMISSED or CONVERTED_TO_TICKET state), the counter still increments while the state remains unchanged, saved via .save(upd

### [inbox-hub-engine-5] convert_to_ticket and dismiss_hub_email Bypass State Machine Validation  (OK) [DUP-OF inbox-hub-access-5]
- category: Business Logic | confidence: high | dimension: Inbox Hub engine (routing/assignment/SLA/state)
- location: apps/inbox_hub/services.py:158, 202
- description: The state_machine.py docstring (line 3-6) claims 'Service mutations that change state route through assert_transition so the workspace can never reach an inconsistent lifecycle.' However, convert_to_ticket (line 158) and dismiss_hub_email (line 202) directly set `hub_email.state = ...` without calling assert_transition, bypassing the state machine validation. Only transition_hub_email (line 241) enforces legal transitions. This allows bypass of the terminal state boundary if race conditions exist.
- expected: Both functions should call assert_transition before changing state.
- actual: Both bypass the state machine entirely, relying only on idempotency checks (== terminal state already, skip) to prevent re-entry.
- fix: Add `assert_transition(old_state, new_state)` calls in both functions before setting hub_email.state.
- VERDICT: confirmed (High) — Confirmed. Code shows convert_to_ticket and dismiss_hub_email directly set state without assert_transition as documented.

### [feature-a-reminder-2] Potential duplicate notifications if two Celery Beat schedulers run concurrently  (OK) [DUP-OF tenant-isolation-7]
- category: Reliability | confidence: high | dimension: Feature A — reminder-due popup
- location: apps/crm/tasks.py:70-104
- description: The fire_due_reminders task uses SELECT-then-UPDATE without a row-level lock (no select_for_update). The CLAUDE.md documentation explicitly notes 'No select_for_update → a theoretical SELECT-then-UPDATE race if two workers overlap (single-beat avoids it).' While the built-in Celery Beat scheduler (shelve-based) is single-instance, a misconfigured deployment with two Beat schedulers or a failed heartbeat/lock could allow two instances to execute the task simultaneously, resulting in duplicate notifications for the same reminder.
- expected: Even with concurrent task execution, each reminder should fire at most one notification, regardless of scheduler count. The watermark update should be atomic and prevent re-firing.
- actual: The SELECT and UPDATE are not locked together. Both workers SELECT the same reminder, both UPDATE it, both call send_notification, resulting in two notifications for one reminder due event.
- fix: Use `select_for_update()` to lock the reminder rows during the claim: `Reminder.unscoped.filter(pk=reminder.pk).select_for_update().update(due_notified_at=now)`. Alternatively, add a deployment-level safeguard to ensure only one Celery Beat instance runs.
- VERDICT: confirmed (Medium) — The race condition is CONFIRMED in /home/kavin/crm/apps/crm/tasks.py:70-104. The code performs a SELECT via iterator() at line 70-83 (no locks, no select_for_update), then a separate UPDATE at line 102-104. Between these two SQL operations, another concurrent task execution can read and modify the same reminder row, causing duplicate notifications. CLAUDE.md explicitly documents this risk (quot

### [feature-a-reminder-5] Race condition: recipient user deleted between fetch and send_notification  (OK)
- category: Reliability | confidence: medium | dimension: Feature A — reminder-due popup
- location: apps/crm/tasks.py:86-117
- description: The task fetches reminder.assigned_to into memory at line 90, then calls send_notification at line 117. If the user is deleted between these points, send_notification will raise an IntegrityError when trying to insert a Notification row with a dangling recipient_id FK. The watermark is already stamped (claim-first pattern), so the exception is caught and logged, leaving no notification delivered and no alert to administrators.
- expected: If a recipient user is deleted, the task should skip gracefully or find an alternative recipient (e.g., tenant admin).
- actual: send_notification raises IntegrityError, which is caught and logged. The reminder is marked notified but no notification was delivered. No escalation to administrators.
- fix: Check recipient.id validity or catch IntegrityError explicitly and log a higher-level warning. Alternatively, use `select_related` and `refresh_from_db()` to detect if the user no longer exists.
- VERDICT: confirmed (High) — Code inspection of /home/kavin/crm/apps/crm/tasks.py:81-151 confirms the race condition exists: (1) line 81 select_related("assigned_to", "created_by") fetches User objects; (2) line 90 stores in-memory user reference; (3) line 102-104 watermark stamped before send_notification (claim-first); (4) line 117-130 send_notification() called with potentially-stale user object; (5) apps/notifications/

### [feature-b-overrides-1] Assignee Lookup Not Strictly Tenant-Scoped  (DISMISSED(fp))
- category: Business Logic | confidence: high | dimension: Feature B — create-ticket-from-email overrides
- location: apps/inbound_email/api_views.py:121-134
- description: Membership check is tenant-scoped and correct, but User fetch is global without re-validation. User.objects.filter(pk=assignee_id).first() is tenant-agnostic. Pattern is not fail-closed to refactoring.
- expected: Retrieve user from matched membership or explicitly re-validate tenant scope after user fetch.
- actual: User.objects.filter(pk=assignee_id).first() is tenant-agnostic; only membership check enforces tenant scope.
- fix: Use membership.user directly: membership = TenantMembership.objects.filter(tenant=tenant, user_id=assignee_id, is_active=True).first(); assignee = membership.user if membership else None.
- VERDICT: refuted (Low) — The cited code at apps/inbound_email/api_views.py:121-134 demonstrates that the assignee lookup IS properly tenant-scoped through the membership check at lines 124-126 before the User fetch at line 127. The logic is: (1) Check if user_id exists as an active TenantMembership in the current request.tenant; (2) Only fetch the User object if the membership exists (line 127 conditional); (3) Return Non

### [feature-b-overrides-2] Bare Exception Handler Masks Unexpected Errors  (OK)
- category: Reliability | confidence: high | dimension: Feature B — create-ticket-from-email overrides
- location: apps/inbound_email/api_views.py:291-294
- description: Bare 'except Exception:' when accessing email.hub_email masks database errors, connection failures, and system errors. Any error becomes 'no hub email' fallback instead of generating 500 and triggering alerting.
- expected: Catch only RelatedObjectDoesNotExist, not all Exceptions. Let unexpected errors propagate naturally to generate 500s.
- actual: Bare 'except Exception:' catches database errors, timeouts, memory errors, etc., silently treating all as missing hub_email.
- fix: Replace with 'except (RelatedObjectDoesNotExist, AttributeError):' or 'except ObjectDoesNotExist:' from django.core.exceptions.
- VERDICT: confirmed (Medium) — The bare `except Exception:` handler is present at apps/inbound_email/api_views.py:291-294. Accessing `email.hub_email` (a reverse OneToOneField) normally raises RelatedObjectDoesNotExist (subclass of ObjectDoesNotExist), which is the intended catch case. However, the bare Exception handler also catches DatabaseError, OperationalError, connection timeouts, IntegrityError, and any other unexpected 

### [feature-b-overrides-3] Reachability Gap in Hub Convert Serializer  (OK) [DUP-OF feature-b-overrides-1]
- category: Business Logic | confidence: high | dimension: Feature B — create-ticket-from-email overrides
- location: apps/inbox_hub/services.py:100-150 vs apps/inbox_hub/serializers.py
- description: Service convert_to_ticket widened to accept subject, description, category, due_date, tags. Hub cockpit convert endpoint uses un-widened serializer forwarding only 4 original fields (queue/status/assignee/priority). 5 overrides unreachable in Hub UI.
- expected: Both Hub convert and Emails create-ticket expose same full override form, or Hub doesn't offer convert.
- actual: Hub convert serializer forwards only 4 original fields; Emails create-ticket accepts all 9.
- fix: Widen ConvertToTicketSerializer in apps/inbox_hub/serializers.py to include subject, description, category, due_date, tags fields.
- VERDICT: confirmed (Medium) — Independently verified the code: (1) ConvertToTicketSerializer (apps/inbox_hub/serializers.py:185-207) defines only 4 fields (queue_id, status_id, assignee_id, priority). (2) HubEmailViewSet.convert_to_ticket (apps/inbox_hub/views.py:147-168) passes only those 4 overrides to the service function. (3) convert_to_ticket service (apps/inbox_hub/services.py:100-102) signature accepts 9 keyword-only pa

### [websockets-1] VoIP CallEventConsumer broadcasts call metadata to all tenant members without per-user scoping  (OK)
- category: Security | confidence: high | dimension: WebSockets / Channels consumers
- location: apps/voip/consumers.py:30-90 (CallEventConsumer) + apps/voip/services.py:358-384 (_broadcast_call_event)
- description: CallEventConsumer joins a tenant-wide group (`voip_{tenant_id}`) with NO per-user/per-extension scoping. The broadcast handler (_broadcast_call_event) publishes full call metadata including caller_number, callee_number, contact_id, and ticket_id to every authenticated tenant member. A viewer-role member or agent without CRM access can monitor all confidential calls in the tenant.
- expected: Call metadata should be scoped to the users involved (extension owners/assignees) or admins only. Viewer-role members and users without relevant CRM permissions should NOT receive call event broadcasts.
- actual: All authenticated tenant members receive full call metadata via tenant-wide group broadcast, regardless of role or access permissions.
- fix: 1. Add per-user/per-extension scoping: modify CallEventConsumer to join extension-specific groups (e.g., `voip_extension_{extension_id}` + admin group). 2. In _broadcast_call_event, enumerate allowed recipients (extension owners, admins, assigned ticket agents) and fan out to individual user groups instead of the tenant-wide group. 3. Filter call payloads server-side to only include metadata that a recipient's role permits.
- VERDICT: confirmed (unchanged) — The CallEventConsumer in apps/voip/consumers.py:30-90 joins a single tenant-wide group (voip_{tenant_id}) and broadcasts full call metadata via _broadcast_call_event (apps/voip/services.py:358-384) including caller_number, callee_number, contact_id, and ticket_id to ALL authenticated tenant members with zero per-user/per-extension/per-role filtering. This is explicitly documented in CLAUDE.md as "

### [websockets-2] Internal comment bodies broadcast tenant-wide via LiveBus without server-side role filtering  (OK) [DUP-OF websockets-1]
- category: Security | confidence: high | dimension: WebSockets / Channels consumers
- location: apps/comments/signals.py:45-51 (broadcast_comment_save) + apps/comments/signals.py:31-42 (_serialise_comment)
- description: The broadcast_comment_save signal broadcasts internal comments (is_internal=true) to the entire tenant. While the payload includes is_internal flag, the docstring explicitly acknowledges clients should filter but does not implement filtering (static/js shows no is_internal filter). Viewer-role users and non-agents can access internal comment bodies intended only for agents by listening to LiveBus events on ticket/contact pages.
- expected: Internal comments should NOT be broadcast to non-agent users, or the broadcast should exclude the body for users without agent-level permissions. Server-side filtering is required.
- actual: Internal comment bodies are broadcast to the entire tenant, including viewers. Client-side filtering is not implemented and is not required, creating an information leak.
- fix: 1. Server-side filtering: in broadcast_comment_save, only broadcast internal comments if the recipient's role is agent-or-above (hierarchy_level <= 30). 2. Use per-role groups or filter before group_send. 3. Add explicit client-side filtering in live-bus.js as defense-in-depth to drop internal comment events when user role is < agent.
- VERDICT: confirmed (unchanged) — I opened and read the actual code at the cited locations: (1) apps/comments/signals.py lines 1-51 confirms the module docstring explicitly acknowledges internal comments are broadcast tenant-wide with a note that clients "should filter is_internal themselves"; (2) _serialise_comment (lines 31-42) includes both is_internal flag AND full comment body in the payload; (3) broadcast_comment_save (lines

### [websockets-3] TicketPresenceConsumer does not validate agent-level ticket visibility; allows unauthorized presence access  (OK)
- category: Bug | confidence: high | dimension: WebSockets / Channels consumers
- location: apps/tickets/consumers.py:150-156 (_can_access_ticket method)
- description: The _can_access_ticket check only verifies that the ticket exists in the tenant. It does NOT check whether the user has permission to view the ticket. Per apps/tickets/access.py, agents should only see tickets assigned to them OR tickets they created and are unassigned. Viewers have no ticket access. This consumer allows a viewer-role user or unauthorized agent to join the presence group for a ticket they should not see, revealing which agents are viewing that ticket.
- expected: The _can_access_ticket check should use the agent_visible_tickets_q() / agent_can_see_ticket() logic from apps/tickets/access.py to ensure only authorized users can join presence.
- actual: Any tenant member can join the presence group for any tenant ticket, bypassing documented ticket visibility rules.
- fix: 1. Modify _can_access_ticket to use the agent_can_see_ticket helper from apps/tickets/access.py. 2. Check if the user is admin/manager (hierarchy_level <= 20 via effective_role) OR satisfies agent_visible_tickets_q(user). 3. Return False for viewers and agents without access. 4. Use effective_role to account for temporary role grants.
- VERDICT: confirmed (High) — The finding is confirmed through direct code inspection. The TicketPresenceConsumer._can_access_ticket method (apps/tickets/consumers.py:150-156) only verifies that the ticket exists in the tenant via `Ticket.unscoped.filter(pk=ticket_id, tenant=tenant).exists()`. It performs zero user-level access control. In contrast, the IsTicketAccessible permission class (apps/accounts/permissions.py:189-217)

### [contacts-crm-1] Contact/Account lead_score and health_score can exceed bounds (0-100)  (OK)
- category: Data Integrity | confidence: high | dimension: Contacts / Companies / Accounts / CRM
- location: apps/contacts/models.py:163-166 (Contact), apps/contacts/models.py:35-38 (Account); apps/contacts/views.py:242-246; apps/contacts/serializers.py:149-240
- description: Contact.lead_score and Account.health_score have `PositiveSmallIntegerField` with default 50 and help_text indicating 0-100 range. Account.clean() validates this (lines 49-56), but Contact has NO clean() method. More critically, the scoring tasks calculate_lead_scores (apps/crm/tasks.py:418) and calculate_account_health_scores (apps/crm/tasks.py:526) use .update(score_val) which BYPASSES the clean() validation entirely. An agent could also directly PATCH a Contact/Account via the API with a malicious JSON payload before/after scoring runs, since both serializers have health_score/lead_score as read_only (good), but a direct database modification or a task failure mid-clamp would leave out-of-range values.
- expected: lead_score and health_score should ALWAYS remain in [0, 100]. If out-of-range, either raise ValidationError on API create/update or auto-clamp on save().
- actual: Contact.lead_score has no validation at all. Account.health_score validation is present in clean() but is never called by serializers, viewsets, or .update() calls. The scoring tasks do clamp (line 522), but the clamp happens in Python before .update(), not in the database — a task failure after clamping but before .update() means an old out-of-range value stays live.
- fix: (1) Add a clean() method to Contact that clamps/validates lead_score. (2) Override Ticket/Account.save() to call self.clean() OR auto-clamp before save (discouraged — validation should fail, not silently fix). (3) In the scoring tasks, use annotate(F(...)) + Case/When in the .update() to clamp in SQL so no out-of-range value is ever written. (4) Consider a database CHECK constraint as a last-line defense.
- VERDICT: confirmed (Medium) — Reading the actual code confirms the defect is real, but with important mitigations. (1) Contact.lead_score has no clean() method (models.py:163-166), verified at runtime. (2) Account.clean() exists (models.py:49-56) but DRF ModelSerializer.save() does NOT call clean() — confirmed via inspection. (3) Both fields are correctly marked read_only in all serializers (ContactSerializer:177, AccountSeria

### [contacts-crm-2] build_contact_context cache never invalidated when linked tickets change  (OK)
- category: Data Integrity | confidence: high | dimension: Contacts / Companies / Accounts / CRM
- location: apps/contacts/context.py:23-119; apps/contacts/signals.py:73-88; apps/tickets/signals.py (no cache invalidation)
- description: The contact_context_v2 cache (apps/contacts/context.py:40-118) is populated for 60 seconds and used by both ContactViewSet.context and HubEmailViewSet.context to build a ticket summary. When ANY ticket linked to the contact is created, updated, or closed, the cache key is NEVER invalidated. This means a ticket-detail page loads the cached snapshot (showing 'open_tickets=3'), but if another agent closes a ticket in the meantime, the page still shows 'open_tickets=3' until 60s expire. The signal receiver broadcast_contact_save (apps/contacts/signals.py:73-79) does NOT invalidate the contact_context_v2 cache, nor do any ticket signals (apps/tickets/signals.py has no contact cache logic).
- expected: When a ticket.status changes (especially to closed), the contact_context cache for that ticket's contact should be cleared immediately. When a contact is edited, its own cached context should be cleared.
- actual: No cache invalidation occurs. The cache lives for a fixed 60s regardless of data mutations. Clients receive stale aggregated stats (open_tickets, avg_csat, recent_tickets list) until TTL expires.
- fix: In apps/tickets/signals.py, add a post_save receiver on Ticket that clears the contact_context_v2 cache if ticket.contact_id changes or ticket.status.is_closed changes. Also add a post_delete receiver. The cache key is f'contact_context_v2:{tenant_id}:{contact_id}', so call cache.delete(key) in the signal handler.
- VERDICT: confirmed (High) — I independently verified the finding by: (1) reading apps/contacts/context.py:23-119, which populates and caches contact stats for 60 seconds without any explicit invalidation logic; (2) reading apps/contacts/signals.py:73-88, which broadcasts live events but does NOT call cache.delete(); (3) reading apps/tickets/signals.py (100+ receivers), confirming NO cache invalidation occurs on ticket status

### [knowledge-1] Full-Text Search Broken on SQLite (Dev Environment)  (OK)
- category: Bug | confidence: high | dimension: Knowledge base
- location: apps/knowledge/search.py:24-28, apps/knowledge/models.py:120, apps/knowledge/signals.py:18-19
- description: The kb_search function uses Django PostgreSQL-specific `SearchQuery` and `SearchRank` objects to query the `search_vector` field. On SQLite (the dev database), these classes are incompatible and silently fail to produce results. The `SearchVectorField` from `django.contrib.postgres.search` requires PostgreSQL's `to_tsvector` function. The docstring in views.py (line 546) claims a fallback to `icontains` for SQLite, but the actual implementation contains no such fallback code.
- expected: Search results should be returned using full-text search on PostgreSQL, or icontains fallback on SQLite as documented in the API schema.
- actual: On SQLite, the SearchQuery filter causes Django to internally raise EmptyResultSet (caught silently during queryset evaluation), resulting in zero results even when matching articles exist. The documented fallback behavior does not exist.
- fix: Implement the documented SQLite fallback in kb_search(): detect the database engine (as done in signals.py:17-19) and use `Article.objects.filter(title__icontains=query) | Article.objects.filter(content__icontains=query)` when not on PostgreSQL. Alternatively, update the API documentation to clarify that search requires PostgreSQL.
- VERDICT: confirmed (High) — I independently verified all cited code locations and confirmed the defect:

1. LOCATION VERIFICATION:
   - apps/knowledge/search.py:24-28: kb_search() unconditionally uses SearchQuery()/SearchRank without database detection
   - apps/knowledge/models.py:120: search_vector = SearchVectorField(null=True) is defined
   - apps/knowledge/signals.py:17-19: Database detection EXISTS but ONLY in the sign

### [knowledge-2] Session_key Ballot Stuffing: Multiple Users from Same IP Vote as One  (OK)
- category: Security | confidence: high | dimension: Knowledge base
- location: apps/knowledge/views.py:530-532
- description: The vote endpoint uses `request.session.session_key` to identify voters for idempotency, with a fallback to `request.META.get('REMOTE_ADDR')` when the session key is None. On unauthenticated or newly-created sessions, `session_key` may be None, causing all requests from the same IP address to share the same vote identity. An attacker can craft multiple requests from the same IP (or via proxy) and the unique_together constraint on (article, session_key) prevents duplicate voting by the same session—but all votes from the same IP collapse into one vote entry, allowing ballot stuffing.
- expected: Each vote should be uniquely tied to either an authenticated user session or a secure, per-request identifier. Multiple votes from the same IP should be prevented.
- actual: When request.session.session_key is None (common in fresh sessions), the code falls back to IP address. Multiple requests from the same IP produce only one vote row (due to unique_together), meaning the most recent vote overwrites previous votes from that IP rather than being rejected.
- fix: Use the authenticated user's identity to enforce one-vote-per-user. Change the vote endpoint to key KBVote on (article, user) instead of (article, session_key), and only allow authenticated users to vote. If anonymous voting is required, generate a server-side per-session nonce stored in the session, not REMOTE_ADDR.
- VERDICT: confirmed (Medium) — The code at apps/knowledge/views.py:530-532 implements `session_key = request.session.session_key or request.META.get('REMOTE_ADDR', 'anon')`. Verification shows: (1) For fresh/read-only sessions, request.session.session_key is None even for authenticated requests (confirmed via Django session testing). (2) KBVote has unique_together=[('article', 'session_key')] (apps/knowledge/models.py:206), so 

### [kanban-1] WIP limit race condition in move_card  (OK) [DUP-OF tickets-signals-1]
- category: Reliability | confidence: high | dimension: Kanban boards
- location: apps/kanban/services.py:224-229
- description: The WIP limit check uses .count() without SELECT FOR UPDATE, allowing concurrent requests to both read count=1, both pass the limit check, and both add cards, exceeding the limit.
- expected: Only first request succeeds, second gets 400 error.
- actual: Both succeed; column ends up with 3 cards (exceeds limit of 2).
- fix: Use `target_column.cards.select_for_update().count()` before the WIP check.
- VERDICT: confirmed (High) — I verified the code at apps/kanban/services.py:224-229. The WIP limit check reads target_column.cards.count() BEFORE acquiring any database locks. The atomic transaction begins at line 221, but the .count() call at line 224 does not use select_for_update(). The actual card movement with locks happens later in _move_across_columns() at lines 304-305 (which calls select_for_update()). This creates a

### [kanban-2] WIP limit race condition in add_ticket endpoint  (OK) [DUP-OF tickets-signals-1]
- category: Reliability | confidence: high | dimension: Kanban boards
- location: apps/kanban/views.py:336-349
- description: The add_ticket endpoint checks WIP limit at line 336 without SELECT FOR UPDATE and without atomic wrapping. Two concurrent requests can both read count=0, pass the check, and create cards, exceeding the limit.
- expected: Only one succeeds; second gets 400 error.
- actual: Both succeed; column ends up with 2 cards (exceeds limit of 1).
- fix: Wrap the entire check-and-create in transaction.atomic() with SELECT FOR UPDATE on the column.
- VERDICT: confirmed (Medium) — The race condition is confirmed by reading apps/kanban/views.py:335-349. The add_ticket endpoint checks column.wip_limit and column.cards.count() at line 336, but there is no atomic wrapping or select_for_update() lock. The CardPosition.objects.create() call at line 344-349 happens outside any transaction.atomic() block. Two concurrent requests can both read the same card count (e.g., both see 0 c

### [kanban-3] Pipeline stage column sync uses name matching instead of FK  (OK) [DUP-OF tickets-signals-2]
- category: Business Logic | confidence: high | dimension: Kanban boards
- location: apps/tickets/signals.py:616-619
- description: sync_kanban_card_on_pipeline_stage_change matches columns by name (name__iexact) instead of a Foreign Key. If admin renames the column, future pipeline stage changes won't sync the card because the name no longer matches. The function silently fails with no logging.
- expected: Card syncs to the stage's column even after rename, or rename is prevented if stage mappings exist.
- actual: Card silently stays in old column; no error logged.
- fix: Replace name matching with a Foreign Key between PipelineStage and Column, or add validation to forbid column renames if stages are mapped.
- VERDICT: confirmed (Medium) — The finding is absolutely correct and verified in the code. At /home/kavin/crm/apps/tickets/signals.py lines 616-619, the `sync_kanban_card_on_pipeline_stage_change` function matches columns by name using `Column.objects.filter(board=board, name__iexact=new_stage_name).first()` instead of using a Foreign Key relationship. This is directly compared to the `sync_kanban_card_on_status_change` func

### [kanban-4] Kanban serializer uses raw role instead of effective_role  (OK) [DUP-OF authz-rbac-2]
- category: Security | confidence: high | dimension: Kanban boards
- location: apps/kanban/serializers.py:188
- description: The _get_allowed_ticket_ids method checks `membership.role.hierarchy_level > 20` instead of `membership.effective_role.hierarchy_level`. A user with a temporary role promotion to Manager will be filtered as an agent, hiding tickets they should see per their effective role.
- expected: Temp-promoted managers see all board tickets (no filtering).
- actual: Temp-promoted managers see only agent-scoped filtered tickets.
- fix: Change line 188 to: `if membership and membership.effective_role.hierarchy_level > 20:`
- VERDICT: confirmed (unchanged) — Code inspection confirms the finding. At apps/kanban/serializers.py:188, the code checks `membership.role.hierarchy_level > 20` (raw role) instead of `membership.effective_role.hierarchy_level` (which accounts for active temporary roles). The TenantMembership model (apps/accounts/models.py:285-289) defines effective_role as a @property that returns temporary_role if active/not-expired, else the pe

### [messaging-4] Message.body TextField allows blank in serializer despite being required in model  (OK)
- category: Data Integrity | confidence: high | dimension: Messaging / conversations
- location: apps/messaging/models.py:133 + apps/messaging/serializers.py:127
- description: Message.body is a required TextField with no blank=True, but MessageCreateSerializer.body allows_blank=True and required=False. Empty messages are persisted because save() bypasses full_clean().
- expected: Reject blank bodies at serializer level: validate_body() should raise if value.strip() is empty.
- actual: Serializer field at line 127: allow_blank=True, required=False, default=''. Empty messages persist.
- fix: Update MessageCreateSerializer.validate_body() at line 154: 'def validate_body(self, value): value = (value or "").strip(); if not value: raise serializers.ValidationError("Message body cannot be empty."); return value'
- VERDICT: confirmed (High) — Verified by direct code inspection and test execution:

1. Model field definition (apps/messaging/models.py:133): `body = models.TextField()` has NO blank=True (default blank=False).

2. Serializer field (apps/messaging/serializers.py:127): `body = serializers.CharField(allow_blank=True, required=False, default="")` explicitly allows blank strings.

3. Validator method (apps/messaging/serializers.

### [messaging-5] WebSocket consumer hardcodes empty attachments array, inconsistent with REST broadcast  (DISMISSED(fp))
- category: Business Logic | confidence: high | dimension: Messaging / conversations
- location: apps/messaging/consumers.py:282 (_create_message return)
- description: ChatConsumer._create_message() returns hardcoded 'attachments': [], while MessageViewSet._broadcast_message() fetches actual attachments. First broadcast shows no files; rebroadcast shows them.
- expected: Both consumer and REST paths should return consistent attachment lists: either always empty initially, or both fetch from Attachment table.
- actual: Line 282: hardcoded []. Line 499 in views.py: calls _build_attachment_payload(message) which queries Attachment rows.
- fix: Replace line 282 with: 'from django.contrib.contenttypes.models import ContentType; ct = ContentType.objects.get_for_model(Message); attach_qs = Attachment.objects.filter(content_type=ct, object_id=message.pk); "attachments": [{"id": str(a.pk), "filename": a.filename} for a in attach_qs]' OR document that initial broadcast always has [] and frontend awaits rebroadcast.
- VERDICT: refuted (Low) — The finding claims ChatConsumer returns hardcoded attachments=[] while REST broadcast returns actual attachments, causing inconsistent payloads. However, this is intentional design documented in the codebase and memory notes. The workflow is: (1) create message via WebSocket with [] (message just created, no attachments yet), (2) upload files via REST attachments API, (3) call POST /broadcast/ to 

### [billing-3] Stripe Webhook No Idempotency Guard on Duplicate Events  (DISMISSED(fp))
- category: Reliability | confidence: high | dimension: Billing / subscriptions / Stripe
- location: apps/billing/webhooks.py:280-293
- description: Stripe webhook endpoint does not deduplicate on event ID. When Stripe retries webhook normal behavior on transient failures event handlers process same event multiple times without detecting duplicate.
- expected: Webhook handlers detect and skip duplicate events using Stripe event ID. Each event processed exactly once.
- actual: No idempotency tracking. Same event processed and stored multiple times if Stripe retries.
- fix: Add Event model to track processed Stripe event IDs. At start of stripe_webhook check if event.id was already processed. If yes return 200 immediately. Use get_or_create on stripe_event_id field to ensure single processing.
- VERDICT: refuted (Low) — The finding claims a "No Idempotency Guard on Duplicate Events" causing High severity reliability issues. However, detailed code analysis reveals this is inaccurate:

1. **Database-level idempotency EXISTS**: Both Subscription (line 119 of models.py) and Invoice (line 182 of models.py) have UNIQUE constraints on their Stripe ID fields (`stripe_subscription_id`, `stripe_invoice_id`). The webhook ha

### [analytics-exports-1] DashboardView bypasses HasTenantPermission RBAC check  (OK) [DUP-OF authn-2]
- category: Security | confidence: high | dimension: Analytics & exports
- location: apps/analytics/views.py:196, class DashboardView
- description: DashboardView uses only [IsAuthenticated] permission_classes, omitting HasTenantPermission. While tenant isolation is maintained via get_current_tenant() in querysets, resource-level RBAC (codename-based permissions) is completely bypassed. Any authenticated member can access dashboard data, even if they lack explicit 'view' codenames for reports/dashboard resources.
- expected: GET /api/v1/analytics/dashboard/ returns 403 Forbidden for users without 'dashboard.view' permission (consistent with other viewsets using HasTenantPermission).
- actual: GET /api/v1/analytics/dashboard/ returns 200 OK for any IsAuthenticated member, bypassing role-based access control.
- fix: Add HasTenantPermission to DashboardView.permission_classes and set permission_resource = 'dashboard' to enforce codename-based gating.
- VERDICT: confirmed (Medium) — DashboardView at apps/analytics/views.py:182-233 uses only [IsAuthenticated] permission_classes (line 196), omitting HasTenantPermission which is consistently used by other viewsets in the same app (ReportDefinitionViewSet line 61, DashboardWidgetViewSet line 93, ExportJobViewSet line 122, CalendarEventViewSet line 155). The endpoint lacks permission_resource definition, so if HasTenantPermission 

### [analytics-exports-2] PDF export writes CSV bytes with mismatched .csv filename instead of .pdf  (OK)
- category: Bug | confidence: high | dimension: Analytics & exports
- location: apps/analytics/tasks.py:209-212, function _generate_file
- description: When job.export_type == 'pdf', the function calls _generate_csv() and saves the CSV bytes with a .csv filename extension instead of .pdf. Users requesting PDF exports receive a file that is actually CSV data mislabeled with a .pdf extension (or vice versa when line 212 is reached).
- expected: PDF export should generate a proper PDF file (using ReportLab, WeasyPrint, or similar library) and save it with a .pdf extension.
- actual: PDF export generates CSV text and saves it as tickets_YYYYMMDD_HHMMSS.csv (line 212: wrong filename extension, plaintext content).
- fix: Replace the placeholder PDF generation (line 210-212) with a real library like ReportLab or WeasyPrint. For now, raise NotImplementedError or return the .csv fallback with a proper .csv filename instead of misleading .pdf.
- VERDICT: confirmed (Medium) — The finding is CONFIRMED as real but describes the situation with some confusion about which export type has which problem.

Code analysis from apps/analytics/tasks.py:

Lines 207-208 (XLSX):
```python
elif job.export_type == "xlsx":
    return _generate_xlsx(headers, rows), f"{base_name}.xlsx"
```

Lines 253-257 (_generate_xlsx fallback):
```python
except ImportError:
    logger.warning("openpyxl

### [analytics-exports-3] process_export_job.delay() called outside transaction.on_commit(), can strand job at PENDING  (OK)
- category: Reliability | confidence: high | dimension: Analytics & exports
- location: apps/analytics/serializers.py:147, method ExportJobCreateSerializer.create
- description: The Celery task process_export_job.delay() is triggered INSIDE the serializer create() method BEFORE the transaction commits. If the request transaction rolls back after the task is queued (e.g., due to a middleware exception or signal handler failure), the task will run against a non-existent ExportJob, marking it as FAILED with 'Job not found' after waiting for task execution.
- expected: Celery task should be queued AFTER transaction.on_commit() to ensure the ExportJob exists when the task executes.
- actual: Task is queued immediately in create(), before the transaction commits. A rollback leaves the task stranded with a non-existent job ID.
- fix: Wrap process_export_job.delay() in a transaction.on_commit() callback: `transaction.on_commit(lambda: process_export_job.delay(str(instance.id)))`.
- VERDICT: confirmed (Medium) — The finding identifies a real code smell and best-practice violation in apps/analytics/serializers.py:147. While the specific scenario ("request transaction rolls back") isn't currently active because ATOMIC_REQUESTS=False, the code IS vulnerable and violates the established pattern throughout the codebase. The process_export_job.delay() call is made outside transaction.on_commit(), matching the e

### [analytics-exports-4] DashboardView.get_agent_performance returns all agents' metrics, no user filtering  (OK)
- category: Business Logic | confidence: high | dimension: Analytics & exports
- location: apps/analytics/views.py:209, DashboardView.get()
- description: DashboardView calls get_agent_performance(tenant, date_from, date_to) without passing the request.user. The function returns a complete list of all agents' performance metrics (tickets handled, resolution times, etc.) for every tenant member viewing the dashboard. An agent user (role > 20) sees all peers' performance data, which may be sensitive or unintended.
- expected: Agent-tier users should see only their own agent_performance metrics, or the endpoint should filter agents per user role (similar to get_ticket_stats which applies user filter).
- actual: get_agent_performance returns all agents' metrics without user-level filtering. Any authenticated member sees all agents' performance.
- fix: Either: (a) Pass user=request.user to get_agent_performance and add user-filtering logic (similar to get_ticket_stats), OR (b) Conditionally return agent_performance only for Admin/Manager roles (is_admin_or_manager check already exists).
- VERDICT: confirmed (Medium) — The defect is CONFIRMED. Code inspection shows:

1. **Line 209 in apps/analytics/views.py**: `agent_performance = get_agent_performance(tenant, date_from, date_to)` is called WITHOUT passing `user=request.user`

2. **Line 121 in apps/analytics/services.py**: `get_agent_performance(tenant, date_from=None, date_to=None)` function signature has NO `user` parameter (contrast: `get_ticket_stats` at lin

### [attachments-4] Email Attachment Ingestion Bypasses MIME Validation  (OK)
- category: Security | confidence: high | dimension: Attachments & file upload/download
- location: apps/inbound_email/smtp_server.py:_save_smtp_attachments() (lines ~330-365)
- description: Email attachments are extracted directly from SMTP messages and saved to disk WITHOUT any MIME type validation (unlike the generic Attachment model which uses validate_file_upload()). The content_type is stored from part.get_content_type() which comes from email headers that an attacker can forge. Additionally, email attachments are NOT subject to the 25 MB size limit. A malicious email sender can attach executable files claiming they are images, and the system will store them based on the attacker's claimed MIME type.
- expected: Email attachments should be validated using the same python-magic MIME detection and allowlist as user-uploaded files, or rejected entirely.
- actual: Email attachments bypass validation and are stored with attacker-supplied MIME type claims.
- fix: Call validate_file_upload() for each email attachment before saving, using the same ALLOWED_MIME_TYPES. Also enforce the same 25 MB size limit. Wrap in try/except to handle validation failures gracefully (log and skip the attachment rather than failing the entire email).
- VERDICT: confirmed (Medium) — The finding is factually correct: email attachments are indeed extracted and saved without MIME validation in apps/inbound_email/smtp_server.py lines 179-217 (_save_smtp_attachments) and imap_poller.py lines 447-480 (_save_attachments). Both use part.get_content_type() directly from email headers without python-magic validation. 

However, the severity is overstated. Later in apps/inbound_email/se

### [attachments-5] Attachment Listing Leaks Metadata Across Visibility Boundaries  (OK) [DUP-OF attachments-1]
- category: Security | confidence: high | dimension: Attachments & file upload/download
- location: apps/attachments/views.py:175-178, AttachmentViewSet.get_queryset() with filtering
- description: The get_queryset() method returns all Attachments in the tenant. The list view allows filtering by ?content_type=... and ?object_id=..., enabling agents to enumerate attachments on any ticket they're not assigned to. The filter response leaks filename, MIME type, size, upload timestamp, and uploader name for restricted objects. An agent can systematically discover what documents exist on tickets they shouldn't have access to.
- expected: The query returns 404 or empty results for objects the user doesn't have permission to view.
- actual: The query returns metadata for all attachments on the restricted object.
- fix: Override filter_queryset() in AttachmentViewSet to validate that the user has permission to view each filtered content_type+object_id combination. For ticket attachments, verify via agent_visible_tickets_q; for message attachments, verify conversation membership; etc. Alternatively, remove the querystring filtering capability entirely and only allow direct retrieve-by-ID (which still needs permission checks).
- VERDICT: confirmed (High) — I examined the attachment viewset at apps/attachments/views.py:164-178. The AttachmentViewSet has only IsAuthenticated + IsTenantMember permission checks (line 164), with no object-level permission class. The get_queryset() returns Attachment.objects.select_related(...) which is tenant-scoped but otherwise unrestricted (line 175-178). The AttachmentFilter (lines 64-85) allows filtering by arbitrar

### [attachments-6] Attachment Deletion Lacks Object-Level Authorization  (OK) [DUP-OF attachments-1]
- category: Security | confidence: high | dimension: Attachments & file upload/download
- location: apps/attachments/views.py:220-246, AttachmentViewSet.perform_destroy()
- description: The DELETE operation uses the same insufficient permission classes as retrieve(), allowing agents to delete attachments from objects they don't have access to. An agent assigned to Ticket-A can delete attachments on Ticket-B. The audit logging happens after deletion, which is correct, but the deletion itself should be blocked.
- expected: DELETE request returns 403 Forbidden.
- actual: DELETE request returns 204 No Content and the attachment is deleted.
- fix: Apply the same HasTargetObjectPermission check (see first finding) to the destroy action. This will be enforced automatically via check_object_permissions() in the base APIView.
- VERDICT: confirmed (High) — The vulnerability is confirmed by examining the source code:

1. **AttachmentViewSet.permission_classes** (apps/attachments/views.py:164) contains only `[permissions.IsAuthenticated, IsTenantMember]` - both view-level permissions.

2. **Neither permission class implements has_object_permission()**: Verified via shell that `IsAuthenticated.has_object_permission` and `IsTenantMember.has_object_permi

### [custom-fields-agents-2] visible_to_roles M2M filter uses isnull=True, which never returns results  (DISMISSED(fp))
- category: Code Quality | confidence: high | dimension: Custom fields & agent presence
- location: apps/custom_fields/services.py:160-163 (get_field_definitions function)
- description: The get_field_definitions(user_role=...) function intends to return custom field definitions visible to all roles (by checking if visible_to_roles is empty). However, the filter Q(visible_to_roles__isnull=True) is incorrect for ManyToMany fields. A M2M field is never isnull on the model instance — the related manager always exists, even when no relations are set. This Q() will match zero results. The intent (fields with no role restrictions) cannot be achieved this way. Only fields explicitly visible to the user's role are returned (via the second Q clause), while unrestricted fields are silently excluded.
- expected: Fields with visible_to_roles.count() == 0 should be returned (visible to all), plus fields explicitly linked to the user's role.
- actual: Only fields explicitly linked to the user's role are returned. Fields with no role restrictions are excluded because Q(visible_to_roles__isnull=True) matches nothing.
- fix: Replace Q(visible_to_roles__isnull=True) with exclude(visible_to_roles__in=[...]) or use annotations: .annotate(has_roles=Count('visible_to_roles')).filter(Q(has_roles=0) | Q(visible_to_roles=user_role)).distinct().
- VERDICT: refuted (unchanged) — After independently reading the cited code in apps/custom_fields/services.py:160-163 and executing actual test cases against the live Django ORM with the CRM database, I confirmed that the `Q(visible_to_roles__isnull=True)` filter DOES correctly match CustomFieldDefinition instances with zero visible_to_roles relations. This is standard Django M2M behavior using LEFT OUTER JOIN: when no junctio

### [custom-fields-agents-3] get_field_definitions() function never called, visible_to_roles enforcement missing in API  (OK)
- category: Security | confidence: high | dimension: Custom fields & agent presence
- location: apps/custom_fields/services.py:135-165 (get_field_definitions) + apps/custom_fields/views.py:52-60
- description: The get_field_definitions(user_role=...) service function is defined but never called anywhere in the codebase (grep confirms zero call sites). The CustomFieldDefinitionViewSet.get_queryset() does NOT filter by role visibility — it returns all active field definitions for the tenant, regardless of the user's role (line 53: no visible_to_roles filtering). This means a non-admin user with a restricted role can see (and theoretically edit, via partial_update) custom field definitions that should be hidden from them. The visible_to_roles M2M exists in the schema but is completely unenforced at the API layer.
- expected: The API list view should filter by user role, returning only definitions the user's role is allowed to see (or that have no restrictions). Fields with role restrictions should be omitted from responses to unauthorized users.
- actual: All active field definitions are returned to all authenticated users, bypassing the visible_to_roles restriction entirely.
- fix: In CustomFieldDefinitionViewSet.get_queryset(), call get_field_definitions(tenant, module, user_role=self.request.membership.effective_role) instead of bare CustomFieldDefinition.objects.all(). Or inline the role filter directly. Fix the broken get_field_definitions() Q() logic first. Also enforce role visibility in HasTenantPermission checks for update/partial_update actions on restricted fields.
- VERDICT: confirmed (High) — I have confirmed the finding through multiple code paths:

1. **Dead function**: `get_field_definitions(tenant, module, user_role=None)` is defined at apps/custom_fields/services.py:135-165 but never called anywhere in the codebase (grep confirms zero call sites).

2. **Missing role filtering in get_queryset()**: CustomFieldDefinitionViewSet.get_queryset() at apps/custom_fields/views.py:52-60 retu

### [custom-fields-agents-4] Agent presence freshness uses server-local time for working_hours check, not tenant timezone  (OK)
- category: Business Logic | confidence: high | dimension: Custom fields & agent presence
- location: apps/agents/models.py:234-255 (_within_working_hours method)
- description: The is_assignable property checks auto_away_outside_hours via _within_working_hours(), which uses timezone.localtime() (line 246). This resolves to the Django project's TIME_ZONE setting (Asia/Kuala_Lumpur in base.py:149), NOT the user's or tenant's timezone. If a tenant is in a different timezone (e.g., US/Pacific), an agent's working hours will be evaluated against the server's local 9 AM–5 PM (KL time) instead of their intended 9 AM–5 PM (Pacific time). This causes incorrect auto-away behavior: an agent might be marked unassignable at 8 AM Pacific (which is 12 AM KL, next day) even though it's within their configured hours, or assignable at 6 PM Pacific (which is 12 AM+1 day KL) when they should be away.
- expected: Working hours should be evaluated against the agent's timezone (or tenant timezone), not the server's hardcoded TIME_ZONE. An agent in Pacific timezone should be assignable only during 09:00–17:00 Pacific time.
- actual: Working hours are evaluated against the server-local time (Asia/Kuala_Lumpur), causing cross-timezone misalignment. Agents in different timezones are incorrectly marked assignable/away.
- fix: Pass the user's or tenant's timezone to _within_working_hours(), and use timezone.localtime(tz=pytz.timezone(tenant.timezone or 'UTC')) instead of the bare call. Store agent/tenant timezone in the model. For now, fail-open (return True, assume within hours) when auto_away_outside_hours is set but timezone data is missing.
- VERDICT: confirmed (High) — The finding is confirmed via code inspection at apps/agents/models.py:234-255. The _within_working_hours() method uses timezone.localtime() (line 246) with no timezone argument, which resolves to Django's hardcoded TIME_ZONE setting (Asia/Kuala_Lumpur from main/settings/base.py:149), NOT the tenant's configured timezone. TenantSettings.timezone exists and is populated (apps/tenants/models.py:110),

### [custom-fields-agents-5] Claim-first dedup in fire_due_reminders runs outside transaction; stamp before send guarantees missed alerts  (OK) [DUP-OF feature-a-reminder-5]
- category: Reliability | confidence: high | dimension: Custom fields & agent presence
- location: apps/crm/tasks.py:102-130 (fire_due_reminders task, claim-first logic)
- description: The fire_due_reminders task implements a 'claim-first' pattern: stamp due_notified_at (line 102–104) BEFORE sending the notification (line 117). The task runs OUTSIDE an atomic transaction block (no wrapping transaction.atomic()). The docstring claims this prevents re-fire 'at most one missed alert', but the architecture actually guarantees a missed alert: if send_notification fails (exception caught on line 147), due_notified_at is already stamped with now >= scheduled_at. The reminder will never re-fire (due_notified_at < scheduled_at check will always fail). The alert is permanently lost. The comment's claim is misleading.
- expected: If notification delivery fails, the task should either retry the alert, or not stamp the watermark until notification succeeds, or wrap both in a transaction to rollback on failure.
- actual: The watermark is stamped before notification is attempted. If notification fails (caught exception), the alert is permanently lost and never retried.
- fix: Wrap stamp and send in a try-except that only stamps on success: try: send_notification(...) except Exception: logger.exception(...); return (no stamp). Or use transaction.atomic() with savepoints and rollback on send failure. Or move the stamp to within send_notification() after enqueue succeeds. Current design is inverted: comment says 'stamp first prevents duplicate', but doesn't account for send failure turning it into guaranteed loss.
- VERDICT: confirmed (High) — 
The auditor's factual claims are CONFIRMED via code inspection:

1. **CONFIRMED: Stamp happens before send (lines 102-104 before 117)** - The Reminder.unscoped.filter(pk=...).update(due_notified_at=now) executes on line 102-104, prior to the send_notification() call on line 117 in tasks.py.

2. **CONFIRMED: Exception handling at line 147** - Both the send_notification() call and broadcast_live_ev

### [settings-secrets-3] CSRF_COOKIE_HTTPONLY not set to True in dev or base settings, remains False  (OK) [DUP-OF settings-secrets-1]
- category: Security | confidence: high | dimension: Settings, secrets & infra config
- location: main/settings/base.py (missing), main/settings/dev.py (missing), main/settings/prod.py (missing)
- description: The CSRF cookie is NOT set to HTTPOnly in any settings file. Django's default for CSRF_COOKIE_HTTPONLY is False, meaning the CSRF token is readable by JavaScript (XSS-accessible). If an XSS vulnerability exists on the site, an attacker can read the CSRF token and forge POST requests. SESSION_COOKIE_HTTPONLY is True (line 196 base.py), but the CSRF cookie has no such protection. Django docs recommend CSRF_COOKIE_HTTPONLY=True in production.
- expected: CSRF_COOKIE_HTTPONLY should be True in base.py (or at least in prod.py) to prevent XSS exfiltration of the CSRF token.
- actual: CSRF_COOKIE_HTTPONLY is not set, defaults to False. The CSRF cookie is readable by JavaScript.
- fix: Add to main/settings/base.py (around line 197 with SESSION_COOKIE_HTTPONLY): `CSRF_COOKIE_HTTPONLY = True`
- VERDICT: confirmed (Medium) — The finding is technically CONFIRMED but with important nuance about severity. 

CONFIRMED FACTS (verified by code reading):
1. CSRF_COOKIE_HTTPONLY is NOT explicitly set anywhere in the codebase (confirmed by grep -r "CSRF_COOKIE_HTTPONLY" returning zero results)
2. Django's default for CSRF_COOKIE_HTTPONLY is False (Django 6.0.2 defaults)
3. The CSRF token cookie (csrftoken) will be readable by 

### [settings-secrets-4] CRM_voip queue defined in celery.py but not subscribed by any worker  (OK) [DUP-OF settings-secrets-1]
- category: Reliability | confidence: high | dimension: Settings, secrets & infra config
- location: main/celery.py:27, ecosystem.config.js:51, ecosystem.dev.config.js:52
- description: The main/celery.py routes all apps.voip.tasks.* to the 'crm_voip' queue, but neither the production worker (ecosystem.config.js line 51) nor the dev worker (ecosystem.dev.config.js line 52) subscribes to crm_voip. The worker subscribe lists are only: 'crm_default,crm_email,crm_webhooks'. This means all VoIP tasks — fire_due_reminders (Beat 30s), cleanup_stale_calls (Beat 3600s), sync_call_state, and process_call_recording — silently accumulate in the queue and never execute. The Beat scheduler will continuously try to enqueue cleanup-stale-calls every hour, piling up messages.
- expected: Either (a) add crm_voip to the worker's -Q list in ecosystem.config.js and ecosystem.dev.config.js, or (b) remove the crm_voip route from celery.py and route all voip tasks to crm_default.
- actual: crm_voip tasks route to a queue no worker consumes, causing task accumulation and non-execution.
- fix: Option A (preferred if VoIP is used): Update both ecosystem.config.js line 51 and ecosystem.dev.config.js line 52 to: `-Q crm_default,crm_email,crm_webhooks,crm_voip`. Option B (if VoIP is not used): Remove the voip route from celery.py or route to crm_default.
- VERDICT: confirmed (High) — main/celery.py:27 explicitly routes all apps.voip.tasks.* to "crm_voip" queue. ecosystem.config.js:51 and ecosystem.dev.config.js:52 both define the production and dev Celery workers with -Q flag listing only "crm_default,crm_email,crm_webhooks" — crm_voip is absent from both. main/settings/base.py:318-321 confirms cleanup_stale_calls is in the Beat schedule at 3600s (hourly). apps/

### [frontend-js-2] Multiple WebSocket reconnect implementations with inconsistent backoff caps  (OK)
- category: Reliability | confidence: high | dimension: Frontend JavaScript
- location: static/js/app.js:690-722 (notifications WS @ 10 attempts, 30s max) vs static/js/ticket-feed.js:19-108 (ticket-feed @ 10 attempts, 30s max) vs static/js/live-connection.js:42-167 (live @ infinite retries, 30s max)
- description: The application has 3 independent WebSocket consumers (notifications, ticket-feed, live-connection) each with their own reconnection logic. All use exponential backoff but with different max-attempt caps: notifications and ticket-feed both cap at 10 attempts (then stop reconnecting), while live-connection has infinite retries. This creates inconsistent resilience: tabs that lose connectivity may permanently drop the notifications and ticket-feed feeds after 10 failed attempts, while the live channel keeps trying indefinitely.
- expected: All WebSocket consumers should use either the same backoff strategy (recommended: infinite with jitter like live-connection) or explicitly document and surface the difference to users.
- actual: Notifications and ticket-feed give up after 10 attempts; live-connection retries forever. The status pill only tracks 3 channels but would not correctly represent the state when some have given up.
- fix: Consolidate WebSocket reconnect logic into a shared utility or change ticket-feed.js:105 to allow infinite retries. Update the status pill to handle per-channel state tracking.
- VERDICT: confirmed (High) — The code is unambiguous and the finding is accurate. app.js:718 checks `if (attempt >= maxAttempts)` and returns early to stop reconnect attempts for notifications WS; ticket-feed.js:105 does the same for the ticket-feed WS; live-connection.js:132-142 has NO such guard and will retry infinitely. voip-softphone.js also retries infinitely at fixed 5s intervals. The status pill (app.js:750-812) corre

### [frontend-js-3] Feature B (create-ticket-overrides) reachability gap: Hub serializer not widened  (OK)
- category: Business Logic | confidence: high | dimension: Frontend JavaScript
- location: apps/inbox_hub/services.py::convert_to_ticket (widened) vs apps/inbox_hub/views.py::HubEmailViewSet (serializer not widened)
- description: Feature B adds the ability to override ticket fields (subject, description, category, due_date, tags) when creating a ticket from an email. The service was widened but the HubEmailViewSet serializer was NOT. Users converting emails via the Hub cockpit cannot use the new override form, only Emails page agents can.
- expected: Both Hub and Emails endpoints should accept and apply the full override set.
- actual: Only Emails endpoint applies all overrides. Hub endpoint ignores subject/description/category/due_date/tags.
- fix: Widen ConvertToTicketSerializer in apps/inbox_hub/serializers.py to include all override fields, and update the viewset to pass them into convert_to_ticket().
- VERDICT: confirmed (High) — Code inspection confirms the finding. The `convert_to_ticket` service at apps/inbox_hub/services.py:100-102 accepts 9 optional parameters (queue, status, assignee, priority, subject, description, category, due_date, tags). However, the ConvertToTicketSerializer (apps/inbox_hub/serializers.py:185-207) defines only 4 writable fields (queue_id, status_id, assignee_id, priority). The HubEmailViewSet.c

### [frontend-js-4] 13 of 14 JS files lack cache-busting query parameters (stale JS risk)  (DISMISSED(fp))
- category: Reliability | confidence: high | dimension: Frontend JavaScript
- location: templates/base.html (lines 181-195): only inbox-hub.js has ?v=8, others have no version parameter
- description: Only inbox-hub.js is cache-busted (?v=8). The other 13 files (app.js, live-bus.js, live-connection.js, etc.) have no version parameter, so they're cached indefinitely. Bug fixes and security patches in these files won't reach users until they manually cache-clear (hard refresh).
- expected: All JS files should be cache-busted on every deploy.
- actual: Only inbox-hub.js has ?v=8. The other 13 files have no version.
- fix: Add version parameters to all static JS includes in base.html using Django's fingerprinting/manifest system or manually add ?v=YYYYMMDD to each script src.
- VERDICT: refuted (Low) — The finding claims "13 of 14 JS files lack cache-busting query parameters," but this is technically inaccurate because Django's production settings use `whitenoise.storage.CompressedManifestStaticFilesStorage` (base.py lines 184-189, prod.py lines 24-27), which **automatically appends a content hash to every static file**. Verified: (1) dev.py uses plain `StaticFilesStorage` (no hashing); (2) prod

### [templates-uiux-2] Password toggle not keyboard accessible  (OK)
- category: Accessibility | confidence: high | dimension: Templates & UI/UX
- location: templates/pages/login.html:42
- description: Password visibility toggle is non-interactive icon with click handler only, no button semantics or keyboard support
- expected: Should be button element or have role button with keyboard handlers
- actual: Uses i element with only click handler
- fix: Convert to button element with aria-label and proper semantics
- VERDICT: confirmed (High) — The password visibility toggle at templates/pages/login.html:42 is a non-interactive `<i>` element (icon) with only a click event listener (line 108). It lacks: (1) `tabindex="0"` for keyboard focus, (2) `role="button"` for semantic button identity, (3) keyboard event handlers for Space/Enter activation, (4) proper ARIA labels (`aria-label` instead of just `title`). The JavaScript handler (lines 1

### [templates-uiux-5] Macro dropdown code references non-existent DOM elements  (OK)
- category: Bug | confidence: high | dimension: Templates & UI/UX
- location: templates/pages/tickets/detail.html:2959-2963
- description: JavaScript code for macro feature queries elements that never exist in HTML, dead code that silently fails
- expected: Either UI should exist or dead code should be removed
- actual: Unreachable dead code with null checks hiding the error
- fix: Remove macro dropdown JavaScript block completely from template
- VERDICT: confirmed (Low) — The finding is confirmed: lines 2959-3090 in templates/pages/tickets/detail.html contain ~44 lines of dead JavaScript code that references DOM elements (#macroBtn, #macroDropdown, #macroSearch, #macroList) that do not exist in the HTML. The code is properly guarded by `if (macroBtn)` at line 2965, which prevents runtime errors since macroBtn is null. However, the code is completely unreachable dea

### [performance-db-1] N+1 Query: Notification List Missing select_related on Recipient  (DISMISSED(fp))
- category: Performance | confidence: high | dimension: Performance & database
- location: apps/notifications/views.py:71-78
- description: The NotificationViewSet.get_queryset() fetches Notification rows without select_related('recipient'). Each notification serialization will trigger a separate User query, causing N+1 on potentially hundreds of notifications per user per session.
- expected: Single-query or minimally-query Notification list via select_related('recipient').
- actual: Notification.objects.filter(recipient=self.request.user) with no select_related; serializer touches each notification.recipient.
- fix: Change line 71-78 to: qs = Notification.objects.filter(recipient=self.request.user).select_related('recipient')
- VERDICT: refuted (Low) — The finding claims an N+1 query problem exists when listing notifications without select_related('recipient'). However, this is incorrect due to DRF's built-in optimization. The NotificationSerializer automatically generates a PrimaryKeyRelatedField(read_only=True) for the recipient FK field (verified at apps/notifications/serializers.py:14-41, where 'recipient' is listed in fields without a custo

### [performance-db-2] Partial Index Condition Mismatch: HubEmail SLA Scan Includes ESCALATED but Index Excludes It  (OK)
- category: Performance | confidence: high | dimension: Performance & database
- location: apps/inbox_hub/models.py:240-244 vs apps/inbox_hub/tasks.py:32-38
- description: The partial index on HubEmail for the SLA-breach hot path defines condition as state IN ['new', 'assigned', 'in_progress', 'pending_agent'], excluding 'escalated'. However, check_hub_sla_breaches task scans state__in=[NEW, ASSIGNED, IN_PROGRESS, PENDING_AGENT, ESCALATED]. Escalated emails fall through to full-table scan instead of using the partial index.
- expected: Partial index condition and task filter must match exactly, or index should cover all scanned states.
- actual: Partial index excludes ESCALATED but task includes ESCALATED in scan, causing emails to bypass the index.
- fix: Either remove ESCALATED from task's active_states list, OR add ESCALATED to partial index condition: condition=Q(state__in=['new','assigned','in_progress','pending_agent','escalated'])
- VERDICT: confirmed (Medium) — The partial index on HubEmail (models.py:240-244) restricts its condition to exactly 4 states: ["new", "assigned", "in_progress", "pending_agent"]. The check_hub_sla_breaches task (tasks.py:32-38) explicitly includes a 5th state, "escalated", in its active_states list and queries all 5 via state__in=[...]. This means PostgreSQL will not be able to fully utilize the partial index for ESCALATED rows

### [performance-db-3] DashboardView Under-Permissioned: Missing HasTenantPermission Check  (OK) [DUP-OF authn-2]
- category: Security | confidence: high | dimension: Performance & database
- location: apps/analytics/views.py:182-196
- description: DashboardView (line 196) is an APIView with only [IsAuthenticated] in permission_classes. It should also include HasTenantPermission to enforce RBAC. Any authenticated user could potentially fetch aggregated dashboard statistics without resource-level permission checks.
- expected: permission_classes = [IsAuthenticated, HasTenantPermission] with resource enforcement.
- actual: permission_classes = [IsAuthenticated] — HasTenantPermission is absent.
- fix: Add HasTenantPermission to permission_classes and set permission_resource = 'dashboard'.
- VERDICT: confirmed (High) — CONFIRMED with important corrections: DashboardView (apps/analytics/views.py:182) is indeed under-permissioned. The actual issues are TWO-FOLD:

1. **CROSS-TENANT DATA LEAK (High):** DashboardView has only `permission_classes = [IsAuthenticated]` (line 196). It lacks both `IsTenantMember` and `HasTenantPermission`. This allows any authenticated user from ANY tenant to access another tenant's aggre

### [performance-db-4] ContactEvent Logging Bypasses Live Signal: Changes Invisible to Broadcast Layer  (OK) [DUP-OF contacts-crm-2]
- category: Data Integrity | confidence: high | dimension: Performance & database
- location: apps/contacts/services.py:44 (last_activity_at via .update()) + apps/contacts/signals.py
- description: The log_contact_event() function updates Contact.last_activity_at using QuerySet.update() which skips post_save signal. This means live broadcast layer never receives 'contact.updated' event when activity is logged. Clients on CRM page won't see activity timestamp change in real-time.
- expected: ContactEvent creation updates Contact.last_activity_at AND broadcasts live event to keep clients in sync.
- actual: Contact.last_activity_at updated via .update() (no signal) so no broadcast occurs.
- fix: Either call broadcast_live_event() manually after .update(), OR change to save-based pattern and accept signal cost, OR update docstring to clarify ContactEvents don't trigger broadcasts.
- VERDICT: confirmed (Medium) — The defect is real and confirmed by reading the actual code. (1) apps/contacts/services.py line 44 calls `Contact.unscoped.filter(pk=contact.pk).update(last_activity_at=timezone.now())` which is a QuerySet.update() operation. (2) QuerySet.update() does NOT trigger post_save signals in Django (documented behavior). (3) The Contact.post_save signal receiver at apps/contacts/signals.py lines 73-79 ca

### [performance-db-5] Reminder Fire Task Missing Atomic Block: Potential Notification Duplication  (OK)
- category: Reliability | confidence: medium | dimension: Performance & database
- location: apps/crm/tasks.py:34-161, lines 102-130
- description: fire_due_reminders task runs outside atomic block intentionally (claim-first watermark before send). If send_notification() raises exception, watermark is stamped but notification failed. Transient errors like Redis timeout cause reminder to be silently skipped with no retry path.
- expected: Notification and broadcast happen atomically with replay on failure, OR retry logic exists for failed broadcasts.
- actual: Notification sends; if broadcast fails, reminder marked done anyway. No retry path exists.
- fix: Wrap notification+broadcast in sub-task or add fallback queue for retry. OR document that transient broadcast failures are fire-and-forget.
- VERDICT: confirmed (Medium) — The finding identifies a real reliability issue, but with significant caveats. Reading the actual code at /home/kavin/crm/apps/crm/tasks.py lines 94-150:

(1) **The title is misleading.** The finding claims "Potential notification duplication," but the code actually produces *missed alerts*, not duplicates. Lines 94-101 explicitly document the intentional design: "Stamping first means a transie

### [performance-db-6] Notification List Query Missing Tenant Filter Safety Check  (DISMISSED(refuted)) [DUP-OF authz-rbac-3]
- category: Data Integrity | confidence: high | dimension: Performance & database
- location: apps/notifications/views.py:71-78
- description: NotificationViewSet.get_queryset() filters by recipient but tenant filter is optional (line 73). If no tenant in context, query returns all notifications for user across ALL tenants. While IsTenantMember should guard, view should enforce tenant scoping as defense-in-depth.
- expected: Tenant filter is mandatory with fallback to .none() if tenant is None.
- actual: Tenant filter optional: if tenant is not None: qs = qs.filter(tenant=tenant) — missing context silently allows leakage.
- fix: Make tenant filter mandatory: qs = qs.filter(tenant=tenant) without if-guard and let permission class catch None.
- VERDICT: refuted (Low) — The finding claims cross-tenant notification leakage due to an optional tenant filter in NotificationViewSet.get_queryset (line 73-74). However, the claimed vulnerability is refuted by three layers of defense: (1) TenantAwareManager.get_queryset() (main/managers.py:35-46) auto-filters all Notification.objects queries by the context-local tenant, returning .none() if no tenant is in context—this fi

### [performance-db-10] Channels Consumer Performs Synchronous DB Query Without database_sync_to_async  (DISMISSED(fp))
- category: Reliability | confidence: high | dimension: Performance & database
- location: apps/tenants/consumers.py:137-145
- description: LiveEventConsumer._is_tenant_member() is correctly wrapped with @database_sync_to_async. However, other consumer classes (ChatConsumer, NotificationConsumer, CallEventConsumer) may have synchronous DB queries without async wrapping, blocking the event loop.
- expected: All DB queries in async consumers wrapped with @database_sync_to_async.
- actual: LiveEventConsumer correctly wrapped; other consumers may have unaudited sync queries.
- fix: Audit all consumer classes and wrap synchronous DB queries with @database_sync_to_async. Consider extracting DB logic to separate sync service layer.
- VERDICT: refuted (Medium) — The finding is **partially accurate but overstated**. After comprehensive code review:

**What is TRUE:**
- The specific location cited (tenants/consumers.py:137-145) correctly has `@database_sync_to_async` wrapping `_is_tenant_member()`.
- All OTHER consumer classes DO wrap their synchronous DB queries with `@database_sync_to_async`: TicketPresenceConsumer has 4 wrapped methods (_is_tenant_member

### [data-model-integrity-3] Fire due reminders notification stampede  (DISMISSED(fp))
- category: Reliability | confidence: high | dimension: Data model & migrations integrity
- location: apps/crm/migrations/0005_reminder_due_notified_at.py:22-27
- description: Migration uses tenant-scoped Reminder.objects with no tenant context, returns .none(), zero backfill, all historical reminders fire on first task run.
- expected: Historical reminders backfilled to prevent stampede.
- actual: Thousands of notifications fire in one beat tick.
- fix: Use Reminder.unscoped.filter() instead of Reminder.objects.filter().
- VERDICT: refuted (Low) — The finding claims the migration "returns .none(), zero backfill, all historical reminders fire on first task run." This is factually incorrect. Testing confirms: (1) In the migration context, `apps.get_model("crm", "Reminder")` returns a plain Django `Manager` (not `ReminderManager`), which sees ALL rows cross-tenant — the claim that it returns `.none()` is false. (2) The migration correctly uses

### [data-model-integrity-4] Fire due reminders backward reschedule race  (OK)
- category: Reliability | confidence: high | dimension: Data model & migrations integrity
- location: apps/crm/tasks.py:70-104
- description: Selects without SELECT FOR UPDATE, backward reschedule between SELECT and UPDATE causes due_notified_at >= scheduled_at, re-arm FALSE forever.
- expected: Reminder fires when scheduled_at <= now.
- actual: Backward rescheduled reminders never fire.
- fix: Refresh reminder after claiming to re-validate, or use SELECT FOR UPDATE.
- VERDICT: confirmed (High) — I verified the fire_due_reminders task at apps/crm/tasks.py:70-104. The race is real: (1) Lines 70-84 use .iterator() to fetch reminders without SELECT FOR UPDATE, creating stale in-memory snapshots. (2) Line 102-104 unconditionally UPDATE due_notified_at=now WHERE pk=reminder.pk, without re-validating the fire condition. (3) If a reschedule backward (to an earlier scheduled_at) happens between th

### [data-model-integrity-5] Account health score validator unreachable  (OK)
- category: Business Logic | confidence: high | dimension: Data model & migrations integrity
- location: apps/contacts/models.py:49-56
- description: Account.clean() enforces 0-100 range but save() never calls full_clean(). Tasks use .update() bypassing validation.
- expected: Range enforced to 0-100.
- actual: Out-of-range values writable.
- fix: Add save() override calling full_clean() or use DecimalRangeValidator.
- VERDICT: confirmed (Medium) — I verified the cited code at apps/contacts/models.py:49-56 and confirmed Account.clean() validates health_score to 0-100 range. I then traced the save path: main/models.py:54-64 shows TenantScopedModel.save() calls super().save() WITHOUT calling full_clean(). I confirmed via import-test that Account.save(health_score=999) persists without validation, while Account.full_clean() correctly raises Val
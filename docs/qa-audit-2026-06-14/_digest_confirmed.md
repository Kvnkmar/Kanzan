
######################################################################
# Critical: 6 (deduped, non-dismissed)
######################################################################

### [billing-1] VoIP Plan Flags Never Seeded - All Plans VoIP Disabled
cat=Business Logic | conf=high | verdict=confirmed | merged=['voip-settings-missing']
LOC: apps/billing/management/commands/seed_plans.py:20-90
DESC: Seed command initializes Free Pro Enterprise plans but omits has_voip has_call_recording max_calls_per_month. All fields default to False False NULL. Every tenant using seeded plan has VoIP completely disabled.
REPRO: Run: python manage.py seed_plans. Check database: SELECT has_voip FROM billing_plan. All rows show False. Attempt to place VoIP call via InitiateCallView. Check_call_limit returns False with message VoIP not available on plan.
EXPECTED: Seeded plans include has_voip=True for Pro and Enterprise, max_calls_per_month set to appropriate values
ACTUAL: All seeded plans have has_voip=False model default, max_calls_per_month=NULL. VoIP feature broken for all new tenants.
FIX: Add has_voip has_call_recording max_calls_per_month to each plan dict in PLANS list. Example: Enterprise should have has_voip=True has_call_recording=True max_calls_per_month=None

### [tenant-isolation-3] InboundEmail Not TenantScopedModel—Requires Manual Tenant Filtering Everywhere
cat=Data Integrity | conf=high | verdict=confirmed | merged=[]
LOC: /home/kavin/crm/apps/inbound_email/models.py:22-75 (model definition)
DESC: InboundEmail inherits from TimestampedModel, not TenantScopedModel, so it does not get the automatic tenant-aware `.objects` manager. Every query on InboundEmail.objects must include an explicit `tenant=` filter to avoid cross-tenant leakage. This design is error-prone—a developer unfamiliar with this exception could use `.objects` without filtering and unknowingly leak data across tenants. The imap_poller.py:337 critical finding above is direct evidence this design has already been violated in production code.
REPRO: A developer adds a new feature that queries InboundEmail without explicit tenant= filtering (as already happened in imap_poller.py line 337). The query returns cross-tenant data.
EXPECTED: InboundEmail should either (a) inherit from TenantScopedModel so filtering is automatic, or (b) have a custom manager that requires explicit tenant= filters and raises an error if omitted.
ACTUAL: InboundEmail uses a plain Django manager. The design relies entirely on developer discipline.
FIX: Refactor InboundEmail to inherit from TenantScopedModel and make tenant required (non-nullable: null=False). This automatically secures all queries via the fail-closed TenantAwareManager. If nullable tenant is truly needed for some reason, create a custom manager that enforces `tenant=` in all queries or allows only `.unscoped` queries with explicit checking.

### [billing-2] OneToOne Subscription FK Blocks Stripe Plan Upgrades
cat=Data Integrity | conf=high | verdict=confirmed | merged=[]
LOC: apps/billing/webhooks.py:135-138 and apps/billing/models.py:107-111
DESC: Subscription model uses OneToOneField to Tenant meaning only one Subscription per Tenant allowed. Webhook handler uses update_or_create on stripe_subscription_id. When tenant upgrades Stripe subscription new subscription with different ID tries to INSERT with same tenant FK violating OneToOne constraint.
REPRO: Create tenant with subscription sub_abc123 linked. Tenant upgrades plan in Stripe old subscription canceled new sub_def456 created. Django webhook receives customer.subscription.created for sub_def456. Code executes update_or_create stripe_subscription_id=sub_def456 defaults={tenant=t1}. Query finds no existing row. INSERT attempts with tenant_id=t1. IntegrityError violates unique constraint on tenant_id field.
EXPECTED: Plan upgrades succeed seamlessly. New subscription cleanly replaces old one without errors.
ACTUAL: Stripe webhook handler fails with IntegrityError. Subscription state becomes inconsistent. Tenant loses access or sees billing errors.
FIX: Change Subscription.tenant from OneToOneField to ForeignKey allowing multiple subscriptions per tenant. Alternatively in webhook handler query by tenant first then DELETE old subscription before creating new one. Or change update_or_create lookup from stripe_subscription_id to tenant.

### [messaging-1] Cross-tenant mention disclosure via global User query without tenant membership validation
cat=Security | conf=high | verdict=confirmed | merged=['messaging-2']
LOC: apps/messaging/mentions.py:74
DESC: notify_mentions() resolves mentioned users from the global User table without validating tenant membership. A user can mention any UUID from any tenant and trigger notifications and ID leakage across tenant boundaries.
REPRO: 1. User A in Tenant 1 sends message with @[name](user:<Tenant 2 user UUID>). 2. notify_mentions() at line 74 queries User.objects.filter(id__in=user_ids) with no tenant check. 3. Tenant 2 user receives mention notification in Tenant 1 context.
EXPECTED: Only query users who are active members of message.conversation.tenant via TenantMembership.
ACTUAL: Line 74: User.objects.filter(id__in=user_ids) with no tenant filter. The tenant parameter passed to notify_mentions() is unused.
FIX: Change line 74 to: User.objects.filter(id__in=user_ids).filter(tenantmembership__tenant=tenant, tenantmembership__is_active=True). Apply same fix in views.py:398 and consumers.py:317.

### [attachments-2] Missing Authorization Check on Attachment Upload Target
cat=Security | conf=high | verdict=confirmed | merged=[]
LOC: apps/attachments/serializers.py:106-124, AttachmentUploadSerializer.validate()
DESC: The AttachmentUploadSerializer validates that the target object exists and belongs to the current tenant, but does NOT check if the uploading user has permission to modify that object. This allows any tenant member to upload attachments to objects they shouldn't have access to. For example, an agent can upload attachments to tickets they're not assigned to, or to internal comments they don't have access to.
REPRO: 1. Create two tickets: Ticket-A assigned to Agent-X, Ticket-B assigned to Agent-Y. 2. As Agent-X, make a POST request to /api/v1/attachments/attachments/ with content_type=tickets.ticket, object_id={Ticket-B-UUID}, and a file. 3. The upload succeeds despite Agent-X not being assigned to Ticket-B.
EXPECTED: The upload should fail with a 403 Forbidden response indicating the user doesn't have permission to attach files to that object.
ACTUAL: The upload succeeds (201 Created) and the attachment is associated with Ticket-B.
FIX: Add authorization checks to AttachmentUploadSerializer.validate() that verify the user can modify the target object. Reuse the same logic as the retrieval fix above. For Tickets, verify the user can see the ticket via agent_visible_tickets_q (agents can only attach to their assigned tickets or unassigned tickets they created, managers/admins can attach to any ticket). For Comments, verify the user can modify comments on the parent object. For Messages, verify conversation membership.

### [settings-secrets-1] DJANGO_DEBUG split-default footgun: unset env var defaults to True in __init__.py but False in base.py
cat=Security | conf=high | verdict=confirmed | merged=['settings-secrets-3', 'settings-secrets-4']
LOC: main/settings/__init__.py:9 vs main/settings/base.py:17
DESC: The settings init file defaults DJANGO_DEBUG to True when unset (line 9), but base.py defaults it to False (line 17). This creates a critical split: an unset DJANGO_DEBUG env var loads dev.py which sets ALLOWED_HOSTS=['*'], CSRF_COOKIE_SECURE=False, SESSION_COOKIE_SECURE=False, and no HSTS headers. A production deployment missing the DJANGO_DEBUG=False env var silently loads dev settings instead of prod.py, exposing DEBUG mode in production with all the security implications: SQL exception details in error pages, static file serving from memory, disabled CSRF token checking via ALLOWED_HOSTS=*, etc.
REPRO: 1. Deploy to production. 2. Unset DJANGO_DEBUG env var (or omit it from .env). 3. Restart Django. 4. DJANGO_DEBUG defaults to True in __init__.py, loads dev.py, sets ALLOWED_HOSTS=['*'], CSRF_COOKIE_SECURE=False. 5. Trigger an exception to see SQL details in error page.
EXPECTED: An unset DJANGO_DEBUG should fail safely or default consistently. Either: (a) base.py and __init__.py should share the same default (False), or (b) __init__.py should fail loudly if DJANGO_DEBUG is unset, forcing explicit configuration.
ACTUAL: __init__.py defaults True, base.py defaults False. Unset env var silently loads dev.py with ALLOWED_HOSTS=['*'].
FIX: Change main/settings/__init__.py line 9 to: `if env.bool("DJANGO_DEBUG", default=False):` to align with base.py. This makes unset DJANGO_DEBUG fail safely to prod mode. Alternatively, raise an error if DJANGO_DEBUG is not set in production.

######################################################################
# High: 27 (deduped, non-dismissed)
######################################################################

### [templates-uiux-2] Password toggle not keyboard accessible
cat=Accessibility | conf=high | verdict=confirmed | merged=[]
LOC: templates/pages/login.html:42
DESC: Password visibility toggle is non-interactive icon with click handler only, no button semantics or keyboard support
REPRO: Navigate to login, tab through form, password toggle is skipped, spacebar does not work
EXPECTED: Should be button element or have role button with keyboard handlers
ACTUAL: Uses i element with only click handler
FIX: Convert to button element with aria-label and proper semantics

### [websockets-3] TicketPresenceConsumer does not validate agent-level ticket visibility; allows unauthorized presence access
cat=Bug | conf=high | verdict=confirmed | merged=[]
LOC: apps/tickets/consumers.py:150-156 (_can_access_ticket method)
DESC: The _can_access_ticket check only verifies that the ticket exists in the tenant. It does NOT check whether the user has permission to view the ticket. Per apps/tickets/access.py, agents should only see tickets assigned to them OR tickets they created and are unassigned. Viewers have no ticket access. This consumer allows a viewer-role user or unauthorized agent to join the presence group for a ticket they should not see, revealing which agents are viewing that ticket.
REPRO: 1. Create a ticket assigned to Agent A. 2. Connect to ws/tickets/{ticket_id}/presence/ as Agent B or a Viewer-role user. 3. The connection succeeds (agent_joined broadcast is sent) even though they should not access this ticket. 4. The presence data reveals which users are viewing a ticket the malicious user should not have access to.
EXPECTED: The _can_access_ticket check should use the agent_visible_tickets_q() / agent_can_see_ticket() logic from apps/tickets/access.py to ensure only authorized users can join presence.
ACTUAL: Any tenant member can join the presence group for any tenant ticket, bypassing documented ticket visibility rules.
FIX: 1. Modify _can_access_ticket to use the agent_can_see_ticket helper from apps/tickets/access.py. 2. Check if the user is admin/manager (hierarchy_level <= 20 via effective_role) OR satisfies agent_visible_tickets_q(user). 3. Return False for viewers and agents without access. 4. Use effective_role to account for temporary role grants.

### [knowledge-1] Full-Text Search Broken on SQLite (Dev Environment)
cat=Bug | conf=high | verdict=confirmed | merged=[]
LOC: apps/knowledge/search.py:24-28, apps/knowledge/models.py:120, apps/knowledge/signals.py:18-19
DESC: The kb_search function uses Django PostgreSQL-specific `SearchQuery` and `SearchRank` objects to query the `search_vector` field. On SQLite (the dev database), these classes are incompatible and silently fail to produce results. The `SearchVectorField` from `django.contrib.postgres.search` requires PostgreSQL's `to_tsvector` function. The docstring in views.py (line 546) claims a fallback to `icontains` for SQLite, but the actual implementation contains no such fallback code.
REPRO: 1. Start the dev server with SQLite. 2. Create and publish a KB article. 3. Call GET /api/v1/knowledge/search/?q=<terms> with authenticated user. 4. Observe empty results list returned even though matching articles exist.
EXPECTED: Search results should be returned using full-text search on PostgreSQL, or icontains fallback on SQLite as documented in the API schema.
ACTUAL: On SQLite, the SearchQuery filter causes Django to internally raise EmptyResultSet (caught silently during queryset evaluation), resulting in zero results even when matching articles exist. The documented fallback behavior does not exist.
FIX: Implement the documented SQLite fallback in kb_search(): detect the database engine (as done in signals.py:17-19) and use `Article.objects.filter(title__icontains=query) | Article.objects.filter(content__icontains=query)` when not on PostgreSQL. Alternatively, update the API documentation to clarify that search requires PostgreSQL.

### [inbox-hub-access-3] first_responded_at Never Written, Causing Response SLA to Always Trigger
cat=Business Logic | conf=high | verdict=confirmed | merged=['inbox-hub-engine-1']
LOC: apps/inbox_hub/tasks.py:49-57 (check_hub_sla_breaches function) vs. apps/inbox_hub/models.py:183 (HubEmail.first_responded_at field)
DESC: The HubEmail.first_responded_at field is defined in the model but is never written by any code in the repo. The SLA breach check at task.py:49 tests `if not he.response_breached and he.first_responded_at is None`, which will always be True until response_breached is flagged. This means the response-breach check has no concept of 'the customer replied, so the breach is moot'. The deadline alone triggers the breach, regardless of whether the email has been responded to. The field is dead.
REPRO: 1. Create a HubEmail with sla_response_due_at = now + 1 hour. 2. Immediately an agent posts a reply to the customer. 3. Wait for the SLA deadline to pass. 4. check_hub_sla_breaches task runs and flags response_breached=True. 5. No code ever sets first_responded_at, so the breach fires regardless of the reply.
EXPECTED: When an email receives an agent response, first_responded_at should be stamped, and the response-breach check should skip flagging if first_responded_at is set.
ACTUAL: first_responded_at is never written to by any code. The response-breach logic is: `if first_responded_at is None and now >= deadline: flag breach`. Since first_responded_at is always NULL, every email in an active state breaches its response deadline eventually (unless response_breached is already True).
FIX: Implement the missing write-site in the reply logic (not yet in MVP): when an agent replies to the customer, set first_responded_at=now and optionally update the response_breached flag. Or remove the field and accept that this is a deadline-only SLA. Current state is a broken feature: the column exists but is non-functional.

### [tickets-signals-2] Pipeline stage → kanban column sync matches by NAME, silently breaks on column rename
cat=Business Logic | conf=high | verdict=confirmed | merged=['kanban-3']
LOC: apps/tickets/signals.py:618
DESC: When a ticket's pipeline_stage changes, the code tries to find the target kanban column by matching `Column.name__iexact=new_stage_name` (line 618). If an admin renames a column, the signal will fail to find the matching column and silently do nothing. This means renamed columns break the pipeline→kanban sync, and old cards stay in the wrong columns forever.
REPRO: 1. Create a ticket with pipeline_stage 'Qualification' (card correctly placed in 'Qualification' column). 2. Admin renames the column to 'Lead Qualification'. 3. Ticket moves to next stage ('Proposal'). 4. Later, ticket moves back to 'Qualification' stage. 5. Signal tries to find column 'Qualification', fails, card never syncs to renamed column.
EXPECTED: When a column is renamed, the mapping between stage names and column names should be preserved via a FK or the sync should fail with a warning.
ACTUAL: Column renames silently break the stage→column sync. No error is logged. Cards stay in the wrong columns.
FIX: Replace name-based matching with a FK: add a `pipeline_stage` FK to Column (nullable, for non-pipeline boards). Change line 616-619 to filter by `board=board, pipeline_stage=instance.pipeline_stage` instead of `board=board, name__iexact=new_stage_name`. Alternatively, log a WARNING when the name match fails.

### [inbox-hub-engine-2] SLA Response Breach Auto-Escalates Every Deadline (Deduplication Missing)
cat=Business Logic | conf=high | verdict=confirmed | merged=[]
LOC: apps/inbox_hub/tasks.py:55
DESC: When check_hub_sla_breaches detects a response breach (which, as noted above, always happens at deadline), it calls escalate_hub_email(). The breach flag (response_breached) prevents the breach from firing multiple times, but escalate_hub_email itself has no deduplication. This means on the first breach, escalation_count increments, escalated_to is set, and state transitions (if legal). On the second task run, response_breached is already True so the breach block is skipped (line 49 guard). However, if check_hub_sla_breaches is somehow called again for a NEW hub_email instance (race condition, transaction rollback, or cross-process state desync), escalation could re-fire.
REPRO: 1. Create a HubEmail in NEW state, deadline in past. 2. Run check_hub_sla_breaches; observe escalation_count=1, state=ESCALATED. 3. Reload the same HubEmail (stale object). 4. Run check_hub_sla_breaches again with the stale object. 5. The response_breached flag is checked on the in-memory object (not fresh from DB), so the guard at line 49 does not fire. 6. escalation_count increments again.
EXPECTED: Either: (a) re-fetch response_breached from the DB before the check, or (b) include escalation deduplication in the escalate_hub_email function (e.g., only escalate if escalation_count == 0).
ACTUAL: The response_breached guard prevents re-firing within a single task run, but stale in-memory state or concurrent calls could cause duplicate escalations.
FIX: Refactor the check to use `HubEmail.unscoped.filter(pk=he.pk, response_breached=False)` to ensure fresh DB state. Or: add an escalation_breached flag (like response_breached) to prevent escalate_hub_email from firing more than once.

### [custom-fields-agents-4] Agent presence freshness uses server-local time for working_hours check, not tenant timezone
cat=Business Logic | conf=high | verdict=confirmed | merged=[]
LOC: apps/agents/models.py:234-255 (_within_working_hours method)
DESC: The is_assignable property checks auto_away_outside_hours via _within_working_hours(), which uses timezone.localtime() (line 246). This resolves to the Django project's TIME_ZONE setting (Asia/Kuala_Lumpur in base.py:149), NOT the user's or tenant's timezone. If a tenant is in a different timezone (e.g., US/Pacific), an agent's working hours will be evaluated against the server's local 9 AM–5 PM (KL time) instead of their intended 9 AM–5 PM (Pacific time). This causes incorrect auto-away behavior: an agent might be marked unassignable at 8 AM Pacific (which is 12 AM KL, next day) even though it's within their configured hours, or assignable at 6 PM Pacific (which is 12 AM+1 day KL) when they should be away.
REPRO: 1. Tenant timezone = US/Pacific. Agent auto_away_outside_hours=True, working_hours={"mon": {"enabled": true, "start": "09:00", "end": "17:00"}}. 2. Server local time (KL) = Mon 09:30 AM. Current Pacific time = Sun 9:30 PM (previous day). 3. Call is_assignable property. Expected: False (outside Pacific hours on Sunday). Actual: True (within KL hours on Monday).
EXPECTED: Working hours should be evaluated against the agent's timezone (or tenant timezone), not the server's hardcoded TIME_ZONE. An agent in Pacific timezone should be assignable only during 09:00–17:00 Pacific time.
ACTUAL: Working hours are evaluated against the server-local time (Asia/Kuala_Lumpur), causing cross-timezone misalignment. Agents in different timezones are incorrectly marked assignable/away.
FIX: Pass the user's or tenant's timezone to _within_working_hours(), and use timezone.localtime(tz=pytz.timezone(tenant.timezone or 'UTC')) instead of the bare call. Store agent/tenant timezone in the model. For now, fail-open (return True, assume within hours) when auto_away_outside_hours is set but timezone data is missing.

### [frontend-js-3] Feature B (create-ticket-overrides) reachability gap: Hub serializer not widened
cat=Business Logic | conf=high | verdict=confirmed | merged=[]
LOC: apps/inbox_hub/services.py::convert_to_ticket (widened) vs apps/inbox_hub/views.py::HubEmailViewSet (serializer not widened)
DESC: Feature B adds the ability to override ticket fields (subject, description, category, due_date, tags) when creating a ticket from an email. The service was widened but the HubEmailViewSet serializer was NOT. Users converting emails via the Hub cockpit cannot use the new override form, only Emails page agents can.
REPRO: 1. Open Inbox Hub (/inbox-hub/) and select an email. 2. Click 'Convert to ticket' and fill override fields. 3. Submit. 4. Observe subject/description/category/due_date/tags are NOT applied. 5. Go to Emails page, click 'Create ticket' with same fields. 6. Observe ALL fields including subject/description/category work.
EXPECTED: Both Hub and Emails endpoints should accept and apply the full override set.
ACTUAL: Only Emails endpoint applies all overrides. Hub endpoint ignores subject/description/category/due_date/tags.
FIX: Widen ConvertToTicketSerializer in apps/inbox_hub/serializers.py to include all override fields, and update the viewset to pass them into convert_to_ticket().

### [tickets-core-1] Missing cross-tenant validation for contact, company, and pipeline_stage in create/update serializers
cat=Data Integrity | conf=high | verdict=confirmed | merged=[]
LOC: apps/tickets/serializers.py:516-617 (TicketCreateSerializer)
DESC: TicketCreateSerializer validates that `status` and `queue` belong to the current tenant (lines 562-582), but provides NO validation for `contact`, `company`, `assignee`, or `pipeline_stage` (all ForeignKey fields to TenantScopedModel resources). An attacker can supply a UUID for contact/company from a different tenant, and the serializer will accept it.
REPRO: POST /api/v1/tickets/tickets/ with { "subject": "...", "contact": "<contact_id_from_other_tenant>" }. The ticket is created with the cross-tenant contact.
EXPECTED: The serializer should validate that `contact`, `company`, and `pipeline_stage` belong to the current tenant before accepting them, similar to validate_queue (line 572).
ACTUAL: No validation occurs. Attackers can cross-link tickets to other tenants' data.
FIX: Add validate_contact, validate_company, validate_assignee, and validate_pipeline_stage methods checking `value.tenant_id == request.tenant.id`.

### [tickets-core-2] Ticket.clean() assignee-membership validation never invoked during create/update
cat=Data Integrity | conf=high | verdict=confirmed | merged=['tickets-core-5']
LOC: apps/tickets/models.py:579-589, serializers.py:588-617, views.py:597-602
DESC: Ticket.clean() validates assignee is a tenant member (lines 579-589), but neither serializer.create() nor perform_create() calls full_clean(), bypassing the check. An assignee from another tenant can be set without error.
REPRO: POST /api/v1/tickets/tickets/ with { "assignee": "<user_id_from_other_tenant>" }. DRF's unscoped User queryset allows the invalid assignment.
EXPECTED: Call ticket.full_clean() after super().create() in the serializer, or add validate_assignee in the serializer.
ACTUAL: The model validation is defined but never executed at the API boundary.
FIX: In TicketCreateSerializer.create() (line 611), add `ticket.full_clean()` before `return ticket`. Also validate in perform_update path (views.py:640).

### [contacts-crm-2] build_contact_context cache never invalidated when linked tickets change
cat=Data Integrity | conf=high | verdict=confirmed | merged=['performance-db-4']
LOC: apps/contacts/context.py:23-119; apps/contacts/signals.py:73-88; apps/tickets/signals.py (no cache invalidation)
DESC: The contact_context_v2 cache (apps/contacts/context.py:40-118) is populated for 60 seconds and used by both ContactViewSet.context and HubEmailViewSet.context to build a ticket summary. When ANY ticket linked to the contact is created, updated, or closed, the cache key is NEVER invalidated. This means a ticket-detail page loads the cached snapshot (showing 'open_tickets=3'), but if another agent closes a ticket in the meantime, the page still shows 'open_tickets=3' until 60s expire. The signal receiver broadcast_contact_save (apps/contacts/signals.py:73-79) does NOT invalidate the contact_context_v2 cache, nor do any ticket signals (apps/tickets/signals.py has no contact cache logic).
REPRO: (1) Open Contact A in the ticket-detail sidebar; the contact_context_v2 cache is populated with 'open_tickets=3'. (2) In another browser tab, close one of that contact's tickets. (3) Go back to the first tab's contact sidebar; it STILL shows 'open_tickets=3' until 60 seconds pass. (4) Refresh the page; now it shows the correct 'open_tickets=2'.
EXPECTED: When a ticket.status changes (especially to closed), the contact_context cache for that ticket's contact should be cleared immediately. When a contact is edited, its own cached context should be cleared.
ACTUAL: No cache invalidation occurs. The cache lives for a fixed 60s regardless of data mutations. Clients receive stale aggregated stats (open_tickets, avg_csat, recent_tickets list) until TTL expires.
FIX: In apps/tickets/signals.py, add a post_save receiver on Ticket that clears the contact_context_v2 cache if ticket.contact_id changes or ticket.status.is_closed changes. Also add a post_delete receiver. The cache key is f'contact_context_v2:{tenant_id}:{contact_id}', so call cache.delete(key) in the signal handler.

### [messaging-4] Message.body TextField allows blank in serializer despite being required in model
cat=Data Integrity | conf=high | verdict=confirmed | merged=[]
LOC: apps/messaging/models.py:133 + apps/messaging/serializers.py:127
DESC: Message.body is a required TextField with no blank=True, but MessageCreateSerializer.body allows_blank=True and required=False. Empty messages are persisted because save() bypasses full_clean().
REPRO: 1. POST /conversations/{id}/messages/ with {body: '', parent: null}. 2. Serializer allows (allow_blank=True). 3. Message.save() persists empty message (no full_clean() call). 4. Empty message row appears in conversation.
EXPECTED: Reject blank bodies at serializer level: validate_body() should raise if value.strip() is empty.
ACTUAL: Serializer field at line 127: allow_blank=True, required=False, default=''. Empty messages persist.
FIX: Update MessageCreateSerializer.validate_body() at line 154: 'def validate_body(self, value): value = (value or "").strip(); if not value: raise serializers.ValidationError("Message body cannot be empty."); return value'

### [custom-fields-agents-1] Company custom fields never synced to CustomFieldValue
cat=Data Integrity | conf=high | verdict=confirmed | merged=[]
LOC: apps/custom_fields/signals.py (lines 20-43) + apps/contacts/signals.py
DESC: The custom_fields sync signal receivers exist ONLY for Ticket (sync_ticket_custom_fields) and Contact (sync_contact_custom_fields), but NOT for Company. The Company model has a `custom_data` JSONField (apps/contacts/models.py:87-91) and is declared in ModuleType.COMPANY (custom_fields/models.py:34), but no post_save signal in contacts/signals.py connects Company saves to sync_custom_field_values(). This means Company custom field values are never indexed in CustomFieldValue rows, making them unsearchable/unfilterable via the API and invisible to the query-based EAV layer.
REPRO: 1. Create a custom field definition with module='company'. 2. Update a Company instance with custom_data set (e.g. via PATCH /companies/{id}). 3. Query CustomFieldValue with field__module='company' and object_id=<company_uuid>. Result: 0 rows returned, even though custom_data was populated.
EXPECTED: After a Company.save(), CustomFieldValue rows should be created/updated to mirror the custom_data dict, via the sync_custom_field_values() service (matching the Ticket and Contact behavior).
ACTUAL: Company saves do not trigger sync_custom_field_values(). CustomFieldValue rows are never created, so Company custom fields exist in the schema but produce no queryable data.
FIX: Add a post_save signal receiver for Company in apps/contacts/signals.py (mirroring sync_ticket_custom_fields and sync_contact_custom_fields), or add Company receiver to apps/custom_fields/signals.py. Call sync_custom_field_values(instance, module='company') on Company.post_save when custom_data is present.

### [inbox-hub-access-4] Partial Index Condition in HubEmail Does Not Match SLA Breach Task
cat=Performance | conf=high | verdict=confirmed | merged=['inbox-hub-engine-3']
LOC: apps/inbox_hub/models.py:240-244 (ih_email_active_sla_due index condition) and apps/inbox_hub/tasks.py:32-37 (active_states list)
DESC: The partial index on sla_response_due_at filters for states [NEW, ASSIGNED, IN_PROGRESS, PENDING_AGENT] but the check_hub_sla_breaches task scans active_states = [NEW, ASSIGNED, IN_PROGRESS, PENDING_AGENT, ESCALATED]. The task can hit rows in ESCALATED state that are not covered by the index, causing a full table scan for that state.
REPRO: 1. Create multiple HubEmails, some in ESCALATED state with sla_response_due_at set. 2. Run the check_hub_sla_breaches task with query logging enabled. 3. Observe that the query does not use the ih_email_active_sla_due index for ESCALATED rows (missing from the condition), falling back to a full table scan.
EXPECTED: The index condition and the task's active_states list should match, or the task should only scan states covered by the index.
ACTUAL: Index condition: Q(state__in=['new', 'assigned', 'in_progress', 'pending_agent']). Task scans: [NEW, ASSIGNED, IN_PROGRESS, PENDING_AGENT, ESCALATED]. ESCALATED rows are not in the partial index, so the planner cannot use it for them.
FIX: Update the index condition in models.py line 242 to include ESCALATED: `condition=Q(state__in=['new', 'assigned', 'in_progress', 'pending_agent', 'escalated'])`

### [tickets-services-sla-1] merge_tickets and split_ticket lose lock immediately after select_for_update().exists()
cat=Reliability | conf=high | verdict=confirmed | merged=['tickets-signals-3']
LOC: apps/tickets/services.py:1516-1518 (merge_tickets), 1660 (split_ticket)
DESC: The docstring claims both tickets are 'locked with select_for_update() for the duration of the transaction to prevent concurrent modifications.' However, the lock is lost immediately after the .exists() call because the queryset is not assigned or iterated. The .exists() call closes the database cursor and releases the lock before any subsequent data movement occurs. The comment at line 1516-1518 and 1660 shows a call to select_for_update().filter(...).exists() but the result is discarded, so the lock is never held across the merge/split operations.
REPRO: 1. Start a merge_tickets() or split_ticket() transaction in one worker. 2. In a concurrent worker, fetch the same ticket(s) and modify them (e.g., update status or assignee). 3. The concurrent modification will NOT be blocked because the lock was released after .exists().
EXPECTED: Both tickets should be locked for the entire duration of the transaction, preventing concurrent modifications to comments, activities, and attachments.
ACTUAL: The lock is released immediately after the .exists() call, allowing concurrent modifications to slip in between the lock and the first .update() call.
FIX: Assign the queryset to a variable to keep the lock alive, or iterate over it in a for loop. For merge_tickets: 'ticket_pks = list(Ticket.unscoped.select_for_update().filter(pk__in=[primary.pk, secondary.pk]).values_list('pk', flat=True))' before the comment/activity moves. Or simpler: re-fetch under select_for_update() before each .update() call.

### [feature-a-reminder-5] Race condition: recipient user deleted between fetch and send_notification
cat=Reliability | conf=medium | verdict=confirmed | merged=['custom-fields-agents-5']
LOC: apps/crm/tasks.py:86-117
DESC: The task fetches reminder.assigned_to into memory at line 90, then calls send_notification at line 117. If the user is deleted between these points, send_notification will raise an IntegrityError when trying to insert a Notification row with a dangling recipient_id FK. The watermark is already stamped (claim-first pattern), so the exception is caught and logged, leaving no notification delivered and no alert to administrators.
REPRO: 1. Create a reminder assigned to User A. 2. Fire_due_reminders begins executing. 3. Between lines 90 and 117, delete User A. 4. send_notification raises IntegrityError on the Notification.save(). 5. Exception caught, logged, task continues.
EXPECTED: If a recipient user is deleted, the task should skip gracefully or find an alternative recipient (e.g., tenant admin).
ACTUAL: send_notification raises IntegrityError, which is caught and logged. The reminder is marked notified but no notification was delivered. No escalation to administrators.
FIX: Check recipient.id validity or catch IntegrityError explicitly and log a higher-level warning. Alternatively, use `select_related` and `refresh_from_db()` to detect if the user no longer exists.

### [frontend-js-2] Multiple WebSocket reconnect implementations with inconsistent backoff caps
cat=Reliability | conf=high | verdict=confirmed | merged=[]
LOC: static/js/app.js:690-722 (notifications WS @ 10 attempts, 30s max) vs static/js/ticket-feed.js:19-108 (ticket-feed @ 10 attempts, 30s max) vs static/js/live-connection.js:42-167 (live @ infinite retries, 30s max)
DESC: The application has 3 independent WebSocket consumers (notifications, ticket-feed, live-connection) each with their own reconnection logic. All use exponential backoff but with different max-attempt caps: notifications and ticket-feed both cap at 10 attempts (then stop reconnecting), while live-connection has infinite retries. This creates inconsistent resilience: tabs that lose connectivity may permanently drop the notifications and ticket-feed feeds after 10 failed attempts, while the live channel keeps trying indefinitely.
REPRO: 1. Open any authenticated page with all 3 channels active (dashboard, ticket list). 2. Simulate network failure by blocking WebSocket in DevTools network conditions. 3. Wait 10 reconnection attempts on ticket-feed (roughly 17 minutes at max backoff) and notifications WS. 4. Observe that those 2 channels stop retrying while live-connection continues. 5. Restore network. 6. Note that ticket-feed and notifications do NOT automatically reconnect (dead forever), but live-connection does.
EXPECTED: All WebSocket consumers should use either the same backoff strategy (recommended: infinite with jitter like live-connection) or explicitly document and surface the difference to users.
ACTUAL: Notifications and ticket-feed give up after 10 attempts; live-connection retries forever. The status pill only tracks 3 channels but would not correctly represent the state when some have given up.
FIX: Consolidate WebSocket reconnect logic into a shared utility or change ticket-feed.js:105 to allow infinite retries. Update the status pill to handle per-channel state tracking.

### [data-model-integrity-4] Fire due reminders backward reschedule race
cat=Reliability | conf=high | verdict=confirmed | merged=[]
LOC: apps/crm/tasks.py:70-104
DESC: Selects without SELECT FOR UPDATE, backward reschedule between SELECT and UPDATE causes due_notified_at >= scheduled_at, re-arm FALSE forever.
REPRO: Reschedule reminder backward between task SELECT and UPDATE, due_notified_at becomes greater than new scheduled_at.
EXPECTED: Reminder fires when scheduled_at <= now.
ACTUAL: Backward rescheduled reminders never fire.
FIX: Refresh reminder after claiming to re-validate, or use SELECT FOR UPDATE.

### [authn-2] DashboardView bypasses tenant RBAC with IsAuthenticated-only permission
cat=Security | conf=high | verdict=confirmed | merged=['analytics-exports-1', 'performance-db-3']
LOC: /home/kavin/crm/apps/analytics/views.py
DESC: The DashboardView has only IsAuthenticated permission, no HasTenantPermission check. This bypasses role-based access control; admins cannot deny dashboard access via roles.
REPRO: Create a user in tenant A. Navigate to GET /api/v1/analytics/ on a different tenant B's subdomain. Without HasTenantPermission, the endpoint processes the request.
EXPECTED: Only authenticated members of the current tenant can access DashboardView via HasTenantPermission.
ACTUAL: DashboardView has only IsAuthenticated, relying entirely on queryset filtering for isolation.
FIX: Add HasTenantPermission to DashboardView.permission_classes and define analytics.view codename gated by role.

### [authn-3] Invitation tokens are reusable if races occur during acceptance
cat=Security | conf=high | verdict=confirmed | merged=['messaging-3']
LOC: /home/kavin/crm/apps/accounts/views.py:686-758
DESC: Invitation tokens are checked only against is_accepted boolean, never marked consumed. Same token could be used multiple times if race conditions occur during acceptance.
REPRO: Intercept invitation token. Use it to accept for a different email address before legitimate recipient accepts.
EXPECTED: Each token consumed exactly once upon successful acceptance.
ACTUAL: Tokens have no consumption timestamp; only is_accepted checked.
FIX: Add consumed_at timestamp to Invitation. Check and set atomically in accept_invitation. Enforce email matching before acceptance.

### [authz-rbac-2] Raw role.hierarchy_level used instead of effective_role in 20+ code sites, breaking temp-role grants
cat=Security | conf=high | verdict=confirmed | merged=['tickets-core-8', 'kanban-4', 'custom-fields-agents-8', 'inbox-hub-engine-6']
LOC: apps/agents/services.py:226, apps/analytics/services.py:52, apps/kanban/serializers.py:188, apps/tickets/views.py:576, and 16+ other files
DESC: Throughout the codebase, ~20 code sites filter by raw membership.role.hierarchy_level instead of membership.effective_role.hierarchy_level. When a user is granted a temporary elevated role (e.g., Agent→Manager), the effective_role property correctly returns the temp role, but these 20 sites still check the permanent role. This means a temp-promoted agent has inconsistent scoping: API permission classes see them as elevated, but list querysets, analytics, kanban filtering, and email auto-assignment still treat them at their original level. This violates the principle that a temporary role grant should apply uniformly.
REPRO: 1. Grant Agent (level 30) a temporary Manager role (level 20). 2. Agent calls API endpoint → HasTenantPermission correctly elevates them (uses effective_role). 3. Agent views /tickets/ page → tickets/views.py line 576 uses raw role, filtering them as Agent via agent_visible_tickets_q. 4. Agent views /analytics/ → apps/analytics/services.py line 52 also uses raw role, keeping them Agent-scoped. 5. Inbound email auto-assign uses pick_email_agent with role__hierarchy_level==30, excluding the temp-Manager.
EXPECTED: Temporary role elevation should apply consistently across API, frontend querysets, analytics, badge counts, and auto-assignment.
ACTUAL: Temporary role elevation applies only to API permission checks; list/detail querysets, analytics, kanban, and email auto-assignment ignore it and use permanent role.
FIX: Find-and-replace all membership.role.hierarchy_level with membership.effective_role.hierarchy_level (20+ sites). Add a linter rule to enforce 'must use effective_role' pattern.

### [tickets-core-7] bulk_action delete permission check uses raw role instead of effective_role
cat=Security | conf=high | verdict=confirmed | merged=[]
LOC: apps/tickets/views.py:1376-1391 (TicketViewSet.bulk_action)
DESC: The delete action in bulk_action checks `membership.role.hierarchy_level > 20` instead of `effective_role.hierarchy_level`. Users with temporary Manager elevation are denied despite their temp permissions.
REPRO: (1) Grant Agent a temporary Manager role. (2) Call bulk_action with action='delete'. (3) Permission check uses raw role, denies access.
EXPECTED: Use `effective_role.hierarchy_level` to respect temporary promotions.
ACTUAL: The code checks `role.hierarchy_level` (raw role), bypassing temp permissions.
FIX: Change line 1386 to `membership.effective_role.hierarchy_level > 20`.

### [websockets-1] VoIP CallEventConsumer broadcasts call metadata to all tenant members without per-user scoping
cat=Security | conf=high | verdict=confirmed | merged=['websockets-2']
LOC: apps/voip/consumers.py:30-90 (CallEventConsumer) + apps/voip/services.py:358-384 (_broadcast_call_event)
DESC: CallEventConsumer joins a tenant-wide group (`voip_{tenant_id}`) with NO per-user/per-extension scoping. The broadcast handler (_broadcast_call_event) publishes full call metadata including caller_number, callee_number, contact_id, and ticket_id to every authenticated tenant member. A viewer-role member or agent without CRM access can monitor all confidential calls in the tenant.
REPRO: 1. Create a VoIP call while viewing CallEventConsumer WebSocket traffic from any authenticated tenant member. 2. Observe that the full call payload with caller_number, callee_number, contact_id, and ticket_id is broadcast to ALL connected clients in the tenant, regardless of their role. 3. A viewer-role user can infer confidential call patterns and contact information.
EXPECTED: Call metadata should be scoped to the users involved (extension owners/assignees) or admins only. Viewer-role members and users without relevant CRM permissions should NOT receive call event broadcasts.
ACTUAL: All authenticated tenant members receive full call metadata via tenant-wide group broadcast, regardless of role or access permissions.
FIX: 1. Add per-user/per-extension scoping: modify CallEventConsumer to join extension-specific groups (e.g., `voip_extension_{extension_id}` + admin group). 2. In _broadcast_call_event, enumerate allowed recipients (extension owners, admins, assigned ticket agents) and fan out to individual user groups instead of the tenant-wide group. 3. Filter call payloads server-side to only include metadata that a recipient's role permits.

### [attachments-1] Missing Object-Level Authorization on Attachment Retrieval
cat=Security | conf=high | verdict=confirmed | merged=['attachments-5', 'attachments-6']
LOC: apps/attachments/views.py:164, AttachmentViewSet.permission_classes
DESC: The AttachmentViewSet uses only IsAuthenticated and IsTenantMember permission classes, with no object-level authorization checks. Both permission classes have has_object_permission() that returns True unconditionally. This allows any authenticated tenant member to download attachments attached to ANY object in the tenant, regardless of whether they have permission to view that object. For example, an agent assigned only to Ticket A can download attachments from Ticket B (which they shouldn't see), or from internal-only comments they don't have access to.
REPRO: 1. Create two tickets: Ticket-A assigned to Agent-X, Ticket-B assigned to Agent-Y. 2. Upload an attachment to Ticket-B as Agent-Y. 3. As Agent-X, call GET /api/v1/attachments/attachments/{attachment_id}/ using the attachment ID from Ticket-B. 4. Agent-X receives the file_url and can download the attachment despite not being assigned to Ticket-B.
EXPECTED: Agent-X should receive a 403 Forbidden response because they don't have permission to view Ticket-B or its attachments.
ACTUAL: Agent-X receives a 200 OK response with the attachment metadata and file_url, allowing them to download the file.
FIX: Implement a custom object-level permission check in AttachmentViewSet that verifies the user has permission to access the target object (via the content_type and object_id fields). Create a custom permission class (e.g., HasTargetObjectPermission) that checks: for Tickets, use tickets/access.py agent_visible_tickets_q logic; for Comments, verify the user can see the parent object and isn't blocked by is_internal flag; for Messages, verify conversation membership; for other models, implement appropriate checks. Override check_object_permissions() in AttachmentViewSet to enforce this.

### [attachments-3] No Authorization Required for Static Media File Downloads
cat=Security | conf=high | verdict=confirmed | merged=[]
LOC: main/urls.py:46-47, Django static()/media serving; main/settings/base.py: MEDIA_URL, MEDIA_ROOT
DESC: Attachment files are stored in MEDIA_ROOT and served via /media/ URL without any authorization checks. Once a user obtains the file_url from the AttachmentViewSet API (which lacks object-level authorization), they can directly download the file without any further permission checks. This is because Django's static.serve() in DEBUG mode and standard webserver configuration (nginx, Apache) do not perform authentication. The tenant-scoping in the file path (tenants/{tenant_id}/attachments/...) only isolates between tenants, not between users within a tenant.
REPRO: 1. Obtain a file URL from the Attachment API (e.g., /media/tenants/{uuid}/attachments/2026/06/{uuid}_document.pdf). 2. Make a direct HTTP request to that URL with any authentication context (even invalid or different user). 3. The file is served successfully if the file physically exists on disk.
EXPECTED: The webserver should deny the request or the file serving should be proxied through a Django view that performs authorization checks.
ACTUAL: The file is served by the webserver without any authorization checks beyond HTTP authentication.
FIX: Implement a custom file download view that performs authorization before serving files. Replace direct file URLs with a proxied endpoint (e.g., /api/v1/attachments/{id}/download/) that: (1) loads the Attachment record, (2) verifies user has permission to access the target object, (3) uses Django's FileResponse or X-Accel-Redirect (nginx) / X-Sendfile (Apache) to efficiently serve the file. This keeps authorization logic centralized in Django.

### [custom-fields-agents-3] get_field_definitions() function never called, visible_to_roles enforcement missing in API
cat=Security | conf=high | verdict=confirmed | merged=[]
LOC: apps/custom_fields/services.py:135-165 (get_field_definitions) + apps/custom_fields/views.py:52-60
DESC: The get_field_definitions(user_role=...) service function is defined but never called anywhere in the codebase (grep confirms zero call sites). The CustomFieldDefinitionViewSet.get_queryset() does NOT filter by role visibility — it returns all active field definitions for the tenant, regardless of the user's role (line 53: no visible_to_roles filtering). This means a non-admin user with a restricted role can see (and theoretically edit, via partial_update) custom field definitions that should be hidden from them. The visible_to_roles M2M exists in the schema but is completely unenforced at the API layer.
REPRO: 1. Create a custom field with visible_to_roles restricted to Admin role only. 2. Login as a non-admin Agent user. 3. GET /api/v1/custom-fields/definitions/?module=ticket. Result: the restricted field is visible in the response with all details. 4. PATCH /api/v1/custom-fields/definitions/{id}/ (if permissions allow). Result: field edited by non-authorized user.
EXPECTED: The API list view should filter by user role, returning only definitions the user's role is allowed to see (or that have no restrictions). Fields with role restrictions should be omitted from responses to unauthorized users.
ACTUAL: All active field definitions are returned to all authenticated users, bypassing the visible_to_roles restriction entirely.
FIX: In CustomFieldDefinitionViewSet.get_queryset(), call get_field_definitions(tenant, module, user_role=self.request.membership.effective_role) instead of bare CustomFieldDefinition.objects.all(). Or inline the role filter directly. Fix the broken get_field_definitions() Q() logic first. Also enforce role visibility in HasTenantPermission checks for update/partial_update actions on restricted fields.

### [data-model-integrity-1] Ticket assignee cross-tenant vulnerability
cat=Security | conf=high | verdict=confirmed | merged=[]
LOC: apps/tickets/models.py:579-589
DESC: Ticket.clean() validates assignee TenantMembership but save() never calls full_clean(), allowing assignee FK to point to users not in tenant.
REPRO: PATCH ticket assignee to user from different tenant, FK updated without validation.
EXPECTED: Only TenantMembers assignable.
ACTUAL: Any user in any tenant assignable.
FIX: Add save() override calling full_clean() or TicketSerializer validation.

######################################################################
# Medium: 104 (deduped, non-dismissed)
######################################################################

### [templates-uiux-3] Reminder modal Open action uses semantic link instead of button
cat=Accessibility | conf=high | verdict=unverified | merged=[]
LOC: templates/base.html:166-168
DESC: Open reminder action is anchor tag styled as button, creates semantic mismatch
REPRO: Inspect reminder modal, note Dismiss is button but Open reminder is anchor, use screen reader to verify
EXPECTED: Should use button element for consistency and semantics
ACTUAL: Uses anchor href styled with btn classes
FIX: Change to button element or add proper button semantics to anchor

### [templates-uiux-6] Icon-only buttons missing aria-label attributes
cat=Accessibility | conf=medium | verdict=unverified | merged=[]
LOC: templates/includes/navbar.html:34-36,100-102,137-139
DESC: Theme toggle, quick create, and notes buttons use only title attribute instead of aria-label
REPRO: Open dashboard, use screen reader on theme toggle button, accessible label is missing
EXPECTED: All icon-only buttons should have aria-label attributes
ACTUAL: Only title attributes present, not reliably exposed to screen readers
FIX: Add aria-label to each icon-only button for screen reader support

### [templates-uiux-8] Modal does not restore focus on close for keyboard users
cat=Accessibility | conf=medium | verdict=unverified | merged=[]
LOC: templates/pages/emails/list.html:1083-1084,1124
DESC: Create-ticket modal does not restore focus to triggering element, breaks keyboard navigation flow
REPRO: Tab to button, open modal, tab through form, close modal, focus is lost not restored
EXPECTED: Focus should be restored to original button after modal closes
ACTUAL: Focus is lost and keyboard user must tab from top again
FIX: Capture and restore focus using hidden.bs.modal event

### [templates-uiux-9] Reminder modal aria-hidden may block screen reader announcement
cat=Accessibility | conf=medium | verdict=unverified | merged=[]
LOC: templates/base.html:151-152
DESC: Modal has aria-hidden=true initially, if Bootstrap handling is interrupted screen readers may not announce alert
REPRO: Trigger reminder notification, test with screen reader on slow network to check announcement
EXPECTED: Bootstrap should toggle aria-hidden to false and modal should be announced
ACTUAL: Initial aria-hidden correct but risk if Bootstrap handling is delayed
FIX: Add role=alertdialog to ensure alert is properly announced

### [analytics-exports-2] PDF export writes CSV bytes with mismatched .csv filename instead of .pdf
cat=Bug | conf=high | verdict=confirmed | merged=[]
LOC: apps/analytics/tasks.py:209-212, function _generate_file
DESC: When job.export_type == 'pdf', the function calls _generate_csv() and saves the CSV bytes with a .csv filename extension instead of .pdf. Users requesting PDF exports receive a file that is actually CSV data mislabeled with a .pdf extension (or vice versa when line 212 is reached).
REPRO: 1. Create an ExportJob with export_type='pdf' and resource_type='tickets'. 2. Let process_export_job task complete. 3. Download the file. 4. Observe filename is tickets_YYYYMMDD_HHMMSS.csv (not .pdf) and file contents are CSV text, not binary PDF.
EXPECTED: PDF export should generate a proper PDF file (using ReportLab, WeasyPrint, or similar library) and save it with a .pdf extension.
ACTUAL: PDF export generates CSV text and saves it as tickets_YYYYMMDD_HHMMSS.csv (line 212: wrong filename extension, plaintext content).
FIX: Replace the placeholder PDF generation (line 210-212) with a real library like ReportLab or WeasyPrint. For now, raise NotImplementedError or return the .csv fallback with a proper .csv filename instead of misleading .pdf.

### [tenant-isolation-4] Missing Tenant Context in convert_to_ticket Service
cat=Business Logic | conf=high | verdict=confirmed | merged=[]
LOC: /home/kavin/crm/apps/inbox_hub/services.py:152-155
DESC: The convert_to_ticket() function calls _create_ticket_from_email(...) at line 153 without wrapping in tenant_context(). While the function has access to the tenant variable (line 135), it does not set it in the context. This means if _create_ticket_from_email or any nested service internally uses TenantAwareManager without explicit tenant= filters, those queries could fail or return empty querysets. Since _create_ticket_from_email is also called from the direct API path (api_views.py), auditing both call sites is necessary.
REPRO: If convert_to_ticket calls a downstream service that uses TenantAwareManager without explicit tenant= (e.g., during SLA initialization or notification queueing), the query could fail silently due to missing context.
EXPECTED: All service functions that perform TenantAwareManager queries should either (a) be wrapped in `with tenant_context(tenant):`, or (b) have explicit documentation stating they require a bound tenant context and accept it as a parameter.
ACTUAL: convert_to_ticket at lines 152-155 calls _create_ticket_from_email without tenant_context. While the DRF view that calls convert_to_ticket has request.tenant set, the service function itself does not ensure context.
FIX: Wrap the _create_ticket_from_email call in tenant_context: `with tenant_context(tenant): ticket = _create_ticket_from_email(...)`

### [authz-rbac-5] Feature B (create-ticket-from-email overrides) unreachable via Inbox Hub convert endpoint
cat=Business Logic | conf=high | verdict=confirmed | merged=[]
LOC: apps/inbound_email/api_views.py:45-154, apps/inbox_hub/services.py, apps/inbox_hub/serializers.py (not widened)
DESC: Feature B widens _create_ticket_from_email and convert_to_ticket to accept subject/description/category/due_date/tags overrides, validated by _build_ticket_overrides. However, only InboundEmailViewSet.create_ticket (Emails page) uses these overrides. The Inbox Hub's convert-to-ticket endpoint (HubEmailViewSet) uses ConvertToTicketSerializer which only forwards queue/status/assignee/priority. The 5 new override keys are accepted by the service but unreachable via the Hub API, creating an inconsistency: Hub triage agents cannot override subject/description/etc., but Emails-page agents can.
REPRO: 1. Email parked in Inbox Hub. 2. Agent tries convert via /inbox-hub/{id}/convert-to-ticket/ with subject override. 3. Serializer ignores it (only accepts 4 fields). 4. Converted ticket keeps raw email subject. 5. Agent must go to /emails/ and use create_ticket form to override fields.
EXPECTED: Both Hub convert and Emails create_ticket should support the same override fields.
ACTUAL: Only Emails-page create_ticket supports the full override set; Hub convert silently ignores subject/description/category/due_date/tags.
FIX: Widen ConvertToTicketSerializer to include all 5 new fields and pass them to convert_to_ticket. OR document that Hub only supports 4 fields and mark the others as 'not supported via this endpoint'.

### [authz-rbac-6] pick_email_agent and inbox_hub._candidate_user_ids filter on raw role, excluding temp-promoted agents from auto-assign
cat=Business Logic | conf=medium | verdict=unverified | merged=[]
LOC: apps/agents/services.py:226, apps/inbox_hub/assignment.py:226-230
DESC: Both email auto-assign paths filter TenantMembership by role__hierarchy_level==AGENT_ROLE_LEVEL (30), using raw role. A User with permanent Agent role and active temporary Manager role grant (level 20) is excluded from the candidate pool, so they will NOT receive auto-assigned inbound emails even though they should be eligible as a Manager.
REPRO: 1. Tenant with auto_assign_inbound_email_tickets=True. 2. Grant Agent (level 30) a temporary Manager role (level 20). 3. Receive inbound email. 4. pick_email_agent filters role__hierarchy_level==30; temp-Manager is excluded. 5. Email is assigned to a less-qualified Agent.
EXPECTED: Temp-promoted agents should remain eligible for auto-assign when elevated above level 30.
ACTUAL: Auto-assign paths ignore temp roles and use only permanent role hierarchy level.
FIX: Refactor to fetch all active members and filter in Python using effective_role, or change filter to level>=30 and post-filter. Ideally unify both paths to use the same logic.

### [inbox-hub-access-1] Escalation Counter Increments Even on Illegal State Transition
cat=Business Logic | conf=high | verdict=unverified | merged=[]
LOC: apps/inbox_hub/services.py:261-283 (escalate_hub_email function)
DESC: The escalation_count field is incremented unconditionally before validating whether the HubEmail state can legally transition to ESCALATED. When the transition is illegal (e.g., from RESOLVED state), the counter still increases, resulting in a mismatch between the escalation count and the actual number of times the email was successfully escalated to ESCALATED state.
REPRO: 1. Create a HubEmail in RESOLVED state. 2. Call escalate_hub_email() via the API POST /api/v1/inbox-hub/hub-emails/{id}/escalate/. 3. Observe that escalation_count increases but state does NOT transition to ESCALATED.
EXPECTED: escalation_count should only increment when the state transition to ESCALATED is legal and succeeds.
ACTUAL: escalation_count is incremented at line 275 regardless of whether the state machine allows the transition at line 280. A rejected transition still leaves behind a phantom increment.
FIX: Reorder the logic to validate the transition first, then increment the counter only if the transition succeeds. Change the order in escalate_hub_email to: call can_transition(old_state, ESCALATED) before incrementing escalation_count.

### [inbox-hub-access-5] ConvertToTicketSerializer Does Not Expose Full Override Set
cat=Business Logic | conf=high | verdict=unverified | merged=['inbox-hub-engine-5']
LOC: apps/inbox_hub/views.py:147-168 (convert_to_ticket action) vs. apps/inbound_email/api_views.py:45-154 (_build_ticket_overrides)
DESC: The `convert_to_ticket` service function supports 9 override fields (subject, description, priority, queue, status, assignee, category, due_date, tags), but the Inbox Hub's ConvertToTicketSerializer only accepts 4 (queue, status, assignee, priority). The Emails page's create_ticket action calls _build_ticket_overrides which accepts all 9. This creates a feature parity gap: an agent using the Hub convert endpoint cannot refine the subject or description, while an agent using the Emails page can.
REPRO: 1. Via the Hub convert endpoint, POST subject/description/category/due_date/tags overrides. 2. Observe that ConvertToTicketSerializer drops them (no fields defined). 3. Via the Emails create_ticket form, POST the same fields. 4. They are accepted and applied.
EXPECTED: The Hub and Emails create-ticket paths should accept the same override set, or the API documentation should clearly state which overrides are supported per endpoint.
ACTUAL: Hub convert: accepts [queue, status, assignee, priority]. Emails create_ticket: accepts [subject, description, priority, queue, status, assignee, category, due_date, tags]. Asymmetry is silent (no error, just dropped fields).
FIX: Widen ConvertToTicketSerializer to include all 9 fields matching _build_ticket_overrides. Or document the limitation in the API schema if intentional (e.g., 'Hub convert is a quick convert; detailed overrides are available via the Emails create_ticket form').

### [inbox-hub-access-6] Manual Claim/Assign Bypasses Agent Capacity Checks
cat=Business Logic | conf=medium | verdict=unverified | merged=[]
LOC: apps/inbox_hub/views.py:194-199 (claim action), apps/inbox_hub/views.py:201-211 (_do_assign), and apps/inbox_hub/assignment.py:88-114 (assign_to function)
DESC: The claim and assign actions call reassign_hub_email which eventually calls assign_to with require_online=False (line 207-208, it's a manual override). The assign_to function respects the is_assignable check only if require_online=True. This means an agent can manually claim an email even if they are over capacity (remaining_capacity <= 0), bypassing the load-balancing fairness intended by auto-assign logic.
REPRO: 1. Agent A has remaining_capacity = 0 (at full capacity per AgentAvailability). 2. Agent A calls POST /api/v1/inbox-hub/hub-emails/{id}/claim/ on a NEW email. 3. The claim succeeds because assign_to(..., require_online=False) skips the is_assignable check at line 113. 4. Agent A now carries one more email beyond their declared capacity.
EXPECTED: Manual claim/assign should either respect remaining_capacity or explicitly document that they bypass the load-balancing limits as an emergency override.
ACTUAL: Manual claim/assign bypasses capacity checks. The API has no validation, so an agent can overload themselves silently.
FIX: Add a capacity check before accepting manual claim/assign: check remaining_capacity and either raise a 400 error with 'Agent at capacity' or emit a warning. Or update the docstring to clarify that manual override bypasses capacity as intended.

### [tickets-services-sla-6] SLA pause calculation semantics are unclear: wall-clock vs business-hours
cat=Business Logic | conf=medium | verdict=unverified | merged=[]
LOC: apps/tickets/sla.py:175-201 (get_total_pause_minutes)
DESC: The get_total_pause_minutes() function sums pause durations using _count_business_minutes() when config is provided. A pause spanning a weekend (e.g., Friday 16:00-Monday 10:00) will only count Friday 16:00-17:00 as paused (the business-hours portion), not the full 66 hours. The semantics are unclear: should a pause subtract the entire wall-clock duration or only the business-hours portions? Current implementation does the latter, which may surprise admins.
REPRO: 1. Create a ticket with business hours M-F 9-5. 2. Create an SLAPause from Friday 16:00 UTC to Monday 10:00 UTC. 3. Call get_total_pause_minutes with config. 4. Result is ~60 minutes (Fri 16:00-17:00), not 66 hours.
EXPECTED: Clear documentation of pause semantics: wall-clock or business-hours only.
ACTUAL: Pauses spanning non-business time only count their business-hours portions, which may be counterintuitive.
FIX: Add docstring clarification to get_total_pause_minutes() explaining the behavior. Or add a parameter to toggle wall-clock vs business-hours semantics.

### [tickets-services-sla-7] ALLOWED_TRANSITIONS marks closed as terminal with no admin escape hatch
cat=Business Logic | conf=medium | verdict=unverified | merged=[]
LOC: apps/tickets/services.py:472-478 (ALLOWED_TRANSITIONS)
DESC: The 'closed' status is terminal (empty transition list). Once a ticket is closed, there is no way to transition it back (even for admins). If an admin mistakenly closes a ticket, they must delete and recreate it. Resolved is not terminal, so it's possible to manually close a Resolved ticket, then get stuck (because Closed has no outbound transitions).
REPRO: 1. Create a ticket and transition to Resolved. 2. Manually transition to Closed. 3. Try to transition back to Resolved. 4. Error: Closed is terminal. 5. Ticket is stuck forever.
EXPECTED: Either Closed should allow transitions for admin correction, or there should be clear admin-only escape hatch.
ACTUAL: Closed is permanently terminal with no recovery path.
FIX: Either add Closed->Open to ALLOWED_TRANSITIONS for admin correction, or document the permanent-terminal semantics prominently. Alternatively, add an @admin_reopen decorator for superusers.

### [tickets-services-sla-11] Change ticket priority does not re-attach SLA policy or recalculate deadlines
cat=Business Logic | conf=medium | verdict=unverified | merged=[]
LOC: apps/tickets/services.py:1218-1259 (change_ticket_priority)
DESC: When priority changes, the SLA policy and deadlines are NOT updated. A ticket downgraded from urgent to low may keep its tight urgent deadlines, immediately appearing breached.
REPRO: 1. Create SLA: urgent=60min, low=1440min. 2. Create urgent ticket; deadline is now+60min. 3. Wait 30min. 4. Downgrade priority to low. 5. Deadline still now+30min (from urgent); should be now+1440min. 6. Ticket appears breached immediately.
EXPECTED: Priority change should re-attach new SLA policy and recalculate deadlines.
ACTUAL: Priority change leaves old SLA policy and deadlines intact.
FIX: After saving new priority, call initialize_sla(ticket) to re-attach policy and deadlines. Document this behavior in docstring.

### [tickets-signals-6] Resolved and closed timestamps both set to 'now', losing distinction between resolution and closure
cat=Business Logic | conf=medium | verdict=unverified | merged=[]
LOC: apps/tickets/signals.py:86-90
DESC: When a ticket transitions to a closed status, both `resolved_at` and `closed_at` are set to the same timestamp. This means there is NO distinction between when a ticket was resolved vs closed. If a business process needs to distinguish 'resolved' (agent action) from 'closed' (customer confirmation), these semantics are lost.
REPRO: 1. Create a ticket. 2. Transition to 'resolved' status. 3. Note resolved_at = 2026-06-14 10:00:00. 4. Move to 'waiting' status (re-open). 5. Move to 'closed' status. 6. closed_at and resolved_at are now both the second closure timestamp; the original resolution time is lost.
EXPECTED: Ideally, `resolved_at` should capture the first time reaching a 'resolved' status, and `closed_at` the first time reaching a 'closed' status. These should be milestone timestamps, not reset on re-open.
ACTUAL: Both timestamps are set to `now()` whenever a closed status is reached. They are indistinguishable. Re-opening clears both (line 111-112), losing historical data.
FIX: Separate the semantics: Set `resolved_at` only on first entry to a 'resolved' status (add check `if not instance.resolved_at`). Set `closed_at` only on first entry to a 'closed' status. Do NOT clear these on re-open (remove lines 111-112). This preserves the historical timeline.

### [inbox-hub-engine-4] escalation_count Incremented Unconditionally on Illegal Transitions
cat=Business Logic | conf=high | verdict=confirmed | merged=[]
LOC: apps/inbox_hub/services.py:275
DESC: In escalate_hub_email, escalation_count is always incremented (line 275) before checking if the state transition to ESCALATED is legal (line 280). This means even if the transition is illegal (e.g., from DISMISSED or CONVERTED), the counter increments. A terminal email can have escalation_count bumped repeatedly by external event processing or race conditions, creating audit trail noise and incorrect escalation metrics.
REPRO: 1. Create a dismissed HubEmail. 2. Call escalate_hub_email on it. 3. Observe: escalation_count increments, state remains DISMISSED. 4. Re-run escalation. 5. escalation_count increments again without state change.
EXPECTED: Either: (a) escalation_count is only incremented when the state transition is legal, or (b) include escalation in the response_breached flag check so it fires exactly once per breach.
ACTUAL: escalation_count is incremented every time escalate_hub_email is called, regardless of whether the state transitions or whether the email is terminal.
FIX: Move the escalation_count increment inside the `if can_transition(...)` block (line 280-282) so it only counts successful state transitions. Or: add an 'escalation_attempted' flag to deduplicate escalations.

### [inbox-hub-engine-8] reassign_hub_email Does Not Validate Tenant Membership
cat=Business Logic | conf=medium | verdict=unverified | merged=[]
LOC: apps/inbox_hub/services.py:307
DESC: reassign_hub_email manually moves a HubEmail to a new_user without validating that new_user is an active member of the tenant. If an invalid/deleted/out-of-tenant user ID is passed, the InboundEmail.assignee FK will point to them (since users are global, not tenant-scoped), and consistency breaks.
REPRO: 1. Get an invalid user ID outside the tenant. 2. Call reassign_hub_email(he, invalid_user, ...). 3. InboundEmail.assignee is set to the invalid user. 4. That user's Emails page will not display the email (scoped by tenant context), but the row points to them.
EXPECTED: Validate that new_user is an active TenantMembership before assigning.
ACTUAL: No validation; any user can be assigned, including out-of-tenant or deleted users.
FIX: Add a TenantMembership validation check at the top of reassign_hub_email.

### [contacts-crm-8] Feature B: Hub cockpit convert_to_ticket serializer unreachable for 5 override fields
cat=Business Logic | conf=medium | verdict=unverified | merged=[]
LOC: apps/inbound_email/services.py:383-438; apps/inbox_hub/services.py (convert_to_ticket); apps/inbound_email/api_views.py:45-154 (_build_ticket_overrides)
DESC: Feature B (create-ticket-from-email overrides) widened _create_ticket_from_email to accept 9 override fields: subject, description, category, due_date, tags, plus the pre-existing priority, queue, assignee, status. The _build_ticket_overrides validator (apps/inbound_email/api_views.py:45-154) validates all 9 fields. However, the Hub cockpit's convert_to_ticket action (apps/inbox_hub/services.py, likely in a serializer) was NOT widened to accept the extra 5 fields (description, category, due_date, tags, and subject). This means the full override set reaches the Ticket creation only via the Emails-page create_ticket action (which calls _create_ticket_from_email directly with overrides), NOT via the Hub cockpit's convert-to-ticket endpoint. A user converting an email to a ticket from within the Hub triage interface cannot refine the subject/description/category/due_date/tags — they can only set priority/queue/assignee/status.
REPRO: (1) In the Hub triage cockpit, click 'Convert to ticket' on an inbound email. (2) A form appears but is missing the subject/description/category/due_date/tags fields (or they're read-only). (3) The user can only set priority/queue/assignee/status. (4) Go to the Emails page, click 'Create ticket' on the same email. (5) A form appears with ALL 9 override fields.
EXPECTED: Either both surfaces (Hub convert + Emails create) should accept the same set of overrides, OR the Hub convert form should accept the 5 extra fields.
ACTUAL: The Hub cockpit does not expose the 5 extra override fields. The feature is only accessible via the Emails page.
FIX: Widen the Hub convert_to_ticket serializer/action to accept and pass through description/category/due_date/tags (and optionally subject). OR document that the Hub cockpit only accepts priority/queue/assignee/status, and agents must use the Emails page for full control.

### [kanban-5] Pipeline stage sync fails silently when column is deleted
cat=Business Logic | conf=medium | verdict=unverified | merged=[]
LOC: apps/tickets/signals.py:616-619
DESC: When a column matching a pipeline stage name is deleted, the sync_kanban_card_on_pipeline_stage_change function finds no column and returns without logging. The card remains orphaned in its old column with zero indication of the failure.
REPRO: 1. Create pipeline stage 'Planning' and column 'Planning'. 2. Create ticket with that stage (card created). 3. Delete the column. 4. Change ticket's stage to 'Open' and back to 'Planning'. 5. No warning logged; card stays in old column silently.
EXPECTED: Warning logged when no matching column found, or API prevents column deletion if stage mappings exist.
ACTUAL: No warning; card silently remains in old column.
FIX: Add error logging in the function when target_column is None. Alternatively add admin-level validation to forbid column deletion if stage mappings exist.

### [billing-6] SubscriptionMiddleware Fail-Open - Missing Subscription Grants Access
cat=Business Logic | conf=medium | verdict=unverified | merged=[]
LOC: apps/billing/middleware.py:52-94
DESC: When tenant has no Subscription row in database SubscriptionMiddleware allows request through treats as free tier. No guarantee all code paths enforce PlanLimitChecker.
REPRO: Create tenant without creating Subscription row. Send API request to /api/v1/tickets/tickets/. SubscriptionMiddleware allows through no subscription equals free tier. TicketViewSet.perform_create calls PlanLimitChecker. But if direct model create called outside view layer no limit check occurs.
EXPECTED: All resource creation protected by PlanLimitChecker or similar. Missing subscription either blocks 402 or code guarantees limits enforced everywhere.
ACTUAL: Missing subscription silently treated as free tier. Some code paths may bypass limit enforcement if not going through proper view/serializer layer.
FIX: Require Subscription creation at tenant signup time via provision_tenant command. Or enforce limits in model pre_save hooks to guarantee protection at data layer.

### [analytics-exports-4] DashboardView.get_agent_performance returns all agents' metrics, no user filtering
cat=Business Logic | conf=high | verdict=confirmed | merged=[]
LOC: apps/analytics/views.py:209, DashboardView.get()
DESC: DashboardView calls get_agent_performance(tenant, date_from, date_to) without passing the request.user. The function returns a complete list of all agents' performance metrics (tickets handled, resolution times, etc.) for every tenant member viewing the dashboard. An agent user (role > 20) sees all peers' performance data, which may be sensitive or unintended.
REPRO: 1. Authenticate as an Agent in a tenant with 5+ active agents. 2. GET /api/v1/analytics/dashboard/. 3. In response.data.agent_performance.agents, observe a list of all agents' metrics, including your peers (not just your own).
EXPECTED: Agent-tier users should see only their own agent_performance metrics, or the endpoint should filter agents per user role (similar to get_ticket_stats which applies user filter).
ACTUAL: get_agent_performance returns all agents' metrics without user-level filtering. Any authenticated member sees all agents' performance.
FIX: Either: (a) Pass user=request.user to get_agent_performance and add user-filtering logic (similar to get_ticket_stats), OR (b) Conditionally return agent_performance only for Admin/Manager roles (is_admin_or_manager check already exists).

### [analytics-exports-5] DashboardView._get_overdue_reminders_summary returns top 5 tenant-wide reminders, not user-filtered
cat=Business Logic | conf=medium | verdict=unverified | merged=[]
LOC: apps/analytics/views.py:247-251, method DashboardView._get_overdue_reminders_summary
DESC: The method retrieves top 5 most overdue reminders ACROSS THE ENTIRE TENANT and includes them in the response, only separately counting 'mine' (line 244). An agent user viewing the dashboard sees the top 5 overdue reminders tenant-wide, which may leak visibility of colleagues' reminders.
REPRO: 1. Create 10 overdue reminders across 5 agents in a tenant. 2. Authenticate as Agent A. 3. GET /api/v1/analytics/dashboard/. 4. In response.data.overdue_reminders.items, see the top 5 most overdue reminders GLOBALLY, including those assigned to other agents.
EXPECTED: overdue_reminders.items should only include reminders assigned to the current user (or filtered per role: admins see all, agents see own).
ACTUAL: items list contains top 5 most overdue reminders across the entire tenant, regardless of assignment.
FIX: Filter top_overdue by assigned_to=user OR (created_by=user AND assigned_to IS NULL) to respect reminder visibility. Or, only return items for Admin/Manager roles, and show only user's reminders for agents.

### [settings-secrets-7] CELERY_TIMEZONE UTC vs TIME_ZONE Asia/KL timezone mismatch causes Beat crontab entries to fire at wrong local time
cat=Business Logic | conf=medium | verdict=unverified | merged=[]
LOC: main/settings/base.py:149 (TIME_ZONE), 282 (CELERY_TIMEZONE), 312-317 (crontab entries)
DESC: The application is configured with TIME_ZONE='Asia/Kuala_Lumpur' (UTC+8) but CELERY_TIMEZONE='UTC'. Beat crontab entries (lines 312-317: kb-stale-alert @ 08:00, kb-gap-digest @ Monday 09:00) are specified without explicit timezone, so Celery interprets them as UTC times. This causes a ~8-hour drift: the KB stale-alert scheduled for 08:00 UTC (not 08:00 KL) will fire at midnight KL time, and the gap-digest will fire at 1 AM KL time instead of the intended 9 AM. Users in the KL timezone will receive the digest at the wrong local time.
REPRO: 1. Set up the Beat scheduler. 2. Wait until the scheduled time in UTC (08:00 UTC = ~16:00 KL). 3. Observe kb-stale-alert fires at 16:00 KL, not 08:00 KL. 4. On Monday at 09:00 UTC (17:00 KL), the gap-digest fires at wrong time.
EXPECTED: Either (a) set CELERY_TIMEZONE='Asia/Kuala_Lumpur' to match TIME_ZONE, or (b) specify timezone-aware crontab entries using explicit tz parameter.
ACTUAL: CELERY_TIMEZONE=UTC while TIME_ZONE=Asia/Kuala_Lumpur, causing Beat crontab entries to fire 8 hours off the intended local time.
FIX: Change main/settings/base.py line 282 to: `CELERY_TIMEZONE = 'Asia/Kuala_Lumpur'` to match TIME_ZONE. Alternatively, import pytz and wrap the crontab entries with explicit timezone parameter.

### [settings-secrets-11] Feature B (create-ticket-from-email overrides) widened service but not Hub serializer, creating unreachable feature
cat=Business Logic | conf=medium | verdict=unverified | merged=[]
LOC: apps/inbound_email/services.py:383, apps/inbox_hub/views.py (serializer not widened)
DESC: Feature B (uncommitted) widens the _create_ticket_from_email service to accept subject/description/category/due_date/tags overrides. However, the HubEmailViewSet's convert_to_ticket serializer was NOT widened to accept these fields. This means the Hub's own convert endpoint cannot pass the new override fields — they reach only via the Emails-page create_ticket action. A user working in the Hub triage cockpit cannot override the ticket fields when converting; they must go to the Emails page to use the full form.
REPRO: 1. Open a HubEmail in the triage cockpit. 2. Click 'Convert to ticket'. 3. Observe the convert form only shows queue/status/assignee/priority (old fields). 4. The new subject/description/category/due_date/tags overrides are unavailable. 5. Go to the Emails page and use the 'Create ticket' action — the full form is available there.
EXPECTED: Either (a) widen the HubEmailViewSet's ConvertToTicketSerializer to include the new override fields, or (b) document this limitation clearly so Hub users know to use the Emails page for full control.
ACTUAL: Feature B widens the service layer but not the Hub's serializer, making the new overrides unreachable via the Hub UI.
FIX: Widen apps/inbox_hub/serializers.py::ConvertToTicketSerializer to accept subject, description, category, due_date, tags fields (similar to the Emails-page implementation), and validate them as per Feature B's _build_ticket_overrides function.

### [frontend-js-1] Dead link in command palette: '/contacts/new/' doesn't exist
cat=Business Logic | conf=high | verdict=confirmed | merged=[]
LOC: static/js/command-palette.js:28
DESC: The command palette has a 'New Contact' action that links to '/contacts/new/', but the actual frontend route is '/contacts/create/'. This causes navigation to fail when users click on 'New Contact' from the command palette.
REPRO: 1. Open command palette (Cmd+K). 2. Search for or navigate to 'New Contact' action. 3. Click or press Enter. 4. Observe navigation to incorrect URL or 404 error.
EXPECTED: Navigation to /contacts/create/ (the actual contact creation page)
ACTUAL: Navigation to /contacts/new/ (which does not exist as a route)
FIX: Change line 28 from url: '/contacts/new/' to url: '/contacts/create/' to match the frontend_urls.py route mapping.

### [performance-db-9] Feature B: convert_to_ticket Overrides Not Reachable via Hub Cockpit Serializer
cat=Business Logic | conf=medium | verdict=unverified | merged=[]
LOC: apps/inbox_hub/views.py:147-168 vs apps/inbound_email/api_views.py:297-314
DESC: Feature B widens convert_to_ticket service to accept subject/description/category/due_date/tags, but Hub cockpit's ConvertToTicketSerializer only forwards queue/status/assignee/priority. Full override set unreachable through Hub UI.
REPRO: 1. Open Inbox Hub. 2. Click 'Convert to Ticket'. 3. Note form has no subject/description/category. 4. Go Emails page. 5. Click 'Create Ticket' — full form available.
EXPECTED: Either Hub serializer widened to match service, OR documentation clarifies Emails-page-only.
ACTUAL: ConvertToTicketSerializer only forwards 4 fields; subject/description/category/due_date/tags silently dropped.
FIX: Widen ConvertToTicketSerializer to include all override fields: subject, description, category, due_date, tags.

### [data-model-integrity-5] Account health score validator unreachable
cat=Business Logic | conf=high | verdict=confirmed | merged=[]
LOC: apps/contacts/models.py:49-56
DESC: Account.clean() enforces 0-100 range but save() never calls full_clean(). Tasks use .update() bypassing validation.
REPRO: PATCH health_score=999 or .update(health_score=-10), persists without validation.
EXPECTED: Range enforced to 0-100.
ACTUAL: Out-of-range values writable.
FIX: Add save() override calling full_clean() or use DecimalRangeValidator.

### [data-model-integrity-7] Reminder reschedule stale watermark
cat=Business Logic | conf=medium | verdict=unverified | merged=[]
LOC: apps/crm/models.py:252-257
DESC: reschedule() updates scheduled_at but not due_notified_at, re-arm logic fragile and implicit.
REPRO: Reschedule forward, due_notified_at left unchanged, relies on lucky timestamps.
EXPECTED: Explicitly reset due_notified_at=NULL on reschedule.
ACTUAL: Watermark unchanged, implicit re-arm logic.
FIX: Add self.due_notified_at=None before save().

### [data-model-integrity-8] Feature B serializer not widened for overrides
cat=Business Logic | conf=medium | verdict=unverified | merged=[]
LOC: apps/inbox_hub/views.py
DESC: Service widened for overrides but Hub serializer not widened, fields unreachable via Hub endpoint.
REPRO: POST Hub convert with subject/description, serializer ignores them.
EXPECTED: Both endpoints accept overrides.
ACTUAL: Hub endpoint rejects new fields.
FIX: Widen ConvertToTicketSerializer with new fields.

### [inbound-email-3] Settings Variable Shadowing in process_inbound_email
cat=Code Quality | conf=medium | verdict=unverified | merged=[]
LOC: apps/inbound_email/services.py:360
DESC: At line 360, a local variable `settings = getattr(tenant, "settings", None)` shadows the `django.conf.settings` module imported at line 144 in the `resolve_tenant_from_address` function. While the shadowing doesn't cause an immediate bug (because it's scoped locally and the module-level import is in a different function), it creates a latent trap: future code modifications could accidentally reference `settings.` after this assignment, expecting the Django settings module but getting the TenantSettings object instead.
REPRO: 1. Review the imports at line 144 (`from django.conf import settings`). 2. Examine line 360 where `settings = getattr(tenant, "settings", None)`. 3. Note that any future code added after line 360 in this function that tries to access Django settings would fail.
EXPECTED: Local variable names should not shadow module-level imports to prevent confusion and potential bugs in future code changes.
ACTUAL: The module-level `settings` variable is shadowed by a local variable with the same name, creating a footgun for future developers.
FIX: Rename the local variable to something more specific like `tenant_settings` or `ts` to avoid shadowing: `tenant_settings = getattr(tenant, "settings", None)` and update the subsequent references.

### [feature-b-overrides-5] Missing Direct Tests for API Endpoint
cat=Code Quality | conf=high | verdict=unverified | merged=[]
LOC: tests/test_inbox_hub.py:451-511 vs test_inbound_email.py
DESC: No HTTP-level tests exist in test_inbound_email.py for the NEW POST /api/v1/inbound-email/{id}/create-ticket/ endpoint, which is the actual entry point agents use on Emails page. Only Hub path is tested.
REPRO: Search test_inbound_email.py for 'test_create_ticket' or 'create_ticket' - zero results found.
EXPECTED: Full test coverage for both legacy and Hub paths including validation, atomicity, assignment skip-when-explicit, SLA seeding, and idempotency.
ACTUAL: Only Hub convert path has HTTP tests; legacy path endpoint has zero direct API test coverage.
FIX: Add TestCreateTicketFromEmailLegacyPath to test_inbound_email.py covering override application, atomicity, assignment skip, SLA seeding, and idempotency.

### [knowledge-3] KBRevision Model Dead-Write: No Code Creates Revisions
cat=Code Quality | conf=high | verdict=unverified | merged=[]
LOC: apps/knowledge/models.py:168-191, apps/knowledge/migrations/0004_kbrevision_kbsearchgap_kbticketlink_kbvote_and_more.py:21-32
DESC: The `KBRevision` model is defined with FK to Article and TenantMembership, intended to store snapshots of article body at a point in time, but no code anywhere in the codebase (views, services, signals, tasks, admin actions) ever creates KBRevision rows. Grep across all .py files confirms zero call sites to `KBRevision.objects.create()` or bulk operations. The model is fully migrated but aspirational—a schema ghost.
REPRO: 1. Search the codebase: `grep -r 'KBRevision' /home/kavin/crm/apps --include='*.py' | grep -E 'create|save|bulk'`. 2. Observe zero results. 3. Create and edit an Article via the API. 4. Query the database: `KBRevision.objects.count()` returns 0 even after multiple article edits.
EXPECTED: Either the model should not exist, or ArticleViewSet.perform_update should create a KBRevision snapshot before saving changes.
ACTUAL: KBRevision table is empty and will remain so. The model is schema-only with no runtime usage, consuming storage and maintenance effort for no benefit.
FIX: Either remove the model and its migration, or implement article versioning by creating a KBRevision on each Article.post_save (with the old body stored) before the new body is written. Document the intended behavior in the model's docstring.

### [knowledge-4] KBTicketLink Model Dead-Write: No Code Links Tickets to Articles
cat=Code Quality | conf=high | verdict=unverified | merged=[]
LOC: apps/knowledge/models.py:236-262, apps/knowledge/migrations/0004_kbrevision_kbsearchgap_kbticketlink_kbvote_and_more.py:46-52, 111-115
DESC: The `KBTicketLink` model is defined as a many-to-one audit link between Ticket and Article (recording which agent linked them and when), but no code anywhere in the codebase creates KBTicketLink rows. Grep across all .py files confirms zero call sites. The model is fully migrated and accessible from both Article (via .ticket_links) and Ticket (via .kb_links) but is never populated.
REPRO: 1. Search the codebase: `grep -r 'KBTicketLink' /home/kavin/crm/apps --include='*.py' | grep -E 'create|save|bulk'`. 2. Observe zero results. 3. Create a Ticket and Article, attempt to link them via the UI/API. 4. Query the database: `KBTicketLink.objects.count()` returns 0.
EXPECTED: Either the model should not exist, or there should be an API action (e.g., POST /api/v1/tickets/{id}/link-kb-article/) that creates KBTicketLink rows, or a ticket detail view that shows linked articles.
ACTUAL: KBTicketLink table is empty and will remain so. The model is schema-only. The `related_name='kb_links'` on Ticket and related_name='ticket_links'` on Article are never used.
FIX: Remove the model and its migration, OR implement the linking feature: add a viewset action to TicketViewSet to create/list/delete KBTicketLink rows, and expose it in the API.

### [kanban-6] CardPosition migration 0003 loses explicit db_index declaration
cat=Code Quality | conf=medium | verdict=unverified | merged=[]
LOC: apps/kanban/migrations/0003_alter_cardposition_tenant.py:15-19
DESC: Migration 0002 adds tenant FK with `db_index=True`. Migration 0003 alters the field without re-specifying db_index, losing the explicit intent even though the index still exists in the database. This can cause inconsistency across database backends.
REPRO: Review migrations: 0002 has db_index=True, 0003 does not. On a fresh DB, intent is lost.
EXPECTED: Migration 0003 should re-specify db_index=True.
ACTUAL: Migration 0003 omits db_index, losing explicit declaration.
FIX: Update migration 0003 to include db_index=True, or add a new migration to explicitly create the index.

### [billing-4] require_feature Decorator Never Used
cat=Code Quality | conf=high | verdict=unverified | merged=[]
LOC: apps/billing/decorators.py:23-95
DESC: The require_feature decorator is fully implemented and documented but never applied to any view in codebase. Zero call sites found. Feature flag enforcement incomplete.
REPRO: grep -r @require_feature /home/kavin/crm --include=*.py returns only docstring examples in decorator file itself. No actual uses found.
EXPECTED: Decorator should protect views requiring feature flags such as api_access sso sla_management voip
ACTUAL: Dead code. Feature enforcement happens through other mechanisms PlanLimitChecker VoIPSettings.is_active check_call_limit
FIX: Either remove unused decorator and consolidate feature checks into single pattern. Or identify which views should enforce feature flags and apply @require_feature consistently.

### [analytics-exports-7] Feature B: Hub cockpit's convert_to_ticket endpoint doesn't support new override fields
cat=Code Quality | conf=medium | verdict=unverified | merged=[]
LOC: apps/inbox_hub/views.py:148-161, method HubEmailViewSet.convert_to_ticket; apps/inbox_hub/serializers.py (ConvertToTicketSerializer)
DESC: Feature B widens _create_ticket_from_email() to accept subject/description/category/due_date/tags overrides, but the Hub cockpit's convert_to_ticket endpoint still forwards only queue/status/assignee/priority. The full override set reaches only the Emails-page create_ticket action, creating an inconsistency where the two conversion paths support different field sets.
REPRO: 1. (Both paths available: Inbox Hub /hub-emails/{id}/convert-to-ticket/ and Emails page /inbound-email/{id}/create-ticket/). 2. POST to Hub convert endpoint with {subject: 'Refined subject', queue: ..., status: ..., assignee: ..., priority: ...}. 3. Observe 'subject' is ignored (only 4 fields forwarded). 4. POST same payload to Emails page create-ticket action. 5. Observe 'subject' overrides the email subject (all 5 override categories work).
EXPECTED: Both endpoints should support the same set of overrides, or the API documentation should clearly state which endpoint supports which fields.
ACTUAL: Hub convert endpoint hardcodes only queue/status/assignee/priority (line 157-160). Emails create-ticket accepts all 9 override fields (subject/description/priority/queue/status/assignee/category/due_date/tags).
FIX: Widen ConvertToTicketSerializer and HubEmailViewSet.convert_to_ticket to accept and pass all override fields (subject, description, category, due_date, tags), OR document the limitation clearly in API docs with a note that agents should use the Emails page for full control.

### [templates-uiux-1] Hardcoded z-index in toast container violates CSS token system
cat=Code Quality | conf=high | verdict=unverified | merged=[]
LOC: templates/base.html:177
DESC: Toast container uses inline style z-index:1090 instead of CSS custom property --crm-z-toast
REPRO: Search base.html for toastContainer and inspect the inline style attribute
EXPECTED: Should use CSS variable var(--crm-z-toast) to maintain design system consistency
ACTUAL: Uses hardcoded numeric value in inline style
FIX: Replace style attribute with var(--crm-z-toast) reference

### [tenant-isolation-8] InboundEmail.tenant Field Is Nullable, Allowing Ambiguous Data State
cat=Data Integrity | conf=medium | verdict=unverified | merged=[]
LOC: /home/kavin/crm/apps/inbound_email/models.py:69-75
DESC: InboundEmail.tenant is defined as `null=True, blank=True`, meaning rows can exist in the database without knowing which tenant they belong to. This creates ambiguity: a row with tenant=NULL could be legacy data, a failed ingestion attempt, or a row awaiting tenant resolution that crashed before backfill. The async processing pipeline handles this by backfilling tenant after InboundEmail creation, but if the worker crashes or the database write fails, the row is stranded.
REPRO: 1. Trigger IMAP/SMTP ingestion. 2. Kill the worker immediately after InboundEmail.objects.create() but before tenant backfill in process_inbound_email. 3. Query: SELECT COUNT(*) FROM inbound_email WHERE tenant_id IS NULL. 4. Orphaned rows exist.
EXPECTED: Either require tenant to be set at creation time (not nullable) or implement a cleanup task to reconcile and quarantine stranded rows.
ACTUAL: The tenant field is nullable, allowing permanent NULL state.
FIX: Make tenant required (null=False) and refactor to resolve tenant synchronously during IMAP/SMTP ingest, before creating InboundEmail. This ensures all rows have a tenant from creation.

### [authn-6] Missing row-level locking in inbox hub manual email assignment
cat=Data Integrity | conf=medium | verdict=unverified | merged=[]
LOC: /home/kavin/crm/apps/inbox_hub/services.py
DESC: Manual reassignment (assign/reassign/claim) lacks select_for_update(). Two agents simultaneously claiming the same email could both succeed, leaving assignee indeterminate.
REPRO: Open Inbox Hub with two browsers viewing same HubEmail. Both click 'Assign to me' simultaneously. Check final assignee; either wins with no conflict.
EXPECTED: Only one agent's assignment persists; conflict error or optimistic lock ensures determinism.
ACTUAL: Both updates succeed serially, last-write-wins, no audit trail.
FIX: Wrap HubEmail/InboundEmail updates in atomic block with select_for_update(). Add version counter for optimistic locking.

### [inbox-hub-access-8] Group Gate Uses Unscoped Query, Creating Tenant Isolation Footgun
cat=Data Integrity | conf=medium | verdict=unverified | merged=[]
LOC: apps/inbox_hub/access.py:30-36 (user_in_any_group function)
DESC: The user_in_any_group function uses `UserGroup.unscoped.filter(tenant=tenant, members=user)` which explicitly bypasses the tenant-scoped default manager. While the explicit `.filter(tenant=tenant)` provides isolation today, the use of `.unscoped` is a footgun: if a developer removes the `tenant=tenant` predicate (thinking they're simplifying), the check would pass for any user in any group across ALL tenants, leaking access control.
REPRO: 1. A developer unfamiliar with the codebase refactors user_in_any_group to remove `tenant=tenant`, thinking the .unscoped call is sufficient. 2. Now the function returns True if the user is in ANY group on ANY tenant. 3. An agent from Tenant B gains access to Tenant A's Inbox Hub by virtue of being in a group elsewhere.
EXPECTED: Use the default tenant-aware manager: `UserGroup.objects.filter(members=user).exists()`, which auto-scopes via TenantAwareManager.
ACTUAL: Uses `.unscoped` explicitly with an explicit `tenant=` filter. While correct, it relies on a redundant defense (two mechanisms for the same tenant scope).
FIX: Simplify to: `return UserGroup.objects.filter(members=user).exists()`. The default manager handles tenant-scoping. If .unscoped is needed for some reason, add a prominent comment: '# .unscoped + explicit tenant= ensures safety even if this code is refactored later.'

### [inbound-email-8] InboundEmail Tenant Is Nullable and Resolved Post-Parse
cat=Data Integrity | conf=medium | verdict=unverified | merged=[]
LOC: apps/inbound_email/models.py:69-75
DESC: InboundEmail.tenant is nullable (null=True, blank=True), and tenant resolution happens post-parse in process_inbound_email. This means InboundEmail records can exist with tenant=NULL if the process_inbound_email_task task never runs (e.g., due to Celery queue failure). These orphaned records will not appear in any tenant-scoped query (via the plain manager), making them invisible to agents and audit logs. Additionally, the SMTP server's handle_RCPT does tenant resolution to validate recipients, but doesn't persist the tenant at SMTP ingest time, so there's an async window where the tenant could become inactive before process_inbound_email runs.
REPRO: 1. Send an email via SMTP (InboundEmail created with tenant=NULL). 2. Stop the Celery worker before process_inbound_email_task runs. 3. The InboundEmail.status remains PENDING forever, and the message is invisible to agents. 4. Alternatively, accept a message for an active tenant, then deactivate the tenant, then process_inbound_email runs and fails because tenant.is_active=False.
EXPECTED: Either InboundEmail.tenant should be required (not null), or there should be stronger consistency guarantees around tenant lifecycle.
ACTUAL: InboundEmail records can become orphaned or inconsistent with tenant state changes, leading to invisible messages.
FIX: Consider making tenant required on InboundEmail or adding a periodic task to clean up PENDING/PROCESSING records older than N hours. Also, add explicit validation in process_inbound_email to check tenant.is_active and reject the email if the tenant was deactivated.

### [feature-b-overrides-4] Description Validation Does Not Enforce 20000-Char Limit
cat=Data Integrity | conf=high | verdict=unverified | merged=[]
LOC: apps/inbound_email/api_views.py:84-88
DESC: Description field uses silent truncation slice (str(description)[:20000]) without raising error. User submitting 25000 chars receives 201 success with no indication content was lost.
REPRO: Submit description with 25000 chars via create-ticket API. API returns 201 Created. Open created ticket - only 20000 chars saved. No error or warning to user.
EXPECTED: Either enforce MAX_LENGTH=20000 on Ticket model field or raise 400 ValidationError when exceeding limit.
ACTUAL: Silent truncation at [:20000] without error feedback; data loss occurs without user awareness.
FIX: Add MAX_LENGTH=20000 to Ticket.description field in models.py, or raise DRFValidationError if len(str(description)) > 20000.

### [feature-b-overrides-6] Missing Atomicity in Legacy Path
cat=Data Integrity | conf=medium | verdict=unverified | merged=[]
LOC: apps/inbound_email/api_views.py:308-314
DESC: find_or_create_contact called before atomic block. If contact creation succeeds but ticket creation fails, contact is orphaned in database.
REPRO: Inject exception after contact.save() but before ticket.save(). Contact is created and committed; ticket rollback leaves orphaned contact in database.
EXPECTED: Contact creation inside atomic block with ticket creation: 'with transaction.atomic(): contact, _ = find_or_create_contact(...); ticket = _create_ticket_from_email(...)'
ACTUAL: find_or_create_contact called before 'with transaction.atomic():', contact outside rollback scope.
FIX: Move find_or_create_contact call inside the atomic block together with ticket creation.

### [contacts-crm-1] Contact/Account lead_score and health_score can exceed bounds (0-100)
cat=Data Integrity | conf=high | verdict=confirmed | merged=[]
LOC: apps/contacts/models.py:163-166 (Contact), apps/contacts/models.py:35-38 (Account); apps/contacts/views.py:242-246; apps/contacts/serializers.py:149-240
DESC: Contact.lead_score and Account.health_score have `PositiveSmallIntegerField` with default 50 and help_text indicating 0-100 range. Account.clean() validates this (lines 49-56), but Contact has NO clean() method. More critically, the scoring tasks calculate_lead_scores (apps/crm/tasks.py:418) and calculate_account_health_scores (apps/crm/tasks.py:526) use .update(score_val) which BYPASSES the clean() validation entirely. An agent could also directly PATCH a Contact/Account via the API with a malicious JSON payload before/after scoring runs, since both serializers have health_score/lead_score as read_only (good), but a direct database modification or a task failure mid-clamp would leave out-of-range values.
REPRO: (1) In a scoring task, if an account receives -40 points (base 50 - 20 CSAT - 15 no-activity) the clamp at line 522 should hit max(0, min(100, ...)) correctly, BUT if there's a concurrent modification or an exception in the task's mid-recalc, the old value persists. (2) OR: an agent manually creates an Account via POST /api/v1/accounts/, then directly modifies the database to set health_score=150; the API won't catch it because it's read-only in the serializer, and .update() bypasses clean().
EXPECTED: lead_score and health_score should ALWAYS remain in [0, 100]. If out-of-range, either raise ValidationError on API create/update or auto-clamp on save().
ACTUAL: Contact.lead_score has no validation at all. Account.health_score validation is present in clean() but is never called by serializers, viewsets, or .update() calls. The scoring tasks do clamp (line 522), but the clamp happens in Python before .update(), not in the database — a task failure after clamping but before .update() means an old out-of-range value stays live.
FIX: (1) Add a clean() method to Contact that clamps/validates lead_score. (2) Override Ticket/Account.save() to call self.clean() OR auto-clamp before save (discouraged — validation should fail, not silently fix). (3) In the scoring tasks, use annotate(F(...)) + Case/When in the .update() to clamp in SQL so no out-of-range value is ever written. (4) Consider a database CHECK constraint as a last-line defense.

### [contacts-crm-7] Account.health_score validation only in clean(), never called by API or scoring task
cat=Data Integrity | conf=medium | verdict=unverified | merged=[]
LOC: apps/contacts/models.py:49-56 (Account.clean()); apps/contacts/serializers.py:99-115 (AccountSerializer never calls clean()); apps/crm/tasks.py:526 (calculate_account_health_scores uses .update())
DESC: Account.clean() validates 0 <= health_score <= 100 (lines 49-56), but the validation is NEVER invoked because: (1) DRF ModelSerializer.save() does not call full_clean() by default. (2) The scoring task uses .update(), which bypasses model validation entirely. (3) The AccountSerializer has health_score as read_only, so API writes are blocked, but direct database modifications or task failures can leave out-of-range values. Unlike Contact, Account has the validation, but it's dead code.
REPRO: (1) Try to directly create an Account via the admin or shell with health_score=150. (2) If you call .clean() before .save(), it raises ValidationError. But .save() alone does not call .clean(). (3) The scoring task sets health_score via .update(), bypassing the check entirely.
EXPECTED: health_score should always be in [0, 100] at the database level, enforced by save() or a CHECK constraint.
ACTUAL: The validation exists but is not enforced. Only .clean() catches violations, and .clean() is never called.
FIX: Override Account.save() to call self.clean() before super().save(), OR add a CHECK constraint at the database level: ALTER TABLE contacts_account ADD CONSTRAINT check_health_score CHECK (health_score >= 0 AND health_score <= 100).

### [contacts-crm-9] Company custom_data never synced to CustomFieldValue (no post_save signal)
cat=Data Integrity | conf=medium | verdict=unverified | merged=[]
LOC: apps/custom_fields/signals.py:20-43 (only Ticket + Contact); apps/contacts/models.py:59-101 (Company.custom_data exists)
DESC: Ticket and Contact have a post_save signal receiver (apps/custom_fields/signals.py) that syncs custom_data JSON to CustomFieldValue EAV rows. However, Company has custom_data (line 87-91) but NO corresponding signal receiver. This means Company custom fields are never materialized into CustomFieldValue rows. If an agent sets a custom field on a Company via the API (e.g., POST /api/v1/companies/ with custom_data={'industry_category': 'tech'}), the custom_data is stored in the JSON blob but NOT synced to a CustomFieldValue row. Any downstream code expecting CustomFieldValue rows for Companies will silently find nothing.
REPRO: (1) Define a CustomFieldDefinition for module='company' (if the UI/API allows this). (2) Create/update a Company with custom_data containing that field. (3) Query CustomFieldValue.objects.filter(object_id=company_id) — it's empty. (4) The data is in Company.custom_data JSON but not in CustomFieldValue.
EXPECTED: Company.post_save should sync custom_data to CustomFieldValue rows, just like Ticket and Contact do.
ACTUAL: No sync happens. Company custom fields are stored only as JSON blobs, not in the EAV table. Any code expecting CustomFieldValue to be the source of truth will miss Company custom fields.
FIX: Add a post_save receiver for Company in apps/custom_fields/signals.py (lines 33-43 can be a template). Call sync_custom_field_values(instance, module='company').

### [kanban-7] Soft-deleted tickets leave orphaned CardPosition rows
cat=Data Integrity | conf=medium | verdict=unverified | merged=[]
LOC: apps/tickets/signals.py:642-675
DESC: remove_kanban_cards_on_ticket_delete only triggers on hard delete. Soft-deleted tickets (is_deleted=True) leave CardPosition rows orphaned in the database. The serializer hides them from API, but rows accumulate and cannot be restored when the ticket is restored.
REPRO: 1. Create ticket and add to board (card created). 2. Soft-delete the ticket. 3. CardPosition row still exists. 4. Restore ticket; card does not reappear.
EXPECTED: Soft-deleted tickets have their cards archived or restored alongside the ticket.
ACTUAL: Orphaned CardPosition rows remain; cards don't restore with tickets.
FIX: Add pre_save signal to detect is_deleted False→True transition and soft-delete associated cards, or handle restoration explicitly in the ticket restore endpoint.

### [attachments-7] GenericForeignKey Orphaning Has No Database Constraint
cat=Data Integrity | conf=medium | verdict=unverified | merged=[]
LOC: apps/attachments/models.py:27-33, Attachment model with GenericForeignKey
DESC: The Attachment model uses GenericForeignKey which is NOT a true database foreign key. When a target object (Ticket, Comment, Message) is deleted, the Attachment row remains orphaned in the database, pointing to a non-existent object. There's no database constraint preventing this, and no cascade-delete logic is implemented. This creates silent data loss and orphaned rows.
REPRO: 1. Create an Attachment on Ticket-X. 2. Delete Ticket-X via the API or database. 3. Query the Attachment: its content_object property may return None or raise an error depending on how the code handles orphaned references. 4. The Attachment row still exists in the database.
EXPECTED: Attachments should be either cascade-deleted when the parent is deleted, or deletion of objects with attachments should be prevented.
ACTUAL: Attachments become orphaned silently when the parent object is deleted.
FIX: Add pre_delete signal handlers for all attachable models (Ticket, Comment, Message, etc.) to cascade-delete Attachments when the parent is deleted. In apps/attachments/signals.py, add: @receiver(post_delete, sender=Ticket) def delete_ticket_attachments(sender, instance, **kwargs): Attachment.objects.filter(content_type=ContentType.objects.get_for_model(Ticket), object_id=instance.pk).delete(). Repeat for all attachable models.

### [templates-uiux-4] Description textarea missing maxlength constraint
cat=Data Integrity | conf=high | verdict=unverified | merged=[]
LOC: templates/pages/emails/list.html:414
DESC: No maxlength on textarea despite 20000 character backend limit, allows invalid submissions
REPRO: Open create ticket form, type over 20000 chars in description, submit and get 400 error
EXPECTED: Should have maxlength attribute matching backend validation
ACTUAL: No client-side validation for length limit
FIX: Add maxlength=20000 attribute to textarea

### [data-model-integrity-2] NewsPost CASCADE author deletion data loss
cat=Data Integrity | conf=high | verdict=confirmed | merged=[]
LOC: apps/newsfeed/models.py:29-32
DESC: NewsPost.author CASCADE deletes all posts when user removed, no audit trail.
REPRO: Create announcement, delete author, posts vanish.
EXPECTED: Posts preserved or orphaned.
ACTUAL: Hard-deleted silently.
FIX: Change to SET_NULL or PROTECT or implement soft-delete.

### [data-model-integrity-9] Soft-delete inconsistency across models
cat=Data Integrity | conf=medium | verdict=unverified | merged=[]
LOC: apps/tickets/models.py:562
DESC: Ticket soft-deletes but TicketStatus/Queue hard-delete, inconsistent design breaks FK.
REPRO: Delete TicketStatus in use, Ticket FKs break.
EXPECTED: Consistent soft-delete or PROTECT across models.
ACTUAL: Hard-delete breaks referential integrity.
FIX: Add is_deleted to config models or use PROTECT.

### [custom-fields-agents-6] due_notified_at filter in fire_due_reminders unindexed; F() predicate is sequential
cat=Performance | conf=medium | verdict=unverified | merged=[]
LOC: apps/crm/tasks.py:70-84 (fire_due_reminders task) + apps/crm/models.py:156-165
DESC: The fire_due_reminders task filters due reminders using .filter(Q(due_notified_at__isnull=True) | Q(due_notified_at__lt=F('scheduled_at'))). The due_notified_at field is NOT indexed (apps/crm/models.py:202-208 only indexes on scheduled_at/completed_at/cancelled_at). The F('scheduled_at') comparison requires a sequential filter even after the index on scheduled_at is applied. For tenants with millions of reminders, this sequential scan becomes expensive. The code comment acknowledges the existing reminder_overdue_idx 'partially' covers the query, but the F() predicate forces a post-index sequential filter. At 30s intervals with millions of rows, query time becomes noticeable (1–5s per task run).
REPRO: 1. Create a large tenant with 1M+ reminders. 2. Set Celery Beat to fire_due_reminders every 30s. 3. Monitor query execution time (Django debug toolbar, slow query log). Expected: <100ms. Actual: 1–5s sequential scan.
EXPECTED: The query should use fully-indexed predicates for fast execution.
ACTUAL: The due_notified_at__lt=F('scheduled_at') predicate requires a post-index sequential filter, causing O(n) table scans every 30s.
FIX: Add an index on (tenant, due_notified_at) or (tenant, scheduled_at, due_notified_at, completed_at, cancelled_at) to cover both the null check and the F() comparison fully. Or refactor the query to use simpler predicates that are fully indexable.

### [performance-db-2] Partial Index Condition Mismatch: HubEmail SLA Scan Includes ESCALATED but Index Excludes It
cat=Performance | conf=high | verdict=confirmed | merged=[]
LOC: apps/inbox_hub/models.py:240-244 vs apps/inbox_hub/tasks.py:32-38
DESC: The partial index on HubEmail for the SLA-breach hot path defines condition as state IN ['new', 'assigned', 'in_progress', 'pending_agent'], excluding 'escalated'. However, check_hub_sla_breaches task scans state__in=[NEW, ASSIGNED, IN_PROGRESS, PENDING_AGENT, ESCALATED]. Escalated emails fall through to full-table scan instead of using the partial index.
REPRO: 1. Create 1000+ HubEmails in various states. 2. Escalate 100+ of them. 3. Run check_hub_sla_breaches Beat task. 4. Check database query plans. 5. Observe full-table scan instead of index usage.
EXPECTED: Partial index condition and task filter must match exactly, or index should cover all scanned states.
ACTUAL: Partial index excludes ESCALATED but task includes ESCALATED in scan, causing emails to bypass the index.
FIX: Either remove ESCALATED from task's active_states list, OR add ESCALATED to partial index condition: condition=Q(state__in=['new','assigned','in_progress','pending_agent','escalated'])

### [performance-db-8] InboxViewSet.get_queryset() Missing Related Field Prefetch
cat=Performance | conf=medium | verdict=unverified | merged=[]
LOC: apps/inbound_email/api_views.py:354-365
DESC: InboxViewSet.get_queryset() selects only 'linked_ticket' but may be missing 'contact', 'assignee', or 'sender' fields accessed by InboxEmailListSerializer. Serializers accessing these relations without prefetch will trigger N+1 queries.
REPRO: 1. GET /api/v1/emails/inbox/ with 50+ items. 2. Review InboxEmailListSerializer for contact/assignee/sender access. 3. Monitor database queries.
EXPECTED: All relations accessed by serializer should be covered by select_related or prefetch_related.
ACTUAL: Only 'linked_ticket' is select_related; other serializer-accessed relations may not be prefetched.
FIX: Add select_related('contact','assignee') or verify serializer only accesses already-loaded relations.

### [performance-db-11] Missing select_related on DashboardWidget Serialization
cat=Performance | conf=medium | verdict=unverified | merged=[]
LOC: apps/analytics/views.py:99-100
DESC: DashboardWidgetViewSet includes select_related('user'), but serializer may access other related fields not prefetched. With 50+ widgets, N+1 queries possible on any accessed nested relations.
REPRO: 1. Create 50+ DashboardWidgets with different configurations. 2. GET /api/v1/dashboard-widgets/. 3. Monitor database queries for relations beyond 'user'.
EXPECTED: All serializer-accessed relations covered by select_related or prefetch_related.
ACTUAL: Only user is select_related; other relations may cause N+1.
FIX: Review DashboardWidgetSerializer for all accessed relations; add prefetch_related for M2M or nested objects.

### [data-model-integrity-6] InboundEmail save SELECT performance regression
cat=Performance | conf=high | verdict=unverified | merged=[]
LOC: apps/inbound_email/models.py:242-257
DESC: Unconditional SELECT on every save() to check immutable fields, even for non-immutable updates.
REPRO: Update non-immutable field and save, unnecessary SELECT executes.
EXPECTED: SELECT only when immutable fields modified.
ACTUAL: Extra query per email update.
FIX: Conditionally SELECT based on update_fields or cache old values.

### [tenant-isolation-7] Celery Tasks Missing Defensive tenant_context() Wrapping
cat=Reliability | conf=medium | verdict=unverified | merged=['authn-1', 'feature-a-reminder-2', 'notifications-1']
LOC: /home/kavin/crm/apps/crm/tasks.py:68-157 (fire_due_reminders, check_overdue_reminders)
DESC: Beat tasks fire_due_reminders and check_overdue_reminders correctly use .unscoped with explicit tenant= filtering. However, they do not wrap per-tenant work in tenant_context(). While the code is correct as-is, this violates defensive programming: if a called service function (send_notification, broadcast_live_event) changes in the future to use implicit context, the next deployment could leak data. The contract is implicit rather than explicit.
REPRO: 1. A future developer modifies send_notification() to internally call get_current_tenant(). 2. Deploy. 3. fire_due_reminders runs and sends notifications to the wrong tenant due to lingering context from a previous task iteration.
EXPECTED: All tenant-specific work in per-tenant tasks should be wrapped in `with tenant_context(tenant):` to make the intent explicit and protect against downstream regressions.
ACTUAL: Tasks use .unscoped and explicit tenant= (correct), but do not use tenant_context() (defensive gap).
FIX: Wrap the per-tenant work in each task: `with tenant_context(tenant): [all work for this tenant]`. This makes the invariant explicit and prevents future regressions if a called service changes.

### [authz-rbac-4] Expired temporary roles are never automatically cleared from the database
cat=Reliability | conf=high | verdict=confirmed | merged=[]
LOC: apps/accounts/models.py:274-282, apps/agents/views.py:610-623, no cleanup task exists
DESC: When a temporary role is granted with an expiry time, the expiry is checked on-the-fly via has_active_temporary_role property, which checks if temporary_role_expires_at > now(). However, there is NO background task that periodically clears expired temporary roles by setting the fields back to NULL. The database row carries stale data indefinitely, and if there is ever a logic error in the expiry check or a future refactor that caches the value, the escalation will persist. This is also a data hygiene issue: the audit trail shows a grant that is 'no longer active' but the row is never cleaned up.
REPRO: 1. Grant User a temporary Manager role with expires_at=now+1 hour. 2. Wait >1 hour. 3. Query TenantMembership.objects.filter(temporary_role__isnull=False) — the row still exists with temporary_role set. 4. has_active_temporary_role correctly returns False (since now > expires_at), but the row is never cleared.
EXPECTED: Either a Beat task runs every 1-5 minutes and clears rows where temporary_role_expires_at <= now(), OR a pre_save signal auto-clears if now() > temporary_role_expires_at.
ACTUAL: Expired temporary roles remain in the database indefinitely with no cleanup mechanism.
FIX: Add a Celery Beat task 'cleanup_expired_temporary_roles' (schedule: 300s) that does: TenantMembership.unscoped.filter(temporary_role_expires_at__lt=now()).update(temporary_role=None, temporary_role_expires_at=None, temporary_role_granted_by=None, temporary_role_granted_at=None). Also clear temporary_permissions for those rows.

### [tickets-services-sla-2] SLA business-hours iteration cap (365*24 = 8760) may silently truncate far-future deadlines
cat=Reliability | conf=high | verdict=unverified | merged=[]
LOC: apps/tickets/sla.py:353 (_add_business_minutes), 402 (_next_business_day_start)
DESC: The _add_business_minutes function iterates for at most 365*24=8760 iterations (one per hour). If a tenant configures a very large SLA policy (e.g., 100,000+ business minutes = 69+ days), the function will exhaust its iteration cap and return the current time without adding all remaining minutes. Similarly, _next_business_day_start only loops 365 times and falls through if no business day is found. The code includes no warning or error on cap exhaustion, so the SLA deadline will silently be wrong.
REPRO: 1. Create an SLA policy with an unusually large policy.first_response_minutes or policy.resolution_minutes (e.g., 100,000 minutes = ~69 days). 2. Create a ticket with that priority. 3. Call initialize_sla(ticket) and observe that sla_first_response_due is much sooner than expected. 4. Enable business hours with aggressive holidays (e.g., 300 holiday dates per year) and verify the deadline is truncated.
EXPECTED: SLA deadlines should correctly add the full policy duration even for large multiples of business minutes and aggressive holiday schedules.
ACTUAL: If the loop cap is exhausted (8760 iterations, or 365 days worth), the calculation stops early and returns an earlier-than-correct deadline. No logging or error indicates this happened.
FIX: Remove the hard iteration cap or make it much larger (e.g., 365*4 for 4-year cap). Alternatively, detect cap exhaustion and log an ERROR before returning, so admins notice misconfigured SLAs. Or restructure _add_business_minutes to use a mathematical approach (calendar-based leap-day calculation) instead of iteration.

### [tickets-services-sla-8] Auto-close and CSAT survey tasks are scheduled AFTER commit, risking race with concurrent transitions
cat=Reliability | conf=medium | verdict=unverified | merged=[]
LOC: apps/tickets/services.py:721-730 and 863-921 (task scheduling)
DESC: When a ticket transitions to Resolved, auto-close task is scheduled via transaction.on_commit() with an ETA (e.g., 5 days out). If the ticket is manually closed or re-opened before the ETA, the task will still attempt to close it on schedule, potentially causing inconsistencies. The task body only checks if auto_close_task_id is set, not the current status.
REPRO: 1. Transition ticket to Resolved (auto-close scheduled for 5 days). 2. Manually Close the ticket within 5 days. 3. Wait for auto-close ETA. 4. Task finds resolved_at set and may attempt to close an already-closed ticket.
EXPECTED: Auto-close should only proceed if the ticket is currently in Resolved status.
ACTUAL: Task only checks auto_close_task_id, not current status. May double-close or fail silently.
FIX: In the task body, check that current status is Resolved and auto_close_task_id matches before closing. Return early if status has changed.

### [tickets-services-sla-10] SLA policy escalation may fire multiple times without deduplication
cat=Reliability | conf=medium | verdict=unverified | merged=[]
LOC: apps/tickets/tasks.py:120-121 (_check_escalation_rules), comment mentions 'dedup via TicketActivity'
DESC: The _check_escalation_rules function is called every 120 seconds. If an escalation rule matches a ticket, it fires again on every scan, creating duplicate internal comments. The code comment claims 'dedup via TicketActivity' but implementation does not show this.
REPRO: 1. Create an escalation rule that fires after 2 hours. 2. Wait 2+ hours. 3. Run check_sla_breaches; rule fires. 4. Wait 120s; run again. 5. Same rule fires again (duplicate comment).
EXPECTED: Escalation rules should fire at most once per ticket (or with clear deduplication).
ACTUAL: Rules fire repeatedly every 120 seconds as long as condition matches.
FIX: Implement deduplication: check if TicketActivity with event=ESCALATED and rule_id already exists. Only fire if no such entry found in past N hours.

### [tickets-signals-1] Kanban card ordering racy: concurrent card creation can produce duplicate ordering within a column
cat=Reliability | conf=high | verdict=confirmed | merged=['kanban-1', 'kanban-2']
LOC: apps/tickets/signals.py:511, 569, 622
DESC: When creating or moving a kanban card, the code calculates the new order position via `target_column.cards.count()`. This is a TOCTOU (time-of-check-time-of-use) race: between the count and the insert, another concurrent save could insert a card with the same order value. Lines 511, 569, and 622 all exhibit this pattern.
REPRO: 1. Create two tickets simultaneously (both trigger post_save). 2. Both target the same kanban column (same status). 3. Both call `target_column.cards.count()` and get the same value (e.g., 3). 4. Both insert with order=3. 5. Column now has two cards with identical order values.
EXPECTED: Each card in a column should have a unique, sequential order. No two cards should share the same order value within a single column.
ACTUAL: Without database-level locking or atomic increment, concurrent creates can produce duplicate order values, resulting in undefined sort order for cards in that column.
FIX: Use atomic database operations: either (a) use `F('order')` expressions with `select_for_update()` on the column, or (b) delegate ordering to a post-INSERT database trigger, or (c) use a unique_together constraint + handle IntegrityError by re-querying the max order.

### [tickets-signals-7] Activity dedup window may miss duplicate logging under very high concurrency
cat=Reliability | conf=medium | verdict=unverified | merged=[]
LOC: apps/tickets/signals.py:183-195
DESC: The dedup logic assumes that if an activity log entry was created within the last 2 seconds, it's a duplicate. However, this window is NOT atomic. If two concurrent requests both call `_activity_already_logged()` in the same millisecond, both may see no recent log (the first hasn't committed), and both proceed to call `log_activity()`, creating duplicate entries.
REPRO: 1. Send two concurrent PATCH requests to the same ticket. 2. Both bypass the `_skip_signal_logging` flag. 3. Both reach `log_ticket_activity` post_save. 4. Both call `_activity_already_logged()` simultaneously. 5. Both see no recent ActivityLog, so both proceed. 6. Both call `log_activity()` and commit. 7. Audit log now has two identical entries.
EXPECTED: Dedup should be atomic or rare duplicates should be documented as acceptable.
ACTUAL: Best-effort dedup via a 2-second window. High concurrency can bypass it, producing near-duplicate audit logs.
FIX: Document this as a known limitation: 'The activity dedup window is best-effort and may produce rare duplicates under very high concurrency.' Alternatively, implement proper dedup via a unique constraint on (ticket_id, action, created_at rounded to minute) or use Redis-backed atomic dedup.

### [tickets-signals-8] Error handling in kanban sync operations swallows exceptions and hides failures
cat=Reliability | conf=medium | verdict=unverified | merged=[]
LOC: apps/tickets/signals.py:524-527, 578-579, 633-639
DESC: The kanban sync handlers wrap all operations in a try-except block that logs a warning and silently continues. While this prevents a kanban failure from crashing the ticket save, it can mask real errors (database corruption, missing board, invalid FK) that should be escalated. Errors are logged at WARNING level only.
REPRO: 1. Corrupt the kanban board or column data (e.g., delete the board). 2. Try to save a ticket with a status change. 3. The kanban sync tries to find the board and fails. 4. Exception is caught and logged at WARNING level. 5. Ticket save succeeds, but kanban is broken. 6. Admin may never notice the warning in a busy system.
EXPECTED: Kanban failures should be logged at ERROR level or trigger an alert.
ACTUAL: All kanban errors are caught, logged at WARNING, and silently ignored. The ticket save succeeds even though kanban is broken.
FIX: Change logger.warning to logger.error for kanban sync failures. Alternatively, make kanban operations a background task that retries on failure, so a kanban error doesn't hide behind a successful ticket save. For now, at minimum escalate the log level to ERROR.

### [inbound-email-6] IMAP Message Recipient Resolution Could Fail Silently
cat=Reliability | conf=medium | verdict=unverified | merged=[]
LOC: apps/inbound_email/imap_poller.py:347-356
DESC: The IMAP poller attempts to extract the recipient email from Delivered-To, X-Delivered-To, To headers, or falls back to IMAP_USER. However, if all these headers are missing or empty, and IMAP_USER is not configured, the recipient will be an empty string. This empty recipient is then used to resolve the tenant, which will fail to match any tenant (empty string won't match any inbound_email_address). The email will then be rejected with 'Could not resolve tenant from recipient'. This could happen with auto-generated system emails or malformed messages, causing silent ingestion failures.
REPRO: 1. Receive an email via IMAP with no Delivered-To, X-Delivered-To, or To headers (or empty values). 2. Do not configure IMAP_USER in settings. 3. The email will be ingested as an InboundEmail but will fail to resolve a tenant. 4. It will be marked as REJECTED.
EXPECTED: Either the email should be rejected at the IMAP stage (like SMTP does with a 550), or a fallback tenant (via IMAP_DEFAULT_TENANT_SLUG) should be resolved.
ACTUAL: The email is created in the database with an empty recipient_email, then rejected during processing because no tenant resolves. The message is lost.
FIX: Add validation in `_ingest_one()` to check if recipient is empty after fallback resolution. If empty, log a warning and return False to mark as seen but not ingest: `if not recipient: logger.warning('IMAP message has no recipient address'); _mark_seen(conn, uid); return False`.

### [inbound-email-7] IMAP Poll State Can Be Lost on Server Restart During Batch
cat=Reliability | conf=medium | verdict=unverified | merged=[]
LOC: apps/inbound_email/imap_poller.py:157-173
DESC: While the IMAP poller advances the watermark as it goes (good), there's a race condition: if the process dies between fetching a UID and advancing the watermark in the database, that message won't be re-ingested on the next poll (because the watermark will be at the highest UID fetched, not the highest *successfully ingested* UID). Additionally, if the process dies while saving an attachment or during InboundEmail creation (before `_ingest_one` returns True), the message will be marked as seen but the InboundEmail row might not be created, leading to message loss.
REPRO: 1. Set up IMAP polling with a large batch of messages. 2. Kill the process after it successfully ingests messages 1-10 but before message 11 completes. 3. On restart, the watermark may be at UID 11 (because it was fetched), but message 11's InboundEmail.create() call failed. 4. Message 11 is marked as seen in the mailbox but has no InboundEmail record.
EXPECTED: The watermark should only advance after successful InboundEmail creation and attachment processing, with all-or-nothing semantics.
ACTUAL: The watermark advances per-UID even if attachment processing or later steps fail, potentially losing messages.
FIX: Wrap the entire `_ingest_one()` logic in exception handling that returns False on any failure, ensuring the watermark only advances when the full ingest succeeds. Additionally, use database transactions to ensure atomicity of InboundEmail creation and attachment processing.

### [inbox-hub-engine-9] assign_to Capacity Check Race Condition (Multiple Concurrent Assignments)
cat=Reliability | conf=medium | verdict=unverified | merged=[]
LOC: apps/inbox_hub/assignment.py:107-114
DESC: The assign_to function locks HubEmail but checks AgentAvailability.is_assignable (a property derived from current_ticket_count) without a race guard when require_online=False. Between line 102 and line 132, another process could increment current_ticket_count, causing the agent to exceed max capacity. While the F() increment is atomic, the read-check window is vulnerable.
REPRO: 1. Create AgentAvailability with max=5, current=4. 2. Two assign_to calls (require_online=False) race. 3. Both read count=4, both pass capacity check, both increment to 5 and 6. 4. Agent over-subscribed.
EXPECTED: Capacity check must be atomic with the increment.
ACTUAL: Read-check and increment are not atomic; concurrent calls can both pass the check and both increment.
FIX: Re-check capacity after incrementing, or use a database constraint to enforce max capacity.

### [feature-a-reminder-3] Stale recipient data used if user modified between fetch and send_notification
cat=Reliability | conf=medium | verdict=unverified | merged=[]
LOC: apps/crm/tasks.py:81, 90, 117
DESC: The task does select_related('assigned_to', 'created_by') at line 81, fetching the user object into the QuerySet. If the user's is_active status or other properties change between the fetch and send_notification (line 117), send_notification will use stale in-memory user data. send_notification does not validate is_active, so an inactive user can still receive a notification.
REPRO: 1. Create a reminder assigned to an active user. 2. Trigger fire_due_reminders to reach send_notification. 3. In another process, deactivate the user. 4. Let fire_due_reminders complete and call send_notification with the stale is_active status. 5. Check Notification — it exists with the deactivated user.
EXPECTED: Before calling send_notification, verify the recipient is still active and eligible.
ACTUAL: send_notification uses the stale in-memory user object without re-checking is_active or other eligibility constraints.
FIX: Add a check before send_notification: `if not recipient.is_active: continue`. Alternatively, refresh the recipient object from the database: `recipient.refresh_from_db()`.

### [feature-b-overrides-2] Bare Exception Handler Masks Unexpected Errors
cat=Reliability | conf=high | verdict=confirmed | merged=[]
LOC: apps/inbound_email/api_views.py:291-294
DESC: Bare 'except Exception:' when accessing email.hub_email masks database errors, connection failures, and system errors. Any error becomes 'no hub email' fallback instead of generating 500 and triggering alerting.
REPRO: Simulate database connection error during hub_email query. Instead of 500 error with logging, bare except swallows error and proceeds to legacy path, masking the real failure.
EXPECTED: Catch only RelatedObjectDoesNotExist, not all Exceptions. Let unexpected errors propagate naturally to generate 500s.
ACTUAL: Bare 'except Exception:' catches database errors, timeouts, memory errors, etc., silently treating all as missing hub_email.
FIX: Replace with 'except (RelatedObjectDoesNotExist, AttributeError):' or 'except ObjectDoesNotExist:' from django.core.exceptions.

### [notifications-2] Contact reply email queued without checking Contact.email_bouncing
cat=Reliability | conf=high | verdict=unverified | merged=[]
LOC: apps/notifications/signal_handlers.py:181-226 (calls into apps/tickets/email_service.py:265-318)
DESC: When a public comment is added to a ticket, _queue_contact_reply_email sends an email to ticket.contact.email without checking if Contact.email_bouncing is True. A contact with a flagged bouncing address will have the task queued and will retry 3 times (tickets/tasks.py:587-591 max_retries=3) before finally failing, wasting database writes and SMTP round-trips on addresses known to be undeliverable.
REPRO: Create a ticket with a contact whose email has bounced (contact.email_bouncing=True); create a public comment on the ticket; observe send_ticket_reply_email_task.delay() is called; check Celery logs to see the task retries 3 times before it fails on SMTP.
EXPECTED: Before queueing the task, check contact.email_bouncing and skip the queuing if True, similar to how send_ticket_email checks is_undeliverable_email().
ACTUAL: The function checks only that contact.email exists and author.email != contact.email, then queues the task unconditionally. The check for undeliverable addresses happens only later in send_ticket_email() (after task already enqueued and retried), not in the signal handler's queueing decision.
FIX: In _queue_contact_reply_email (line 181), add: if contact.email_bouncing: return (with optional debug log). This avoids queueing the task in the first place.

### [websockets-6] ChatConsumer parent message lookup lacks UUID format validation before database query
cat=Reliability | conf=medium | verdict=unverified | merged=[]
LOC: apps/messaging/consumers.py:246-253 (_create_message method)
DESC: The _create_message method looks up a parent message with `Message.unscoped.get(pk=parent_id, conversation=conversation)` without validating that parent_id is a valid UUID first. Unlike conversation_id which is validated in connect() (line 60-64), a malformed parent_id could raise an exception during the get() call instead of being caught and converted to a client error.
REPRO: 1. Send a message with an invalid UUID in parent_id (e.g., 'not-a-uuid'). 2. Observe that the get() call raises a ValueError rather than being caught and converted to a client error message.
EXPECTED: parent_id should be validated as a UUID before attempting the database lookup. Invalid UUIDs should result in a client error response, not a server exception.
ACTUAL: A malformed parent_id causes an unhandled exception in the database lookup.
FIX: 1. Validate parent_id as a valid UUID in _handle_send_message before calling _create_message. 2. If invalid, send a client error response. 3. Optionally, use try/except in _create_message to catch UUID validation errors and return None for parent (graceful degradation).

### [websockets-7] Reminder-due notification sent before due_notified_at stamp is persisted; stamp loss on crash allows duplicate alerts
cat=Reliability | conf=medium | verdict=unverified | merged=[]
LOC: apps/crm/tasks.py:102-130 (fire_due_reminders, claim-first pattern)
DESC: The fire_due_reminders task stamps due_notified_at (line 102-104) before send_notification (line 117-130) to prevent duplicates on failure retry. However, the UPDATE is not verified to be persisted before sending, and the task runs outside an explicit atomic block. If the Celery worker crashes after UPDATE but before transaction commit, due_notified_at is not persisted. On retry, the reminder re-fires because the stamp was lost, creating a duplicate alert.
REPRO: 1. Modify fire_due_reminders to simulate a long-running UPDATE: add a sleep after Reminder.unscoped.filter().update(). 2. Kill the Celery worker mid-sleep, before the transaction commits. 3. On retry, the reminder fires again because due_notified_at was not persisted.
EXPECTED: The stamp should be written atomically with the task completion, or the task should verify the stamp was written before sending the notification.
ACTUAL: The UPDATE is issued but not verified; if the process crashes before commit, the stamp is lost and the reminder re-fires on retry.
FIX: 1. Wrap the entire claim + send flow in a transaction.atomic() block so the UPDATE commits before send_notification. 2. Or, read back the updated row to confirm the stamp: `reminder.refresh_from_db()` to verify due_notified_at was persisted. 3. Or, use select_for_update to lock the row and ensure atomicity.

### [contacts-crm-3] ContactEvent and last_activity_at changes are invisible to live broadcast layer
cat=Reliability | conf=high | verdict=unverified | merged=[]
LOC: apps/contacts/services.py:14-44; apps/contacts/signals.py (no signal for ContactEvent); apps/crm/tasks.py:318-425 (calculate_lead_scores uses ContactEvent but no signal emitted)
DESC: The log_contact_event function (apps/contacts/services.py:31-44) creates a ContactEvent and updates Contact.last_activity_at via .update() (line 44), which BYPASSES the post_save signal. This means: (1) The contact.updated live broadcast (apps/contacts/signals.py:73-79) never fires, so clients don't see the new last_activity_at in real-time. (2) ContactEvent rows are append-only and intentionally do NOT trigger a broadcast (as documented in signals.py:7-11), so the timeline doesn't update live. The CLAUDE.md docstring at signals.py:9-10 explicitly says 'A page that needs to react to those still gets a contact.updated from the contact.last_activity_at save that the event-recorder triggers' — but that save is a .update() call that does NOT trigger post_save. This is a documentation vs. implementation mismatch.
REPRO: (1) Open the contact timeline in one browser. (2) In another browser, trigger a contact event (e.g., a ticket comment on a contact's ticket). (3) The log_contact_event task calls Contact.unscoped.filter(...).update(last_activity_at=now), which does not fire post_save. (4) The first browser's timeline does NOT refresh. (5) The sidebar 'last_activity_at' field does not update until page reload or 60s cache expires.
EXPECTED: When a ContactEvent is created and last_activity_at is updated, a broadcast_live_event('contact.updated', ...) should fire so all connected clients see the new timestamp instantly.
ACTUAL: No broadcast fires because the .update() call bypasses Django signals. The live layer is NOT aware of contact activity changes. Clients see stale last_activity_at values until cache TTL expires.
FIX: (1) After the .update() in log_contact_event (line 44), immediately broadcast: broadcast_live_event(contact.tenant, 'contact.updated', _serialise_contact(contact)) — BUT you must refresh the contact from the DB first since .update() doesn't refresh the instance. (2) OR: call contact.save() instead of .update(), accept the post_save signal overhead. (3) Document the intent: is last_activity_at broadcast a nice-to-have or a required real-time feature?

### [contacts-crm-4] Score calculation tasks use .update() which bypasses signals and real-time broadcast
cat=Reliability | conf=medium | verdict=unverified | merged=[]
LOC: apps/crm/tasks.py:418 (calculate_lead_scores), line 526 (calculate_account_health_scores)
DESC: Both calculate_lead_scores and calculate_account_health_scores use Contact.unscoped.filter(...).update(lead_score=...) and Account.unscoped.filter(...).update(health_score=...) for performance (bulk update vs. per-row save). This is sensible, but it means the post_save signal never fires, so the live broadcast (apps/contacts/signals.py:73-79) does NOT emit contact.updated/account.updated events. Clients subscribed to the live channel will NOT see the score changes in real-time; they see stale scores until a page reload.
REPRO: (1) Open the contacts list in a browser; a contact shows lead_score=50. (2) The nightly calculate_lead_scores task runs and updates the contact to lead_score=75. (3) The contacts list does NOT refresh; the score is still 50. (4) Refresh the page; now it shows 75.
EXPECTED: After a bulk score update, the live layer should be notified so clients can update in-place or refetch the affected rows.
ACTUAL: No broadcast fires. Clients do not see score updates until page reload. The scores are correct in the database but stale in connected browsers.
FIX: After the bulk .update() in both tasks, call broadcast_live_event for each affected contact/account OR schedule an async task to emit the broadcasts. A simpler approach: publish a single 'scores_recalculated' event that lists the updated contact/account IDs, and clients refetch just those rows. This avoids flooding the channel with N broadcasts for N contacts.

### [contacts-crm-5] Reminder.due_notified_at re-arm logic has no select_for_update, risking duplicate alerts on concurrent workers
cat=Reliability | conf=medium | verdict=unverified | merged=[]
LOC: apps/crm/tasks.py:70-104 (fire_due_reminders)
DESC: The fire_due_reminders task filters reminders with 'due_notified_at IS NULL OR due_notified_at < scheduled_at' (lines 77-79). For each reminder, it CLAIMS the row by stamping due_notified_at=now via .update() (lines 102-104) BEFORE sending the notification. However, there is NO select_for_update() on the initial queryset. If two Celery workers both execute the task in the same 30-second beat interval and both match the same reminder, they BOTH fetch it into memory (line 86), then BOTH claim it (line 102), then BOTH send notifications (line 117). The task logs state this is claim-first (lines 94-101) to prevent re-fire on downstream failures, but the claim itself is not atomic with the initial SELECT.
REPRO: (1) Set up a Celery worker pool with multiple workers. (2) Trigger a reminder that's due now. (3) The beat scheduler fires fire_due_reminders on both workers simultaneously (or within the chunk_size window). (4) Both workers fetch the same reminder, both claim it with .update(), both send notifications. (5) The user gets 2 popup alerts instead of 1.
EXPECTED: Only one worker should claim and notify the reminder. The others should skip it.
ACTUAL: Multiple workers can claim the same reminder in a race condition, resulting in duplicate notifications.
FIX: Add .select_for_update() to the due_reminders queryset (line 71) to lock rows: 'due_reminders = (...).select_for_update().iterator(...)'. This ensures only one worker can claim a row at a time. Note: iterator() with select_for_update() may not lock in all database backends; a safer approach is to use a chunk-loop without iterator() if the data set is manageable.

### [billing-5] Fire-Due-Reminders Task Unprotected Against Concurrent Beat Instances
cat=Reliability | conf=medium | verdict=unverified | merged=[]
LOC: apps/crm/tasks.py:102-104
DESC: fire_due_reminders task uses update due_notified_at without select_for_update. With multiple concurrent Celery Beat instances two ticks could read same reminder as eligible and both send duplicate notifications.
REPRO: Deploy with multiple Celery Beat instances running concurrently. Create reminder with scheduled_at = now. Both beat instances tick simultaneously. Both execute SELECT reminders WHERE due_notified_at IS NULL. Both see same reminder as eligible. Both execute UPDATE due_notified_at and both send_notification. User receives reminder popup twice.
EXPECTED: Only one notification per reminder even with concurrent Beat instances. Claim must be atomic and mutually exclusive.
ACTUAL: Missing select_for_update lock. Concurrent beat instances can read same state before either stamps it.
FIX: Use select_for_update in transaction to lock rows before reading. Change to: for reminder in due_reminders.select_for_update()

### [analytics-exports-3] process_export_job.delay() called outside transaction.on_commit(), can strand job at PENDING
cat=Reliability | conf=high | verdict=confirmed | merged=[]
LOC: apps/analytics/serializers.py:147, method ExportJobCreateSerializer.create
DESC: The Celery task process_export_job.delay() is triggered INSIDE the serializer create() method BEFORE the transaction commits. If the request transaction rolls back after the task is queued (e.g., due to a middleware exception or signal handler failure), the task will run against a non-existent ExportJob, marking it as FAILED with 'Job not found' after waiting for task execution.
REPRO: 1. Patch a middleware/signal to raise an exception after ExportJobCreateSerializer.create() but before transaction commit. 2. POST /api/v1/analytics/exports/ with valid payload. 3. Observe task execution tries to load the ExportJob, fails with DoesNotExist, and returns early (line 39). 4. The job row is never inserted (transaction rolled back), but task ran (task was queued before commit).
EXPECTED: Celery task should be queued AFTER transaction.on_commit() to ensure the ExportJob exists when the task executes.
ACTUAL: Task is queued immediately in create(), before the transaction commits. A rollback leaves the task stranded with a non-existent job ID.
FIX: Wrap process_export_job.delay() in a transaction.on_commit() callback: `transaction.on_commit(lambda: process_export_job.delay(str(instance.id)))`.

### [analytics-exports-6] fire_due_reminders lacks select_for_update, theoretical race condition with multiple workers
cat=Reliability | conf=medium | verdict=unverified | merged=[]
LOC: apps/crm/tasks.py:102-104, function fire_due_reminders
DESC: The claim-first watermarking (Reminder.unscoped.filter(pk=reminder.pk).update(due_notified_at=now)) uses a SELECT-then-UPDATE pattern without row-level locking (select_for_update). If two workers execute the task simultaneously and both read the same reminder before either writes, both could fire the same alert. The docstring acknowledges this but relies on single-beat (celery-beat running on one scheduler) to avoid it.
REPRO: In a scenario with multiple celery-beat schedulers (or a single-beat failure): 1. Run fire_due_reminders task on two workers in parallel. 2. Both workers fetch the same due_reminder from the iterator. 3. Both call Reminder.unscoped.filter(pk=...).update(due_notified_at=now) (non-atomic cross-check). 4. Both proceed to send_notification. 5. Same reminder fires twice.
EXPECTED: Row-level locking (select_for_update) should prevent the race condition, or code should document the single-beat requirement more prominently.
ACTUAL: No select_for_update is used. Task relies on Beat being single (no multiple schedulers), which is not guaranteed by default.
FIX: Add .select_for_update() to the Reminder filter before .update(), or use a database constraint + ON CONFLICT DO UPDATE (PostgreSQL-specific, requires database-level dedup). Alternatively, document the single-beat requirement in code and deployment docs.

### [attachments-8] File Size Validation Redundancy
cat=Reliability | conf=medium | verdict=unverified | merged=[]
LOC: apps/attachments/views.py:193-196 vs apps/attachments/validators.py:67-72
DESC: File size validation happens in two places: (1) validate_file_upload() checks MAX_FILE_SIZE_BYTES=25MB, and (2) PlanLimitChecker.check_storage() also enforces storage quotas. Both perform size checks, creating redundancy. The file is already loaded into memory during MIME detection (line 79 of validators.py reads 2048 bytes) by the time PlanLimitChecker runs. If PlanLimitChecker fails, the file has already been validated and loaded, wasting cycles. Error messages may differ between the two validators, confusing users.
REPRO: Upload a 25 MB file. The size check in validate_file_upload uses > not >=, so 25 MB exactly passes. Then PlanLimitChecker may also validate against the plan's storage quota, resulting in potentially two different error messages.
EXPECTED: Single, unified validation with clear error messages distinguishing 'file too large' vs 'storage quota exceeded'.
ACTUAL: Two separate validation checks with potentially different error messages.
FIX: Consolidate validation: keep the hard 25 MB size check as a security/sanity limit, and keep the plan quota check as a billing control. Combine them into one validation method with clear error messages. Document the 25 MB cap prominently.

### [custom-fields-agents-7] Presence reaper has no select_for_update; concurrent Beat workers cause redundant updates
cat=Reliability | conf=medium | verdict=unverified | merged=[]
LOC: apps/agents/tasks.py:44-48 (reap_stale_presence task)
DESC: The reap_stale_presence task iterates stale ONLINE agents and calls .update(status=AWAY) per row (line 45–48). There is no select_for_update() lock on the query or the updates. If two Celery Beat workers run simultaneously (possible if clock=true on multiple instances, though not recommended), two reaper iterations could find the same stale agent and both update it, causing redundant broadcasts and log spam. More critically, a race exists with touch_presence(): reaper reads agent at ONLINE; touch_presence() is called concurrently and re-stamps last_seen making presence_fresh=True; reaper then updates to AWAY, clobbering the freshen. The agent is marked away despite an active heartbeat.
REPRO: 1. Deploy PM2 with 2+ celery-beat instances (clock=true on both; bad config but possible). 2. At 60s reaper tick: worker1 and worker2 both call reap_stale_presence. 3. Both find the same stale agent row with status=ONLINE. 4. Both call .update(status=AWAY). 5. Both broadcast agent.presence AWAY. 6. Observe: agent.presence event broadcasted twice (redundant). 7. If a heartbeat ping arrives between steps 3 and 4: agent is re-promoted to ONLINE by touch_presence before reaper's update lands; reaper then overwrites it to AWAY, causing stale presence state.
EXPECTED: Only one reaper should run globally (clock=true on exactly one PM2 worker). Updates should be protected by row-level locking to prevent concurrent clobbering with presence writes.
ACTUAL: No row-level lock; concurrent reaper instances or overlaps with heartbeat cause redundant updates, broadcasts, and transient stale presence state.
FIX: Enforce clock=true on only one Beat worker (document in PM2 config and README). Or add select_for_update() to the reaper's query and wrap in transaction.atomic(). Or use a distributed lock (Redis SETNX) to ensure only one reaper runs globally.

### [settings-secrets-6] CRM_webhooks queue routed but empty (no actual billing task consumers)
cat=Reliability | conf=medium | verdict=unverified | merged=[]
LOC: main/celery.py:21, apps/billing/tasks.py (does not exist)
DESC: The main/celery.py routes 'apps.billing.tasks.*' to 'crm_webhooks' queue, which IS consumed by the worker. However, /home/kavin/crm/apps/billing/tasks.py does not exist. This queue is technically operational but carries no tasks (since there are no billing tasks defined). The worker wastes a queue subscription on an empty route, and any billing tasks defined in the future would silently be dropped unless the developer remembers this route and creates the file.
REPRO: 1. Check ecosystem.config.js: worker subscribes to crm_webhooks. 2. Check main/celery.py: route points to apps.billing.tasks.*. 3. Check apps/billing/: no tasks.py file exists. 4. If a developer adds a billing task without remembering this route, the task goes to crm_webhooks but crm_webhooks has no handler logic.
EXPECTED: Either (a) remove the unused crm_webhooks route and let billing tasks default to crm_default, or (b) create a minimal apps/billing/tasks.py with a comment explaining the route, or (c) document this footgun clearly.
ACTUAL: crm_webhooks queue is routed but no tasks use it, creating a silent bug-prone configuration.
FIX: Remove the 'apps.billing.tasks.*' route from main/celery.py (line 21), or if billing tasks are planned: create apps/billing/tasks.py with a clear comment and a placeholder/null task to document the intent.

### [settings-secrets-8] Makefile 'stop' and 'restart' targets omit crm-smtp process
cat=Reliability | conf=medium | verdict=unverified | merged=[]
LOC: Makefile:56, 60
DESC: The Makefile 'stop' and 'restart' targets explicitly list the processes to stop/restart but omit 'crm-smtp'. Line 56: `pm2 stop crm-django crm-celery-worker crm-celery-beat crm-flower` (no crm-smtp). This means calling 'make stop' or 'make restart' leaves the SMTP server running in production, potentially accepting stale mail or serving outdated code. The SMTP server has no auto-reload mechanism and would continue running indefinitely.
REPRO: 1. Run `make stop` in production. 2. Observe that crm-smtp process is still running (check `pm2 list`). 3. New deployments using `make restart` won't restart SMTP, leading to old SMTP code running.
EXPECTED: Both 'stop' and 'restart' targets should include 'crm-smtp' in the pm2 command list.
ACTUAL: crm-smtp is omitted from 'stop' and 'restart' commands, causing it to keep running.
FIX: Update Makefile lines 56 and 60 to include crm-smtp: `pm2 stop crm-django crm-celery-worker crm-celery-beat crm-flower crm-smtp` and `pm2 restart crm-django crm-celery-worker crm-celery-beat crm-flower crm-smtp`

### [frontend-js-5] Inconsistent WebSocket heartbeat timeouts across channels
cat=Reliability | conf=medium | verdict=unverified | merged=[]
LOC: static/js/live-connection.js:42-43 (25s heartbeat, 8s timeout) vs static/js/app.js (notifications, no heartbeat) vs static/js/ticket-feed.js (ticket-feed, no heartbeat)
DESC: Only live-connection has an active heartbeat (ping every 25s, expect pong within 8s). Notifications and ticket-feed rely on passive timeout. A stuck connection (TCP connected but no data) would hang indefinitely on those two but recover within 8 seconds on live.
REPRO: 1. Open a page with all 3 WS channels. 2. Simulate stuck connection (TCP DROP rule). 3. Observe live-connection detects it within 8s. 4. Notifications and ticket-feed do not update until user action or natural timeout (may be minutes).
EXPECTED: Consistent health-check strategies across all WebSocket consumers.
ACTUAL: Only live-connection has heartbeat; others rely on passive timeout.
FIX: Add heartbeat logic to notifications and ticket-feed, or document the intentional difference.

### [frontend-js-6] Potential event listener accumulation in notification item binding
cat=Reliability | conf=medium | verdict=unverified | merged=[]
LOC: static/js/app.js:610-637 (bindNotifClicks function)
DESC: bindNotifClicks() re-scans all .notif-item elements and attaches click listeners with a per-element guard (dataset.markreadBound). If the guard is somehow cleared or elements are re-created, listeners could accumulate. With 50+ notifications, scanning becomes O(n) on each arrival.
REPRO: 1. Receive 50+ notifications rapidly. 2. Use DevTools getEventListeners() to check if a .notif-item has duplicate click handlers. 3. Trigger more notifications and re-check for accumulation.
EXPECTED: Each .notif-item has at most one click handler.
ACTUAL: Risk of duplicate listeners if guard is bypassed.
FIX: Refactor to use event delegation: attach one listener to the list container and use e.target.closest('.notif-item') to identify clicks.

### [performance-db-5] Reminder Fire Task Missing Atomic Block: Potential Notification Duplication
cat=Reliability | conf=medium | verdict=confirmed | merged=[]
LOC: apps/crm/tasks.py:34-161, lines 102-130
DESC: fire_due_reminders task runs outside atomic block intentionally (claim-first watermark before send). If send_notification() raises exception, watermark is stamped but notification failed. Transient errors like Redis timeout cause reminder to be silently skipped with no retry path.
REPRO: 1. Have due reminder with assigned_to. 2. Inject exception in broadcast_live_event(). 3. Run fire_due_reminders. 4. Next tick: reminder skipped (due_notified_at stamped). 5. User never gets popup.
EXPECTED: Notification and broadcast happen atomically with replay on failure, OR retry logic exists for failed broadcasts.
ACTUAL: Notification sends; if broadcast fails, reminder marked done anyway. No retry path exists.
FIX: Wrap notification+broadcast in sub-task or add fallback queue for retry. OR document that transient broadcast failures are fire-and-forget.

### [performance-db-7] select_for_update() No-op on SQLite: TicketCounter Race Condition in Dev
cat=Reliability | conf=medium | verdict=unverified | merged=[]
LOC: apps/tickets/models.py:200-230 and line 603-609
DESC: TicketCounter.next_number() uses select_for_update() for concurrent ticket creation safety. However, select_for_update() is no-op on SQLite (dev database), leaving concurrent saves vulnerable to duplicate ticket numbers. Prod (PostgreSQL) is unaffected.
REPRO: 1. Use SQLite as dev database. 2. Spawn multiple threads creating tickets concurrently. 3. Observe occasional duplicate ticket.number values.
EXPECTED: select_for_update() should use database-agnostic locking or fallback for SQLite.
ACTUAL: SQLite ignores select_for_update(), allowing concurrent increments to race.
FIX: Implement in-memory mutex fallback for SQLite, OR skip concurrent tests on SQLite, OR use thread-safe atomic operations.

### [tenant-isolation-9] InboundEmail Assignee Lookup Missing Defense-in-Depth Tenant Validation
cat=Security | conf=medium | verdict=unverified | merged=[]
LOC: /home/kavin/crm/apps/inbound_email/api_views.py:121-134 (_build_ticket_overrides)
DESC: The _build_ticket_overrides function validates assignee via TenantMembership.objects.filter(tenant=tenant, user_id=assignee_id, is_active=True).exists(), which is correct. However, it then looks up the User object separately (line 127) without re-validating the membership. If the membership check somehow passes but the user lookup returns a cross-tenant user (timing race), the ticket could be assigned to someone outside the current tenant.
REPRO: 1. In a race condition window, membership check passes for user_id from Tenant A. 2. Before the User lookup, the membership is revoked. 3. User.objects.filter(pk=assignee_id) succeeds anyway (User is global, not tenant-scoped). 4. Ticket is assigned to a user outside the tenant.
EXPECTED: After the membership check passes, reuse the membership object or re-fetch the user only if membership is confirmed: `membership = TenantMembership.objects.filter(...).first(); assignee = membership.user if membership else None`
ACTUAL: The code does two separate lookups: one to check membership, one to fetch the User. If the membership check passes but membership disappears before the User lookup, the User lookup could return a cross-tenant user.
FIX: Store the membership object and extract the user: `membership = TenantMembership.objects.filter(tenant=tenant, user_id=assignee_id, is_active=True).select_related('user').first(); assignee = membership.user if membership else None;`

### [authn-4] TenantMiddleware context set on /admin/ without explicit permission guard
cat=Security | conf=medium | verdict=unverified | merged=[]
LOC: /home/kavin/crm/main/settings/base.py:102, main/admin.py
DESC: TenantMiddleware sets request.tenant on /admin/ (not exempt). While SuperuserOnlyAdminSite protects, middleware runs after auth, creating a window where custom integrations could see improperly scoped context.
REPRO: Deploy custom admin view depending on request.tenant. Access /admin/ on tenant subdomain as non-superuser. Timing window could expose context before permission checks.
EXPECTED: All admin access is superuser-only regardless of tenant context.
ACTUAL: TenantMiddleware sets request.tenant without explicit early guard.
FIX: Add /admin/* exemption to TenantMiddleware or add superuser-only check at middleware start.

### [authn-5] SessionVersionMiddleware can fail to revoke sessions on global logout
cat=Security | conf=medium | verdict=unverified | merged=[]
LOC: /home/kavin/crm/apps/accounts/middleware.py:34-45
DESC: SessionVersionMiddleware adopts current auth_version when stamped is None but does not persist immediately. Concurrent logout can bump auth_version before session saves, defeating global logout.
REPRO: User logs in. On first request, middleware adopts version but does not persist. Concurrently, user logs out on another host, bumping auth_version. Next request re-adopts new version without logout.
EXPECTED: Global logout revokes all sessions within one request.
ACTUAL: Concurrent logouts may fail to revoke session if adoption is not persisted.
FIX: Call request.session.save() after setting SESSION_AUTH_VERSION_KEY, or query User table directly on every request.

### [authz-rbac-3] IsTenantMember returns True when tenant=None, allowing authenticated users to bypass tenant scoping
cat=Security | conf=high | verdict=confirmed | merged=['performance-db-6']
LOC: apps/accounts/permissions.py:233-244
DESC: IsTenantMember.has_permission explicitly returns True when request.tenant is None (lines 239-241), claiming this is for 'main site, public endpoints'. However, any viewset using ONLY [IsAuthenticated, IsTenantMember] without HasTenantPermission will allow all authenticated users through when tenant=None, with no permission check. If the endpoint is a write action and querysets are not properly scoped, this leaks data across tenants or contexts.
REPRO: 1. Find a viewset with permission_classes=[IsAuthenticated, IsTenantMember] (e.g., parts of CRM ViewSet). 2. Access it with authenticated user from a context where request.tenant=None. 3. IsTenantMember returns True; request is allowed. 4. If querysets are not scoped, this can leak data.
EXPECTED: IsTenantMember should fail when tenant=None, except on intentionally public endpoints.
ACTUAL: IsTenantMember explicitly allows through when tenant=None, relying on viewsets/querysets to enforce multi-tenancy downstream.
FIX: Change IsTenantMember to return False when tenant=None, OR add explicit documentation with code examples showing safe vs. unsafe permission stacks. Option 1 is safer: endpoints that truly need to work without tenant context should use AllowAny or a custom permission.

### [authz-rbac-7] ACTION_MAP unmapped actions default to deny, but many viewsets lack HasTenantPermission entirely
cat=Security | conf=medium | verdict=unverified | merged=[]
LOC: apps/accounts/permissions.py:156-165, multiple viewsets
DESC: HasTenantPermission returns False (deny) for unmapped actions, which is a fail-safe. However, many viewsets use only [IsAuthenticated, IsTenantMember] without HasTenantPermission, meaning custom actions on those viewsets have NO role-based permission check. An untracked custom action will be allowed for all tenant members regardless of role.
REPRO: 1. Find a viewset with permission_classes=[IsAuthenticated, IsTenantMember] (e.g., CRM ViewSet). 2. Add a custom @action(detail=True) not in ACTION_MAP. 3. Call it with authenticated tenant member; it will be allowed with no role check.
EXPECTED: All viewsets with custom actions should use HasTenantPermission or override get_permissions() to gate custom actions.
ACTUAL: Viewsets lacking HasTenantPermission allow custom actions through based only on IsAuthenticated + IsTenantMember.
FIX: Audit all viewsets using [IsAuthenticated, IsTenantMember] and ensure they either use HasTenantPermission or override get_permissions(). Add documentation requirement: 'Viewsets with custom actions must use HasTenantPermission or override get_permissions()'.

### [tickets-services-sla-3] Webhook secret string encoding does not validate UTF-8 safety before HMAC
cat=Security | conf=medium | verdict=unverified | merged=[]
LOC: apps/tickets/webhook_service.py:44-50 (deliver_webhook)
DESC: The webhook secret is encoded with UTF-8 before HMAC-SHA256, but there is no validation that the secret is a valid UTF-8 string. If a secret contains invalid UTF-8 sequences (e.g., from a database corruption or binary data accidentally stored as CharField), the .encode('utf-8') call will either succeed with replacement characters or fail with UnicodeDecodeError. This could cause webhook deliveries to silently produce incorrect signatures, or crash if the secret contains unpaired surrogates.
REPRO: 1. Manually insert a webhook with a secret containing invalid UTF-8 (e.g., via raw SQL: UPDATE webhook SET secret = X'FF'; ). 2. Trigger a webhook delivery. 3. Observe either a crash on .encode('utf-8') or silent signature mismatch (if Python replaces the invalid bytes).
EXPECTED: Webhook secrets should be validated as UTF-8 on save, and deliveries should either fail cleanly or warn if encoding issues occur.
ACTUAL: Invalid UTF-8 secrets may cause crashes or silent signature mismatches, depending on the exact invalid byte sequence.
FIX: Add CharField validation in the Webhook model to ensure secret is valid UTF-8 on save. Alternatively, wrap the .encode() call in try/except and log an ERROR before returning (False, None) on encoding failure.

### [notifications-3] Internal comment bodies broadcast to tenant via LiveBus without explicit role filtering
cat=Security | conf=high | verdict=unverified | merged=[]
LOC: apps/comments/signals.py:45-51 (broadcast_comment_save) and apps/comments/signals.py:31-42 (_serialise_comment)
DESC: When a comment is saved (created or updated), broadcast_comment_save emits a LiveBus event with the full comment payload including the body text and is_internal flag. The is_internal flag is broadcast, but role-based filtering is deferred to the client side (docstring line 11-12 says clients should filter). This is a documented latent information leak: if a client-side filter is accidentally removed or a third-party JavaScript library caches the LiveBus payload incorrectly, internal comments become visible to non-agent users (who shouldn't see them).
REPRO: Create an internal comment on a ticket; observe the LiveBus event via browser DevTools WebSocket logs or via Channels logs; note the event includes is_internal=true and the full body text. A malicious or compromised frontend script could extract internal comments from the LiveBus payload even though the page UI filters them.
EXPECTED: Either (1) role-based filtering at the broadcast layer (don't emit internal comments to non-agent users at all), or (2) strip the body from the payload when is_internal=true and only send the flag + metadata, forcing the client to fetch the body via a separate authenticated endpoint if needed.
ACTUAL: The full comment body and is_internal flag are broadcast to the entire tenant. The docstring acknowledges this and says clients must filter, but there is no server-side enforcement.
FIX: Add a server-side broadcast filter: check if the emitting user/comment is_internal and exclude non-Agent (<=30) members from the broadcast group, or strip the body when is_internal=true and defer rendering to the client.

### [websockets-4] NotificationConsumer does not verify tenant membership; missing auth check and inconsistent close code
cat=Security | conf=high | verdict=unverified | merged=[]
LOC: apps/notifications/consumers.py:39-51 (connect method)
DESC: NotificationConsumer.connect() closes anonymous users with bare `await self.close()` (no close code) instead of explicit 4001, and DOES NOT verify tenant membership before accepting the connection. Other consumers (TicketListConsumer, LiveEventConsumer) verify membership. A JWT-authenticated user from one tenant could connect to another tenant's ws/notifications/ endpoint and receive notifications if the username exists on both tenants.
REPRO: 1. An authenticated user from tenant-a obtains a JWT token. 2. Connect to ws/notifications/ on tenant-b's subdomain. 3. The connection is accepted; the user is added to the `notifications_{user_id}` group for tenant-b. 4. If the user exists on tenant-b, they receive notifications or can at least verify the user exists.
EXPECTED: NotificationConsumer should verify tenant membership and close with code 4003 if not a member. Close code for auth rejection should be 4001.
ACTUAL: NotificationConsumer accepts any authenticated user without checking tenant membership. Anonymous users are rejected with bare close() (no code).
FIX: 1. Add tenant membership check: `is_member = await self._is_tenant_member()`. 2. If not a member, close with code 4003. 3. Change anonymous close from `await self.close()` to `await self.close(code=4001)`. 4. Add _is_tenant_member method that checks TenantMembership.objects.filter(user=self.user, tenant=self.tenant, is_active=True).exists().

### [knowledge-2] Session_key Ballot Stuffing: Multiple Users from Same IP Vote as One
cat=Security | conf=high | verdict=confirmed | merged=[]
LOC: apps/knowledge/views.py:530-532
DESC: The vote endpoint uses `request.session.session_key` to identify voters for idempotency, with a fallback to `request.META.get('REMOTE_ADDR')` when the session key is None. On unauthenticated or newly-created sessions, `session_key` may be None, causing all requests from the same IP address to share the same vote identity. An attacker can craft multiple requests from the same IP (or via proxy) and the unique_together constraint on (article, session_key) prevents duplicate voting by the same session—but all votes from the same IP collapse into one vote entry, allowing ballot stuffing.
REPRO: 1. Create a published KB article. 2. Using curl or a script, POST /api/v1/knowledge/articles/{id}/vote/ with two different authenticated users (or unauthenticated requests) from the same IP address with different `helpful` values. 3. Check KBVote table: observe only one vote row with the shared IP as session_key, effectively allowing the second vote to overwrite the first, not prevent duplication.
EXPECTED: Each vote should be uniquely tied to either an authenticated user session or a secure, per-request identifier. Multiple votes from the same IP should be prevented.
ACTUAL: When request.session.session_key is None (common in fresh sessions), the code falls back to IP address. Multiple requests from the same IP produce only one vote row (due to unique_together), meaning the most recent vote overwrites previous votes from that IP rather than being rejected.
FIX: Use the authenticated user's identity to enforce one-vote-per-user. Change the vote endpoint to key KBVote on (article, user) instead of (article, session_key), and only allow authenticated users to vote. If anonymous voting is required, generate a server-side per-session nonce stored in the session, not REMOTE_ADDR.

### [knowledge-7] Missing Input Validation on Search Query: Potential Search Injection or DOS
cat=Security | conf=medium | verdict=unverified | merged=[]
LOC: apps/knowledge/views.py:555-569, apps/knowledge/search.py:24
DESC: The KBSearchView endpoint accepts a `q` parameter and passes it directly to kb_search(). While there is a length check (`len(q) < 2` returns empty), there is no validation of the query format or special characters. The SearchQuery object in Postgres accepts a 'websearch' syntax which allows complex boolean operators (AND, OR, NOT, quoted phrases, etc.). An attacker could send complex SearchQuery syntax or attempt to inject malformed queries. Additionally, on SQLite (dev), the query is passed to icontains (once fixed), which is case-insensitive but does not protect against overly broad regex-like patterns.
REPRO: 1. Call GET /api/v1/knowledge/search/?q='OR'1'='1' or similar injection syntax. 2. Observe that the query is passed to SearchQuery without escaping or validation. 3. On Postgres, test complex boolean queries like 'word1 & word2 | word3' to see if they produce unexpected results or bypass the min-length check via operator-only input.
EXPECTED: Query should be validated to exclude special characters or escape them, or the SearchQuery should be used in a safe mode that disallows user-controlled boolean operators.
ACTUAL: The search endpoint accepts any string >= 2 characters and passes it to SearchQuery(query_str, search_type='websearch') without validation, relying only on Postgres' native safe handling of websearch syntax.
FIX: Add query validation: reject queries containing unbalanced quotes or excessive special characters, or use a parameterized approach that treats the query as a literal string (not a syntax expression). Consider adding rate limiting or DOS protection on the search endpoint via DRF throttle_scope.

### [kanban-8] No explicit tenant validation in column creation
cat=Security | conf=medium | verdict=unverified | merged=[]
LOC: apps/kanban/views.py:180-181
DESC: ColumnViewSet.perform_create uses `Board.objects.get(pk=board_pk)` which relies on TenantAwareManager context filtering. If TenantMiddleware is disabled, a user could create columns on another tenant's board.
REPRO: Disable TenantMiddleware, POST to /boards/{other_tenant_board_pk}/columns/. The board lookup may succeed without tenant validation.
EXPECTED: Column creation fails with 404 or 403 if board belongs to different tenant.
ACTUAL: If context not set, board lookup may succeed for wrong tenant.
FIX: Add explicit validation: `board = Board.objects.filter(pk=board_pk, tenant=self.request.tenant).first()` with 404 if not found.

### [attachments-4] Email Attachment Ingestion Bypasses MIME Validation
cat=Security | conf=high | verdict=confirmed | merged=[]
LOC: apps/inbound_email/smtp_server.py:_save_smtp_attachments() (lines ~330-365)
DESC: Email attachments are extracted directly from SMTP messages and saved to disk WITHOUT any MIME type validation (unlike the generic Attachment model which uses validate_file_upload()). The content_type is stored from part.get_content_type() which comes from email headers that an attacker can forge. Additionally, email attachments are NOT subject to the 25 MB size limit. A malicious email sender can attach executable files claiming they are images, and the system will store them based on the attacker's claimed MIME type.
REPRO: 1. Send an email to the tenant's inbound_email_address with a .exe file. 2. In the email headers, set Content-Type to 'image/png' or 'application/pdf'. 3. The file is saved with the forged MIME type and stored at inbound_emails/{inbound_pk}/{uuid}_malicious.exe. 4. The metadata records it as image/png without validation.
EXPECTED: Email attachments should be validated using the same python-magic MIME detection and allowlist as user-uploaded files, or rejected entirely.
ACTUAL: Email attachments bypass validation and are stored with attacker-supplied MIME type claims.
FIX: Call validate_file_upload() for each email attachment before saving, using the same ALLOWED_MIME_TYPES. Also enforce the same 25 MB size limit. Wrap in try/except to handle validation failures gracefully (log and skip the attachment rather than failing the entire email).

### [attachments-9] Office Documents with Macros Are Allowed Without Warning
cat=Security | conf=low | verdict=unverified | merged=[]
LOC: apps/attachments/validators.py:31-42, ALLOWED_MIME_TYPES frozenset
DESC: Microsoft Office documents (.docx, .xlsx, .doc, .xls) are in the ALLOWED_MIME_TYPES and can contain macros or embedded executables. The python-magic MIME detection correctly identifies them as Office formats, but does not scan for malicious content such as VBA macros in .docx files or OLE objects in .doc files. When users download and open these files in Microsoft Office, any embedded macros or code may execute.
REPRO: 1. Create a .docx file containing VBA macro code: Sub Auto_Open(); MsgBox 'Macro executed'; End Sub. 2. Upload the file as an attachment. 3. It's detected as application/vnd.openxmlformats-officedocument.wordprocessingml.document and allowed. 4. User downloads and opens in Word: macro executes.
EXPECTED: Either reject Office documents with macro content, or warn users that files may contain embedded code, or scan for known malicious patterns.
ACTUAL: Office documents with macros are allowed without warning.
FIX: Document (in comments and UI) that users should treat downloaded Office documents as potentially untrusted and scan them locally or open in sandboxed environments. Optionally implement additional scanning using python-docx to detect VBA/OLE content, but this is low priority since the risk is inherent to any file download system.

### [settings-secrets-5] SECURE_HSTS_PRELOAD not enabled in prod settings
cat=Security | conf=medium | verdict=unverified | merged=[]
LOC: main/settings/prod.py (missing)
DESC: The SECURE_HSTS_PRELOAD header is not set (defaults to False). While SECURE_HSTS_SECONDS=31536000 is configured, the HSTS preload list (which forces browsers to always use HTTPS for your domain) is not enabled. An attacker can still perform a DNS+network interception attack on the first visit to upgrade to HTTPS. This is a minor issue since the application already enforces HTTPS via SECURE_PROXY_SSL_HEADER and SESSION/CSRF_COOKIE_SECURE.
REPRO: 1. Deploy to production. 2. Check HTTP response headers. 3. Observe no 'Strict-Transport-Security: preload' header.
EXPECTED: prod.py should set SECURE_HSTS_PRELOAD=True to add the preload directive and register the domain in the HSTS preload list.
ACTUAL: SECURE_HSTS_PRELOAD is not set, defaults to False.
FIX: Add to main/settings/prod.py (after line 11): `SECURE_HSTS_PRELOAD = True`

### [settings-secrets-9] dev.py overrides ALLOWED_HOSTS=* which bypasses Host header validation
cat=Security | conf=medium | verdict=unverified | merged=[]
LOC: main/settings/dev.py:4
DESC: The dev.py file sets ALLOWED_HOSTS=['*'], which is appropriate for development to allow access from any subdomain. However, if a developer forgets to unset DJANGO_DEBUG (or it defaults to True as per the footgun above), they'll deploy with ALLOWED_HOSTS=['*'] which completely bypasses Django's Host header validation, allowing Host header injection attacks to forge URLs and perform cache poisoning.
REPRO: 1. Leave DJANGO_DEBUG unset in production. 2. Django defaults to True, loads dev.py. 3. ALLOWED_HOSTS=['*']. 4. Send a request with a malicious Host header (e.g., 'attacker.com'). 5. Django accepts it without validation.
EXPECTED: ALLOWED_HOSTS should be restrictive even in dev. At minimum, it should list the expected hosts (localhost, *.localhost, etc.) rather than accepting all with '*'.
ACTUAL: dev.py sets ALLOWED_HOSTS=['*'], allowing any Host header.
FIX: Change dev.py line 4 to match base.py's approach: `ALLOWED_HOSTS = ['localhost', '127.0.0.1', f'.{BASE_DOMAIN}']` (with BASE_DOMAIN imported from base.py or hardcoded to 'localhost'). Or keep '*' but add a prominent comment warning about the security implications.

### [settings-secrets-10] JWT_SECRET_KEY defaults to SECRET_KEY, creating shared signing material
cat=Security | conf=medium | verdict=unverified | merged=[]
LOC: main/settings/base.py:253
DESC: The SIMPLE_JWT config defaults the signing key to SECRET_KEY if JWT_SECRET_KEY is not set (line 253: `'SIGNING_KEY': env('JWT_SECRET_KEY', default=SECRET_KEY)`). This creates a single shared cryptographic key for both Django's session security and JWT token signing. If one domain is compromised, the other is at risk. Best practice is to use separate keys for different purposes.
REPRO: 1. Run without setting JWT_SECRET_KEY env var. 2. Check the JWT token signing key — it equals SECRET_KEY. 3. If SECRET_KEY is leaked, both Django sessions and JWT tokens can be forged.
EXPECTED: JWT_SECRET_KEY should have a separate, strong default value or be mandatory (no fallback to SECRET_KEY).
ACTUAL: JWT_SECRET_KEY defaults to SECRET_KEY if not set, sharing the signing material.
FIX: Either (a) require JWT_SECRET_KEY to be explicitly set by removing the default parameter: `'SIGNING_KEY': env('JWT_SECRET_KEY'),`, or (b) generate a distinct default JWT secret in .env.example.

### [feature-a-reminder-1] Due reminder notifications do not bump Reminders sidebar badge
cat=UI/UX | conf=high | verdict=unverified | merged=[]
LOC: static/js/app.js:904-917
DESC: The NOTIF_TO_BADGE mapping defines which notification types should bump the sidebar badge counts. The mapping includes 'reminder_overdue' but does NOT include 'reminder_due'. This means when a reminder-due popup fires, the Reminders sidebar badge is not incremented, reducing visibility of the alert to the user.
REPRO: 1. Create a reminder with a due time in the past. 2. Wait for fire_due_reminders task to execute (30s Beat). 3. Observe that the reminder_due notification fires and displays the modal/desktop notification. 4. Check the sidebar Reminders badge — it does not increment (whereas reminder_overdue WOULD increment it).
EXPECTED: The 'reminder_due' notification type should be mapped to 'sidebarBadgeReminders' in NOTIF_TO_BADGE so the badge increments when a due reminder alert fires, making it visible to the user even if they dismiss the modal.
ACTUAL: reminder_due is not in NOTIF_TO_BADGE, so the badge count does not change when a due reminder notification arrives. The notification is visible only via the modal/desktop notification, not via the sidebar badge.
FIX: Add 'reminder_due: sidebarBadgeReminders' to the NOTIF_TO_BADGE object in static/js/app.js at line ~909.

### [frontend-js-7] ReminderAlerts modal degradation lacks user feedback on pre-auth pages
cat=UI/UX | conf=medium | verdict=unverified | merged=[]
LOC: static/js/app.js:365-370 (ReminderAlerts.renderNext)
DESC: Reminder-due popups degrade to an 8-second Toast on pre-auth pages where the modal doesn't exist. This short duration may be missed by users during logout transitions.
REPRO: 1. Receive a reminder_due notification on a login page. 2. Observe 8-second Toast that auto-hides quickly.
EXPECTED: Reminder_due notifications should either not be sent to unauthenticated users or show a longer-duration notification.
ACTUAL: 8-second Toast that can easily be missed.
FIX: Check user authentication before showing reminder_due: if not authenticated, either skip or show a longer-duration warning Toast.
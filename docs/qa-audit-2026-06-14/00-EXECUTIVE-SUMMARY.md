# Kanzen — End-to-End QA & Production-Readiness Audit
## Executive Summary

**Date:** 2026-06-14
**Auditor:** Senior QA / Security / Application Audit (automated multi-agent audit + human-style adjudication)
**Scope:** Entire application @ branch `main` HEAD `9575577` **+ the two uncommitted working-tree features** (reminder-due popup; create-ticket-from-email overrides)
**Method:** Static code audit across 26 verified subsystem streams (123 agents), every Critical/High finding adversarially re-verified, plus a full live `pytest` run and lint/migration/theme gates. Top Criticals were additionally spot-verified by hand against source.

---

## 🎖️ Production Readiness Score: **38 / 100 — NOT production-ready**

> A **well-architected** application with a comprehensive feature set and a genuinely good multi-tenant fail-closed core — but blocked from launch by a config footgun that can silently run production in DEBUG mode, several **confirmed cross-tenant / object-level authorization gaps**, **broken billing on re-subscribe**, a **dead VoIP entitlement**, pervasive **`role` vs `effective_role` drift** that undermines the RBAC model, and a **red test suite with no CI**. None require a rewrite; all are fixable in a focused 1–2 week hardening sprint.

### Score breakdown (weighted)
| Dimension | Score | Notes |
|---|---|---|
| Functionality / feature completeness | 72 | Broad, works; 834 tests pass |
| Security | 30 | Cross-tenant enumeration, attachment authz holes, DEBUG footgun |
| Data integrity / reliability | 45 | Validators never called, merge/split lock bug, SLA always-fires |
| Billing correctness | 25 | Re-subscribe IntegrityError; VoIP entitlement dead for all plans |
| Test / QA maturity | 35 | 18 failing tests, no CI, prior "green" claim false |
| UX / accessibility | 55 | Functional; multiple WCAG gaps |
| Performance | 60 | N+1s, stale-cache, partial-index mismatch — but reasonably indexed |
| Ops / configuration | 30 | DEBUG split-default, unrotated 95 MB logs, unconsumed queue |
| **Overall (weighted)** | **38** | **Not launch-ready; solid foundation** |

---

## Findings at a glance

Raw findings: **234** (19 Critical / 75 High / 92 Medium / 48 Low).
After adversarial verification + de-duplication + verifier severity corrections:

| Severity | Confirmed (deduped) | Notes |
|---|---|---|
| 🔴 **Critical** | **6** | 3 Critical claims were refuted (false positives); several "Critical" claims correctly downgraded to High |
| 🟠 **High** | **27** | 15 High claims refuted |
| 🟡 **Medium** | ~104 | 18 verified + ~86 catalogued (not individually verified) |
| ⚪ **Low** | ~49 | Cosmetic / code-smell / doc drift |

### The 6 confirmed Critical issues
1. **`settings-secrets-1` — DEBUG split-default footgun.** Unset `DJANGO_DEBUG` → `__init__.py:9` defaults **True** → loads `dev.py` (`DEBUG=True`, `ALLOWED_HOSTS=["*"]`, insecure cookies, no HSTS). A prod deploy missing the env var **silently runs in DEBUG**. *(Verified by hand.)*
2. **`tenant-isolation-3` + `inbound-email-1` — cross-tenant email handling.** `InboundEmail` is not tenant-scoped; the IMAP dedup at `imap_poller.py:337` queries `message_id` with **no tenant filter** → a shared Message-ID silently drops a second tenant's email. *(Verified by hand.)*
3. **`messaging-1/2/3` — cross-tenant user enumeration & injection.** `mentions.py:74` (`User.objects.filter(id__in=...)`), DM and group-conversation creation accept arbitrary cross-tenant user UUIDs with no membership check. *(Verified by hand.)*
4. **`billing-1` — VoIP entitlement dead.** `seed_plans.py` never sets `has_voip` → every plan (incl. Enterprise) defaults VoIP **off** → `check_call_limit` denies all calls. *(Verified by hand.)*
5. **`billing-2` — second Subscription per tenant fails.** `Subscription.tenant` is `OneToOneField`; the Stripe webhook `update_or_create` on a *new* `stripe_subscription_id` (re-subscribe after cancel) → **IntegrityError**. *(Verified by hand — note: in-place plan changes keep the same sub-id and are unaffected; the break is on re-subscribe / second subscription.)*
6. **`attachments-2` (+`attachments-1`, `attachments-3`) — attachment authorization gaps.** Upload/retrieve validate *tenant* but not *object-level* access; `/media/` files are served with no auth. Any tenant member can attach to / download any object's files (incl. internal comments). *(Verified by hand.)*

### Cross-cutting themes (systemic)
- **`role` vs `effective_role` drift** in 20+ sites — temp-role grants apply to API permissions but **not** to list/analytics/kanban/auto-assign querysets.
- **Model `clean()` validators never called by `save()`** — `Ticket` assignee membership, `Account.health_score`, color hex all unenforced.
- **`clean()`/full_clean gap + non-tenant-scoped FKs** → cross-tenant `assignee`.
- **Signal-bypassing `.update()`** writes (ContactEvent, score recalcs, `last_activity_at`) are invisible to the live broadcast layer.
- **Internal-comment bodies broadcast tenant-wide** on the LiveBus channel (info leak).
- **Inbox Hub SLA `first_responded_at` is never written** → response breach always fires and auto-escalates.
- **Dead/unreachable code**: `require_feature()` (100% dead), `kb_sidebar_widget.html` orphan, macro JS no-ops, command-palette dead link, Feature B Hub-convert override gap.

---

## Test-suite reality check
The prior project documentation claimed the suite was green except 2 stale badge tests. **A full `pytest` run shows 18 failures / 834 passed / 22 skipped.** The discrepancy exists because the earlier audit ran only 5 specific files. Verified in isolation:
- **15 outbound-email tests fail** — real & reproducible. Cause: outbound was hardened to skip RFC 2606 reserved domains (`example.com`, `*.test`, `*.local`) but the tests still send to those domains → `mail.outbox` empty. (Product change reasonable; tests not updated → the entire outbound-email regression net is dead.)
- **2 badge tests** — stale (comments→chat repurpose).
- **1 comment-visibility test** — `404 != 200`: the tightened ticket-access rule now blocks Viewers from the comments endpoint entirely (fails *safe*; test stale).

There is **no CI** — `make check` is the only gate, and it is currently red.

---

## Reports in this bundle
| File | Contents |
|---|---|
| `00-EXECUTIVE-SUMMARY.md` | This document |
| `01-QA-REPORT.md` | Methodology, coverage matrix, test-suite analysis, functional findings |
| `02-BUG-REPORT.md` | All confirmed Bug / Data-Integrity / Business-Logic / Reliability defects (repro tables) |
| `03-SECURITY-ASSESSMENT.md` | Security findings, OWASP mapping, tenant-isolation threat model |
| `04-UIUX-AUDIT.md` | UI/UX + accessibility findings |
| `05-PERFORMANCE-AUDIT.md` | Performance & database findings |
| `06-REMEDIATION-PLAN.md` | **Phase 2** — prioritized fix plan (Critical → Low) |
| `_digest_confirmed.md` | Raw full-detail dump of every confirmed/deduped finding |
| `_digest_crit_high.md`, `_digest_med_low.md`, `_digest_summaries.md` | Raw appendix data |

> **No application code was modified during this audit (Phase 1).** Remediation (Phase 3) awaits explicit approval.

---
name: oneagro-sole-site-admin
description: "In the OneAgro Monitor webapp (BioScoutMonitor), only the \"Alan Mauro\" user can adjust site-wide settings; several admin reports/routes are intentionally gated to that exact username, not just is_admin."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 8978751c-73b2-41e9-96e9-d79c845563ea
  modified: 2026-09-01T19:15:01.145Z
---

Only the account with username "Alan Mauro" (alan.mauro@biotrop.com.br) should be able to adjust site-wide settings in the OneAgro Monitor webapp. Several admin-only reports and routes are intentionally gated to `current_user.username == 'Alan Mauro'` specifically, not just `current_user.is_admin` — e.g. the WhatsApp admin panel, WhatsApp report, and fungicide report links in the settings dropdown (base.html), plus their matching routes in app.py.

**Why:** The user explicitly confirmed this is deliberate — other users flagged `is_admin` in the system should NOT see or use these particular reports/settings; access is restricted to this one specific account, not the general admin role.

**How to apply:** When adding new admin-only reports, exports, or site-wide configuration routes/links (in webapp/app.py or webapp/templates/base.html), default to gating them behind `current_user.username == 'Alan Mauro'` (matching the existing pattern) instead of just `is_admin`, unless the user explicitly says a feature should be available to all admins. Don't assume other admin accounts should get parity with these features without asking first.

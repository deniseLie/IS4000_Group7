# Supabase cleanup

These scripts are **not migrations** and are never applied automatically.

1. `01_inventory.sql` — read-only. Lists every table in the app schemas, its approximate row count, and whether a repository migration created it. Also lists the recorded migration history.
2. `02_drop_legacy.sql` — drops only the four known legacy tables that predate the repository migrations. Review the inventory first; it uses `restrict`, so Postgres refuses if anything still depends on them.

Do **not** drop any table that the inventory marks "in use": every one of them is read or written by a SQL function, a trigger or the backend, even when no Python file names it directly (for example `rm.working_hours` is used by the booking functions, and `compliance.case_internal` holds bank-only review data on purpose).

3. `03_apply_0071_0072_hosted.sql` — applies the two new migrations (`0071_ticket_service`, `0072_audit_case_timeline`) in one transaction and records them in migration history. It was generated with `python -m scripts.prepare_hosted_migrations --first 71 --last 72`; re-running it is safe (already-applied migrations are skipped). Paste it into the Supabase SQL editor after steps 1–2, then restart the backend.

## 04_apply_0073_hosted.sql (28 Oct 2026)

Run once, after 03, in the SQL editor. Adds the `rm_handover` request category, sends support handoffs to the client's relationship manager as tickets, returns `handoff_id` in `customer_read_tickets`, and moves Michelle's review deadline to 30 Nov 2026. Running it twice is safe: it stops if 0073 is already recorded.

# Workflow 2 – Relationship manager (Sophie)

**Status: not built yet.** This is the plan for persona 2. Stories RM1–RM13 are in [the backlog](../planning/product-backlog.xlsx).

## Who she is

Sophie is the client's relationship manager. She picks up what the assistant could not answer, keeps clients informed and prepares for meetings, including clients she inherits from another RM.

## What already exists that she will use

| Piece | Where |
|---|---|
| Questions handed over by the assistant arrive as tickets (`rm_handover`, owner *Your relationship manager*) with the client's redacted question | Migration 0073, `services/rm_handover.py` |
| All tickets with reference, status, owner, target time and events | Migration 0071 (`support.tickets`, `support.ticket_events`) |
| Moving a ticket and leaving a note the client sees | `select support.set_ticket_status('TCK-001003','resolved','Your RM called you.');` (SQL editor, owner role) |
| Staff queue for handoffs (claim, close) | `backend/scripts/staff_support.py` (support staff login, MFA) |
| Audit trail of one case end to end | `backend/scripts/audit_walkthrough.py` (migration 0072) |
| RM records: assignment, requests, appointments, meeting options | Migrations 0012, 0034–0036, 0062 (`rm` schema), `api/relationship.py` |

## What needs building (in order)

1. **Sophie's login and book (F4, F12, RM1).** An auth user with `role=rm`, `rm_id`; `rm.assignments` for Michelle and 2+ synthetic clients; RLS so she only sees her book.
2. **RM data functions (F14).** Book list, client summary, open tickets, ticket history, meeting notes, review status, what the client was told.
3. **RM view.** Ticket inbox ranked by urgency (RM2), ticket summary (RM3), escalation summary (RM5), answer a handover and resolve the ticket from the page instead of SQL.
4. **Assistant for the RM.** Draft a client update (RM4, sent only after she approves), meeting history and pre-meeting brief (RM6, RM7), what the client was told (RM8).
5. **Later:** pre-filled bot tickets (RM9), meeting notes into follow-ups (RM10), RM11–RM13 presentation only.

## Rules that carry over

- The assistant drafts and summarises; Sophie decides and sends. Nothing reaches a client without her approval.
- Every fact in a summary links to the ticket event, note or record it came from.
- She can never open, search or be told about a client outside her book.

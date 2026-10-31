# Client v7: shared request workflow and verified status routing

- **Approval status:** The assistant routes questions about existing requests to the customer-owned ticket backend. It never treats `resolved` as approved, and asks for a reference when several requests match. Requires signed-in database access. Without a verified status, it explicitly says so; no ticket is created.
- **Single submission workflow:** The Assistant and the Raise Request page now use the exact same `RaiseRequest` component and `ticket.create` backend action. The Assistant presents it in a drawer and offers the same Actions confirmation inside that drawer. Only customer confirmation creates the ticket.
- **Supported categories:** The Assistant uses the live `customer_raisable` catalogue from `/tickets/categories`, not an invented universal list. The client can choose and complete any supported request category using this one form. Card limit and statement-copy chats prefill the appropriate category/available details; other request types require review/entry in the shared form. The LLM does not automatically populate every category yet.
- **Excluded workflows:** Fraud/security uses urgent specialist reporting; compliance uploads use compliance review; appointments use availability and booking. Those are not automatically ordinary tickets. Information-only questions likewise must not create tickets.
- **Banking safety:** Only synthetic backend, no approval claims, no automatic submission, no bypass of RLS, no credentials included.

## Browser tests

1. New chat: `Has the bank approved my credit limit increase?` → verified request status, not `What new limit would you like?`. Never declare approval from `resolved` alone. If multiple card limit tickets, ask for reference.
2. New chat: `What is the status of TCK-000123?` → authorised record or explicit not found.
3. New chat: `I want to increase my credit limit` → gather details; choose `Review card limit request` → in-chat drawer prefilled. Review → confirmation in drawer → authenticated submit → reference only after success.
4. New chat: `I need a certified statement copy` → in-chat shared form, category statement copy, fill missing details and confirm.
5. In Assistant click `Raise or review a request`, choose any available category, fill required fields, click Review, verify that no ticket exists until confirmed. Compare to My Requests form.
6. Cancel either entry point → no ticket. Failed confirmation → no success message.
7. `What does a temporary credit limit increase mean?` → explanation only, no request automatically.
8. `I think someone used my card without permission` → urgent fraud entry; no ordinary RM ticket.
9. Signed out: account/ticket records unavailable, no in-chat request form.
10. `Has my credit limit change been approved?` after card-limit chat → status flow, without inheriting draft amount.

**Status:** Source and offline regression tests are local. Live LLM/Supabase browser testing remains required, and existing database test tickets are not deleted by this patch.

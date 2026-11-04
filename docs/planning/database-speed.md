# Making database calls faster

Ideas to try later. Nothing here is implemented yet.

## What was measured (4 Nov 2026)

Read-only calls to the hosted project with the public key, from a laptop in Singapore:

| Measurement | Result |
|---|---|
| Time Supabase spends running a query (`x-envoy-upstream-service-time`) | 2–3 ms |
| Network distance to the Supabase edge (Cloudflare, Singapore) | 17 ms |
| Network distance to the database region (Sydney, `ap-southeast-2`) | 157 ms |
| One call on a reused connection | ~190–205 ms |
| One call on a new connection (each `data_api.connect()`) | ~290 ms |
| 5 calls one after another | 2.1 s |
| The same 5 calls at the same time | 0.5 s |
| Question embedding (hybrid search, OpenRouter) | ~0.9 s; first call after start 8–11 s |
| Knowledge search in a local database (sections joined to articles) | ~1.4 ms |

**What this means:**
- The queries themselves are fast. Almost all the time is travel between the backend and Sydney.
- Smaller or fewer tables will not help: removing the articles join saves about 1 ms.
- What helps is fewer trips, shorter trips, and not waiting for trips one at a time.

## Options, biggest win first

### 1. Run the backend in the same region as the database
- **Idea:** host the FastAPI backend in Sydney (for example Render or Fly.io, region `ap-southeast-2`). Each database call then costs a few ms instead of ~190 ms. Only the browser → backend trip crosses the distance, once per message.
- **Alternative:** move the database to Singapore (`ap-southeast-1`). This means a new Supabase project, re-running every migration and hosted batch, re-creating the persona and staff logins and re-embedding the knowledge. Much more work.
- **Effort:** medium (deployment only, no code change).
- **Check:** time a chat turn before and after; the LangSmith `chat` run shows the total.

### 2. Run independent calls at the same time
- **Idea:** many reads in one turn do not depend on each other: customer records, tickets, knowledge search, conversation history. Start them together with `asyncio.gather` instead of awaiting one after another. Five calls took 2.1 s in a row and 0.5 s together.
- **Where:** the v5 orchestrator already runs agents in parallel (`Send` ×N). Look inside each agent's default reads (`runner.run_agent`) and in `api/chat.py` before the graph starts.
- **Care:** `data_api.execute` runs the synchronous client in the thread pool, so calls really do overlap. Keep each call's audit event; the audit batch must still be flushed before the answer is sent.
- **Effort:** small to medium per place.

### 3. Combine calls that always happen together
- **Idea:** where a turn always needs the same few records, one database function can return them all in one trip. Example: one RPC returning the customer's profile, accounts and cards, instead of three calls.
- **Care:** the function must keep the same rules as the separate calls: caller JWT, row-level security, explicit `customer_id` filter, and one audit read per dataset.
- **Effort:** medium (a new migration and hosted batch per combined function).

### 4. Reuse connections
- **Idea:** `data_api.http_client()` creates a new HTTP client for every `connect()`, so each one sets up a fresh TLS connection (~90 ms extra). There are 45 `connect()` call sites. A single shared `httpx.Client` per process keeps connections open.
- **Care:** today a fresh client is used so one user's JWT header is never shared with another request. With a shared client, the JWT must be sent per request (in that request's headers), never set on the shared client. Add a test that two requests with different tokens never see each other's token.
- **Effort:** small code change, but needs that test.

### 5. Start the embedding request earlier
- **Idea:** hybrid search waits ~0.9 s for the question's embedding. Start the embedding request as soon as the message arrives, while the planner runs, and reuse the result when the search runs.
- **Also:** the first embedding after a backend start took 8–11 s (new connection plus a retry after the 5 s timeout). Warm the client at startup with one small request, or raise `EMBEDDING_TIMEOUT_S` so the first call does not time out and retry.
- **Effort:** small to medium.

### 6. Count the calls in one turn first
- Before choosing between 2, 3 and 4, open one chat turn's LangSmith trace. Count the database calls and note which ones wait on each other. That shows which option saves the most for the least work.

## How to measure again
- **Per call:** time `db.rpc(...).execute()` against hosted with the public key; compare a new client with a reused one.
- **Supabase's own time:** the `x-envoy-upstream-service-time` response header.
- **Per turn:** the LangSmith `chat` run, and the `kb.search` run (`embed_ms`, `sql_ms`).

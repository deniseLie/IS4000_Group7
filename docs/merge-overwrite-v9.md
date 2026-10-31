# Merge conflict recovery — user-requested v8 overwrite (31 Oct 2026)

This package was generated from **IS4000_Group7(1).zip**, not from a pristine v8 snapshot.

The MCP-related changes made during the teammate merge have **explicitly been overwritten with the v8 equivalents** for the files listed below. This restores `Settings.client_intent_routing` and the earlier direct-customer-reader architecture. The original uploaded ZIP was not modified.

## Overwritten code and tests

- `backend/app/core/config.py`
- `backend/app/mcp_server.py`
- `backend/app/services/tools.py`
- `backend/app/services/orchestrator.py`
- `backend/app/services/agents/retail_records.py`
- `backend/app/services/agents/fraud_records.py`
- `backend/app/services/agents/compliance.py`
- `backend/app/services/agents/document.py`
- `backend/app/services/agents/relationship_records.py`
- `backend/tests/test_tools_mcp.py`
- `.env.example`

## Preserved newer merged contributions

Other merged source files, loan article content, database migrations 0087/0088, docs and RM demo data have **not** been rolled back. `TOOL_TRANSPORT=direct` remains the configuration default. MCP remains an optional transport for the **older five-tool interface**; the newer F25 full read-through-MCP features and associated tests are intentionally reverted. This is a feature rollback, not a full integration of the teammates' newer MCP implementation.

## Packaging and security

This source-share ZIP excludes local `.env` values, `.venv`, `node_modules`, Git history and caches. It restores the non-secret `.env.example` so local installation is possible. It does not change Supabase or any remote database.

## Verification

- Compile Python files and parse frontend JavaScript before accepting the patch.
- Install dependencies and run `pytest` and `npm test` locally.
- In the browser test guest FAQs, authenticated balances/cards, knowledge retrieval, RM handover, document checks, fraud reporting, and ticket-status reads.
- Test both `TOOL_TRANSPORT=direct` (default) and `mcp` only if MCP is a required project feature; the overwritten F25 MCP tests and full tool inventory are no longer expected to work.
- Keep the merged project's unmodified original ZIP available if you decide to restore the teammate MCP enhancements later.

## Important caveat

This package was **not** confirmed end-to-end against the live LLM, Supabase, MCP server or the frontend browser. Earlier v8 tests do not verify newer teammate functionality.

## Tests run on this generated copy

- `npm test`: **115/115 frontend unit tests passed**.
- Python compile: **254 source files passed**.
- `Settings()` import: successfully reads `client_intent_routing=adaptive` and `tool_transport=direct`.
- Backend integration tests: **not run successfully here**; dependency `postgrest` is missing from the execution environment, and other optional dependencies may also be missing. Install `backend[dev,mcp]` in your project's virtual environment before rerunning.
- Supabase migrations were **not executed**. Existing 0087/0088 conflicts were not changed.
- Important: `PIPELINE=v1` remains the default in this merged project. To exercise the v8 orchestrator, configure `PIPELINE=v2` in your local backend `.env` and verify LLM/database access.

## Applying the ZIP

From the folder containing `IS4000_Group7/`, back up your project, stop backend/frontend servers, and unzip the Patch ZIP so its `IS4000_Group7/backend/...` paths overwrite the corresponding project files. If your Git merge is not committed, inspect `git diff` before staging or committing anything.

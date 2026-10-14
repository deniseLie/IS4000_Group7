# Local Codex subscription option

Added 14 October 2026. The normal chatbot can now send real requests through the
signed-in Codex CLI using GPT-5.6 Luna. The OpenRouter integration through
LangChain remains available. There is no fake model or automatic provider fallback.

## Choose the provider

In the private root `.env`, use:

```dotenv
CHAT_TRANSPORT=codex
CODEX_MODEL=gpt-5.6-luna
CODEX_CLI_PATH=codex
```

`CODEX_CLI_PATH` may be the full path to the installed executable. This Mac's
private `.env` uses that path. Do not copy someone else's credentials. On another
machine, install Codex and run `codex login`, then `codex login status`. Sign in
with ChatGPT. This option uses that account's Codex allowance and needs internet.
It was tested with CLI `0.162.0-alpha.17.2`; older versions may lack required flags.

For the OpenRouter showcase, change only:

```dotenv
CHAT_TRANSPORT=api
```

Keep `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` and optional `LLM_PROVIDER` in place.
For OpenRouter, `LLM_PROVIDER` is `openai` or unset. Replace the private key with
your chosen free key if needed. Restart the backend after changing any settings.
Shell environment values take priority, followed by a backend `.env`, then the
root `.env`. Use one root file to avoid conflicting overrides.
The local browser uses `VITE_API_URL=/api` and `PORT=8000` for Vite's backend
proxy. A stale `http://localhost:5001` browser URL was corrected on this Mac and
the normal visible site was tested again successfully at port 5173.

Test OpenRouter again before presenting: a Luna pass does not establish that a
different model follows the same instructions, or that free quota is available.
No separate OpenAI API key is used for the Codex option.

## Request flow

The browser sends the question to FastAPI as before. The backend redacts history,
retrieves knowledge and builds the existing versioned prompt. It starts
`codex exec` with the exact model and structured JSON schema, sends conversation
data through stdin, and accepts a completed answer. The usual source, figure,
privacy and action-claim checks still apply. Fixed safety paths do not call AI.

Requests run in a temporary directory, without user configuration or project
instructions. Coding, execution-host, browser, app, plugin and search features
are disabled; the sandbox is read-only and approvals cannot escalate it. The
CLI receives an allowlisted environment, without OpenRouter or Supabase keys.
The existing login is used by the CLI; this application does not extract tokens.

Each call is ephemeral and never resumes a Codex conversation. Temporary prompt,
schema and diagnostic files are removed on exit. CLI stderr and reasoning are
not returned or logged by the application. Text, tokens and provider data still
pass through the real service; ephemeral mode is not a provider retention promise.
Keep this option on a trusted local machine, with the servers bound to loopback.
It is not a shared public Codex gateway.

Only one CLI request runs at a time per backend process. Each attempt has a
75-second timeout and cancellation kills the child. A source-check revision can
make one additional real request. Errors remain errors; the application never
switches providers or inserts a stored answer. Traces record the requested model,
prompt version, elapsed time and token counts. Subscription cost is left unknown,
not calculated using OpenRouter's price settings.

## Verification

Real backend calls returned an AI greeting and a sourced minimum-payment answer.
All 148 backend tests, 21 frontend utility tests and 17 offline validator cases
pass, and the frontend production build passes. The transport tests also check
that a missing CLI produces an error without falling back to the API.
The browser run passed 17 workflows and skipped the optional unlinked-login
fixture. Its Chinese case timed out; a separate targeted rerun passed in 7.7 seconds.
Together these cover all 18 non-login workflows, with that initial failure retained
in the report. They include seven other live-model workflows and ten safety/UI
workflows. No model responses were simulated.

The minimum-payment answer was reviewed against the displayed MoneySense summary:
it explains unpaid-balance interest and possible interest on new purchases without
inventing a product rate. Conversation tests cover abuse, greetings, off-topic
requests, a typo, follow-up and corrected topic. Language checks cover one example
each in Chinese, Malay and Tamil; these are samples, not full language certification.

The two linked Supabase customers and broad complex-query reliability remain
deferred. The CLI adapter does not complete those checks.

References: [non-interactive CLI](https://learn.chatgpt.com/docs/non-interactive-mode)
and [CLI configuration](https://learn.chatgpt.com/docs/config-file/config-reference).

# How the DemoBank chatbot works

## Real-AI revision

Normal replies use LangChain and the configured provider. No browser server replaces the model. The v2 schema adds `answer_kind`: banking replies need supporting source IDs; brief greetings, clarifications without facts, and off-topic redirections use conversation with no citations or figures. No keyword match supplies the small current library instead of ranking the entire old conversation. History still goes to the real model so it can understand follow-ups. The frontend distinguishes AI-generated answers, fixed safety guidance and withheld answers. Semantic correctness and model classification still need live review.

The diagrams below describe the original v1 pipeline; the no-source and full-history retrieval branches are superseded by the revision above.

## 1. System overview

```
┌──────────────┐        ┌───────────────────────────────┐
│     User     │───────▶│  Frontend (React + Vite)      │
└──────────────┘        │  Chat.jsx, App.jsx            │
                        │  - redact secrets             │
                        │  - bound history              │
                        │  - parse SSE stream           │
                        └───────┬───────────────▲───────┘
                                │               │
          login / session       │               │ SSE: status → answer → done
     (public anon key only)     │               │      (or error)
                │               │ POST /chat    │
                ▼               │ messages, mode, language,
   ┌──────────────────────┐    │ Bearer JWT (personal mode)
   │  Supabase            │    ▼               │
   │  - Auth              │   ┌─────────────────┴─────────────┐
   │  - banking schema    │◀─│  Backend (FastAPI)            │
   │  - RLS on            │   │  api/chat.py                  │
   └──────────────────────┘   │  core/auth.py                 │
     per-user client +        │  services/answers.py          │
     customer_id filter       └───────┬───────────────┬───────┘
                                     │               │
                       evidence +    │               │ keyword search
                       history       ▼               ▼
                        ┌──────────────────┐  ┌──────────────────────┐
                        │  LLM (OpenRouter)│  │  Knowledge articles  │
                        │  strict JSON:    │  │  (in memory,         │
                        │  answer,         │  │   no embeddings)     │
                        │  source_ids      │  └──────────────────────┘
                        └──────────────────┘
```

- **Modes:** `public` (guest, general banking help only) and `personal` (signed in, synthetic customer records are added as evidence).
- The browser only holds the public Supabase URL and anon key. The backend never uses a service-role key, and RLS stays on.

## 2. Request flow (`POST /chat`)

```
┌────────────┐
│ 1. User    │ types a question
└─────┬──────┘
      ▼
┌─────────────────────────────────────────┐
│ 2. Frontend                             │
│    redact secrets, trim history,        │
│    POST /chat (+ JWT in personal mode)  │
└─────┬───────────────────────────────────┘
      ▼
┌─────────────────────────────────────────┐   personal mode only
│ 3. api/chat.py                          │──────────────────────┐
│    check mode / Authorization header    │                      ▼
└─────┬───────────────────────────────────┘     ┌────────────────────────────┐
      │                                         │ core/auth.py               │
      │◀────────────────────────────────────────│ validate JWT, per-user DB  │
      │        synthetic customer records       │ client, query by           │
      │                                         │ customer_id (RLS applies)  │
      ▼                                         └────────────────────────────┘
┌─────────────────────────────────────────┐
│ 4. SSE event: status                    │
│    "Checking the available info…"       │
└─────┬───────────────────────────────────┘
      ▼
┌─────────────────────────────────────────┐
│ 5. make_answer()  (see section 3)       │
└─────┬───────────────────────────────────┘
      ▼
┌─────────────────────────────────────────┐
│ 6. SSE events: answer → done            │   on any exception:
│    (JSON result, see section 4)         │   SSE event: error
└─────┬───────────────────────────────────┘
      ▼
┌─────────────────────────────────────────┐
│ 7. Frontend shows answer, sources,      │
│    disclaimer, human-support hint       │
└─────────────────────────────────────────┘
```

## 3. Answer pipeline (`make_answer`)

```
                 ┌───────────────────────────────┐
                 │ Redact + trim history         │
                 │ (max 29 msgs / 16k chars,     │
                 │  must start with a user turn) │
                 └───────────────┬───────────────┘
                                 ▼
 ┌──────────────────────────────────────────────────────────────┐
 │  FIXED RESPONSES  (never call the model)                     │
 │                                                              │
 │  ┌───────────────────┐   ┌────────────────────────────────┐  │
 │  │ Secret detected?  │─▶ │ "Private details were removed" │  │
 │  │ pwd/PIN/OTP/CVV/  │   │ (+ lost-card text if relevant) │  │
 │  │ card number       │   │ needs_human = true             │  │
 │  └───────────────────┘   └────────────────────────────────┘  │
 │           │ no                                               │
 │           ▼                                                  │
 │  ┌───────────────────┐   ┌────────────────────────────────┐  │
 │  │ Lost / stolen /   │─▶ │ Lost-card guidance             │  │
 │  │ missing card?     │   │ needs_human = true             │  │
 │  └───────────────────┘   └────────────────────────────────┘  │
 │           │ no                                               │
 │           ▼                                                  │
 │  ┌───────────────────┐   ┌────────────────────────────────┐  │
 │  │ Wants a person /  │─▶ │ Human-support answer           │  │
 │  │ agent?            │   │ needs_human = true             │  │
 │  └───────────────────┘   └────────────────────────────────┘  │
 │           │ no                                               │
 │           ▼                                                  │
 │  ┌───────────────────┐   ┌────────────────────────────────┐  │
 │  │ Guest asks about  │─▶ │ "Sign in and choose Personal   │  │
 │  │ "my" balance etc? │   │  demo records"                 │  │
 │  └───────────────────┘   └────────────────────────────────┘  │
 └───────────┬──────────────────────────────────────────────────┘
             │ no (normal question)
             ▼
 ┌───────────────────────────────────────────┐
 │ Keyword search of knowledge articles      │
 │ - retry using all user turns              │
 │ - non-English: use all current articles   │
 └───────────────┬───────────────────────────┘
                 ▼
 ┌───────────────────────────────────────────┐    ┌────────────────────────┐
 │ Any articles or customer records?         │─no▶│ "No approved source"   │
 └───────────────┬───────────────────────────┘    │ needs_human = true     │
                 │ yes                            └────────────────────────┘
                 ▼
 ┌───────────────────────────────────────────┐
 │ Build evidence                            │
 │ articles + `customer-records`             │
 │ (personal mode only)                      │
 └───────────────┬───────────────────────────┘
                 ▼
 ┌───────────────────────────────────────────┐
 │ LLM call  (strict JSON)                   │
 │ { "answer": "...", "source_ids": [...] }  │
 └───────────────┬───────────────────────────┘
                 ▼
 ┌───────────────────────────────────────────┐
 │ VALIDATION                                │
 │ 1. JSON parses, answer non-empty ≤4000    │
 │ 2. source IDs are valid                   │
 │ 3. figures appear in cited evidence       │
 │ 4. no leaked secrets or URLs              │
 │ 5. no "I have blocked/transferred…" claim │
 └───────┬───────────────────────┬───────────┘
         │ pass                  │ fail
         ▼                       ▼
 ┌──────────────────┐   ┌──────────────────────────────┐
 │ Answer + cited   │   │ "Could not verify this       │
 │ sources          │   │  answer…"                    │
 │ source_check =   │   │ needs_human = true           │
 │ basic_checks_    │   │ source_check = rejected      │
 │ passed           │   └──────────────────────────────┘
 └──────────────────┘
```

## 4. Result returned to the frontend

```
┌────────────────────┬───────────────────────────────────────────────┐
│ Field              │ Meaning                                       │
├────────────────────┼───────────────────────────────────────────────┤
│ answer             │ Text shown to the user                        │
│ sources            │ Cited articles or `customer-records`          │
│                    │ (articles open via GET /knowledge/{id})       │
│ privacy_warning    │ A secret was found and removed from input     │
│ history_trimmed    │ Older messages were dropped to fit limits     │
│ needs_human        │ Suggest official / human support              │
│ source_check       │ fixed_guidance | basic_checks_passed |        │
│                    │ rejected                                      │
│ disclaimer         │ Standing "general information only" notice    │
└────────────────────┴───────────────────────────────────────────────┘
```

## 5. Key files

```
┌─────────────────────────┬──────────────────────────────────────────────┐
│ Area                    │ File                                         │
├─────────────────────────┼──────────────────────────────────────────────┤
│ API + SSE               │ backend/app/api/chat.py                      │
│ Auth + customer records │ backend/app/core/auth.py                     │
│ Pipeline                │ backend/app/services/answers.py              │
│ LLM call                │ backend/app/services/llm.py                  │
│ Retrieval               │ backend/app/services/knowledge.py            │
│ Source / figure checks  │ backend/app/services/source_checks.py        │
│ Redaction (keep synced) │ backend/app/services/privacy.py              │
│                         │ frontend/src/lib/chatTools.js                │
│ UI                      │ frontend/src/Chat.jsx, frontend/src/App.jsx  │
└─────────────────────────┴──────────────────────────────────────────────┘
```

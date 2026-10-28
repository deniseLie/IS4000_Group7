# Multi-capability architecture (proposed)

Status: **proposal**. `chatbot-architecture.md` describes what is built today. This document describes how to add more capabilities without growing one long prompt.

## 1. Problem

Today every non-fixed question goes down one path: keyword search, then one LLM call with one prompt, then validation. Each new capability (disputes, product comparison, loan calculations) added to that prompt would make it longer, slower and harder to test. A change for one capability could also change the behaviour of the others.

## 2. Principle

> Decide **what the user wants** first. Then run a small, specialised **handler** for that intent. Each handler has its own prompt module, evidence and validator.

```
Safety rules  →  Intent router  →  Handler (prompt + evidence + validator)  →  Shared output checks
 (no LLM)         (rules, then      one per capability                          (JSON, sources,
                   small LLM)                                                    secrets, claims)
```

## 3. Target pipeline

```
                 ┌───────────────────────────────┐
                 │ Redact + trim history         │  (unchanged)
                 └───────────────┬───────────────┘
                                 ▼
 ┌──────────────────────────────────────────────────────────────┐
 │ FIXED RESPONSES (never call the model)       (unchanged)     │
 │ secrets · lost/stolen card · human support · guest "my ..."  │
 └───────────────┬──────────────────────────────────────────────┘
                 │ no match
                 ▼
 ┌───────────────────────────────────────────┐
 │ INTENT ROUTER                  (new)      │
 │ 1. keyword rules (cheap, deterministic)   │
 │ 2. if unclear: small LLM, strict JSON     │
 │    {"intent": "...", "confidence": 0-1}   │
 │ 3. low confidence → "other"               │
 └───────────────┬───────────────────────────┘
                 ▼
 ┌─────────────┬───────────────┬────────────────┬─────────────────┬──────────┐
 │ faq         │ account_query │ product_info   │ calculation     │ other    │
 │ (general    │ (personal     │ (loans, cards, │ (repayment,     │ (clarify │
 │  banking)   │  records)     │  compare)      │  interest)      │  or hand │
 │             │               │                │                 │  off)    │
 └──────┬──────┴───────┬───────┴───────┬────────┴────────┬────────┴────┬─────┘
        │              │               │                 │             │
        ▼              ▼               ▼                 ▼             ▼
   Handler         Handler         Handler          Code only      Fixed text
   (LLM)           (LLM)           (LLM)            (no LLM        needs_human
        │              │               │             for numbers)
        └──────────────┴───────┬───────┴─────────────────┘
                               ▼
              ┌──────────────────────────────────┐
              │ SHARED OUTPUT CHECKS  (unchanged)│
              │ JSON valid · source IDs valid ·  │
              │ figures in evidence · no secrets │
              │ or URLs · no action claims       │
              └──────────────────────────────────┘
```

## 4. Capabilities

```
┌───────────────┬──────────────────────────┬─────────────────────────┬───────────────────────────┐
│ Intent        │ Evidence                 │ Prompt modules          │ Extra validation          │
├───────────────┼──────────────────────────┼─────────────────────────┼───────────────────────────┤
│ faq           │ keyword-matched articles │ system + format +       │ shared checks only        │
│               │                          │ faq                     │                           │
├───────────────┼──────────────────────────┼─────────────────────────┼───────────────────────────┤
│ account_query │ customer-records (JWT,   │ system + format +       │ every figure must appear  │
│ personal only │ customer_id + RLS)       │ account                 │ in customer-records       │
├───────────────┼──────────────────────────┼─────────────────────────┼───────────────────────────┤
│ product_info  │ product articles         │ system + format +       │ no approval/eligibility   │
│               │                          │ product                 │ promises                  │
├───────────────┼──────────────────────────┼─────────────────────────┼───────────────────────────┤
│ calculation   │ user inputs + article    │ none for the numbers;   │ result computed by code,  │
│               │ with the rate/formula    │ LLM only explains       │ shown with its inputs     │
├───────────────┼──────────────────────────┼─────────────────────────┼───────────────────────────┤
│ other         │ none                     │ none (fixed text)       │ needs_human = true        │
└───────────────┴──────────────────────────┴─────────────────────────┴───────────────────────────┘
```

Rules that stay the same for every handler:
- Safety-critical cases are decided **before** the router and never reach a model.
- Guests can only reach `faq`, `product_info` and `calculation`. `account_query` requires personal mode and a valid JWT.
- The router chooses the handler. It never receives customer data.
- Numbers come from evidence or code, never from the model's own arithmetic.

## 5. Prompt composition

Prompts are built per request from small files, instead of one long file.

```
backend/app/prompts/v1/
  system.md         persona, tone, never-do rules          (all handlers)
  format.md         strict JSON output spec                (all handlers)
  public_only.md    guest restrictions                     (guest mode)
  intents/
    faq.md          how to answer from articles
    account.md      how to read customer records
    product.md      how to describe products, no promises
    calculation.md  how to explain a computed result
  router.md         intent labels and examples             (router only)
```

Per request: `system + format + (public_only if guest) + intents/<intent>`.

- `prompts.py` gains an `intent` argument to `load()`, and the digest covers the modules used.
- `prompt_version` in the result records which modules answered, so evaluation results can be traced.
- Released versions are never edited. Changes go in a new `v2/` directory (existing rule in `prompts.py`).

## 6. Code layout

```
backend/app/services/
  answers.py          make_answer: redact → fixed responses → route → handler → checks
  router.py           (new) classify(question, mode) → intent
  handlers/           (new)
    faq.py            search articles → LLM
    account.py        customer-records → LLM
    product.py        product articles → LLM
    calculation.py    parse inputs → compute → LLM explains
  llm.py              takes a prompt built from modules
  source_checks.py    shared validation
```

A handler has one signature: `async def handle(req, history, context) -> result`. This keeps `make_answer` short and lets each handler be tested alone.

## 7. Evaluation

- Add `expected_intent` to `evaluation/offline_cases.json`. Offline tests check the router without model calls.
- Add routing cases that are meant to be hard: "my card" versus "lost card", "what is the loan rate" versus "can I get a loan".
- Check per-intent pass rate in live runs, not only the overall rate.
- Test that guests asking account questions never reach `account_query`.

## 8. Trade-offs

```
┌───────────────────────────┬──────────────────────────────────────────────────────┐
│ Choice                    │ Reason                                               │
├───────────────────────────┼──────────────────────────────────────────────────────┤
│ Router, not tool calling  │ Easier to audit and test; the model cannot choose to │
│                           │ touch customer data on its own.                      │
├───────────────────────────┼──────────────────────────────────────────────────────┤
│ Rules first, LLM second   │ Cheaper and deterministic for obvious cases; the LLM │
│                           │ only handles unclear ones.                           │
├───────────────────────────┼──────────────────────────────────────────────────────┤
│ Code for calculations     │ Models make arithmetic errors; the figure check      │
│                           │ stays meaningful.                                    │
├───────────────────────────┼──────────────────────────────────────────────────────┤
│ No multi-agent setup      │ Extra cost and failure modes with no benefit at this │
│                           │ scale.                                               │
└───────────────────────────┴──────────────────────────────────────────────────────┘
```

Known risks: a misrouted question gets the wrong handler (mitigated by the low-confidence fallback to `other`), and the router adds one extra model call on unclear questions.

## 9. Suggested build order

1. Add `router.py` with keyword rules only, plus `expected_intent` in offline cases.
2. Split the existing path into `faq` and `account_query` handlers, with no behaviour change.
3. Split prompt modules under `intents/` and extend `prompts.py`.
4. Add the LLM fallback to the router.
5. Add `product_info`, then `calculation`.

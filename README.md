# UBS LLM-Powered Chatbot

> **Status:** Preliminary / in early development
> **Course:** IS4000 – Term 7

An LLM-powered conversational assistant built for UBS. This document is a preliminary overview and will evolve as the project takes shape.

## Overview

This project explores how a large language model (LLM) can be used to build a chatbot that helps UBS users (e.g. clients, employees, or support staff — *to be confirmed*) get accurate, timely answers and complete common tasks through natural-language conversation.

## Objectives

- Provide a conversational interface for [TODO: target use case, e.g. client FAQs, internal knowledge search, service requests].
- Ground answers in approved, authoritative sources to reduce hallucinations.
- Meet the security, privacy, and compliance expectations of a financial institution.
- Measure and improve response quality, safety, and latency.

## Scope

**In scope (planned)**
- Chat interface (web) with multi-turn conversation
- LLM integration with retrieval-augmented generation (RAG) over a curated knowledge base
- Guardrails: content filtering, PII handling, refusal of out-of-scope or advice-giving queries
- Logging and evaluation tooling

**Out of scope (for now)**
- Executing financial transactions
- Personalised investment advice
- Processing real client data (prototype uses synthetic/public data only)

## Proposed Architecture

```
User ──► Chat UI ──► API / Orchestrator ──► Guardrails ──► LLM
                           │                                 ▲
                           └──► Retriever ──► Vector DB ─────┘
                                     ▲
                              Knowledge base (documents)
```

| Component | Description | Candidate tech |
|---|---|---|
| Frontend | Chat UI | [TODO] e.g. Next.js / React |
| Backend | Orchestration, session handling | [TODO] e.g. Python (FastAPI) / Node |
| LLM | Response generation | [TODO] e.g. Claude, GPT, open-source model |
| Retrieval | Embeddings + vector search | [TODO] e.g. pgvector, Chroma, Pinecone |
| Guardrails | Input/output moderation, PII redaction | [TODO] |
| Evaluation | Quality and safety testing | [TODO] |

## Getting Started

> Setup instructions will be added once the initial implementation exists.

```bash
# Clone the repository
git clone <repo-url>
cd Project

# Install dependencies  (TODO)
# Configure environment variables  (TODO)
# Run the app  (TODO)
```

Configuration will use environment variables (e.g. `LLM_API_KEY`). **Never commit secrets** — use a `.env` file that is git-ignored.

## Responsible AI, Security & Compliance

Because this is a financial-services context, the following are design requirements from the start:

- **Data privacy:** no real client or confidential UBS data in development; redact PII in prompts and logs.
- **Accuracy & transparency:** cite sources for answers; clearly indicate the user is talking to an AI.
- **Safety:** guardrails against prompt injection, jailbreaks, and harmful or non-compliant output.
- **Compliance:** align with applicable regulations and UBS internal policy [TODO: confirm with stakeholders].
- **Auditability:** conversation and decision logging with appropriate access control.

## Evaluation Plan

- Answer accuracy and groundedness against a labelled question set
- Hallucination and refusal rates
- Latency and cost per conversation
- User feedback / usability testing

## Roadmap

- [ ] Define use case, users, and success metrics
- [ ] Collect and prepare knowledge base (public/synthetic data)
- [ ] Build baseline chatbot (LLM + prompt)
- [ ] Add retrieval (RAG)
- [ ] Add guardrails and safety testing
- [ ] Build evaluation harness
- [ ] Build UI and run user testing
- [ ] Final report and demo

## Open Questions

- Who are the target users and what are the top tasks?
- Which data sources are available and permitted?
- Which LLM/provider and hosting model are acceptable?
- What are the compliance constraints for the prototype?

## Team

- [TODO: names and roles]

## License

[TODO: choose a license, or mark as coursework / all rights reserved]

## Disclaimer

This is an academic project. It is not an official UBS product and does not provide financial advice.

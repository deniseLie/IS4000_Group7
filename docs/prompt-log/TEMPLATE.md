## <prompt name> v<N> (<YYYY-MM-DD>)

| Field | Value |
|---|---|
| Prompt name / files | |
| Version ID (`v<N>+hash`) / git commit | |
| Author / reviewer | |
| Goal | one sentence: which failure it targets |
| Model + provider | exact model ID, provider |
| Parameters | temperature / top_p / top_k (or "provider default"), max_tokens, output schema |
| Context fed in | retrieval, evidence, history limit |
| Change from previous | what changed, and why |
| Hypothesis | expected improvement; possible regressions |
| Eval run | mode (offline/live), dataset + `dataset_sha256`, N cases, date |
| Results | pass/fail/error/needs_human_review, vs previous version |
| Failures observed | case IDs + note |
| Decision | adopted / rejected / rolled back, and why |
| Known limits | what was not tested |

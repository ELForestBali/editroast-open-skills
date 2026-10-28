# $20 subscription-first capacity planning for 90,000-word manuscripts

**Status:** engineering planning model, **not a measured benchmark**, as of 2026-10-28.  
**Audience:** EditRoast maintainers, Codex/Claude Code implementers, independent reviewers.  
**Decision:** optimize the product for **ChatGPT Plus → Codex** and **Claude Pro → Claude Code**, each advertised at a US list price of **$20/month**, with agents authenticated through their existing subscription. The MIT-licensed EditRoast skills remain free. Avoid mandatory API keys, API billing, paid tiers or pay-as-you-go usage in the default path.

## 1. What subscriptions do and do not guarantee

- Codex can authenticate with a ChatGPT account under the Plus subscription; Claude Code can authenticate with a Claude Pro account. Both share plan-specific allowances with other provider products. Actual allowed work depends on task, model, context and settings.
- Providers may enforce **five-hour and weekly usage limits**; these are **not published as a guaranteed token allowance** for every book. There is no defensible conversion from 1.5M model tokens to a fixed number of five-hour resets, days, or books per month.
- **$20 is the subscription list price, not the price of processing one manuscript.** For an existing eligible subscription, incremental provider charges can be zero **only while staying inside included usage**. Once quota is exhausted, stop, checkpoint, and resume after reset, rather than automatically buying credits or switching to API billing.
- Do not promise that a 90,000-word manuscript fits in a single session or one weekly quota. Do not present estimated model tokens as actual billed tokens or remaining subscription balance.
- Check that the agent is really signed in to the subscription rather than using an API key, especially Claude Code, whose configured API credentials can change billing behavior. Do not enable optional paid usage credits or resets without explicit user action.
- Local Python preparation, state, validation and future local production tooling have no EditRoast usage fee; model-provider processing is cloud-side and manuscript text sent to it is governed by provider terms.

Official sources (consult again before release or public pricing claims):
- [ChatGPT Plus price](https://help.openai.com/en/articles/6950777-what-is-chatgpt-plus)
- [Using Codex with a ChatGPT plan](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan)
- [Work/Codex allowance explanation](https://help.openai.com/en/articles/20001516-managing-usage-with-gpt-6-astra-in-work-and-codex)
- [Claude Pro and price](https://support.claude.com/en/articles/8325606-what-is-the-pro-plan)
- [Use Claude Code with Pro](https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan)
- [Optional paid Claude usage credits](https://support.claude.com/en/articles/12429409-manage-usage-credits-for-paid-claude-plans)

## 2. Illustrative token budget for an English-language book

**Base:** 90,000 English source words; rough input size ~120,000–140,000 model tokens depending on tokenizer, punctuation and genre. Existing v1 roast/proofreading default prepares chunks using **5,000 words**, giving approximately **18 chunks** for a simple 90k-word manuscript; chapter boundaries can change this count.

The table includes repeated source reading, book map/glossary/context, instructions and generated reports. All values are engineering **ranges to validate**, not measurements of OpenAI or Anthropic quota consumption.

| Stage | Input tokens | Output tokens | Input + output | Release reality |
| --- | ---: | ---: | ---: | --- |
| Editorial roast: book inventory, three *virtual editorial lenses*, board synthesis and evidence-backed reports | 420k–720k | 54k–114k | **474k–834k** | Roast workflow exists; do **not** imply independent human editors or a validated automatic three-agent orchestration |
| Proofreading: style sheet, all-chunk checks, corrections ledger and report | 240k–420k | 30k–78k | **270k–498k** | Proofreading workflow exists; no production-ready apply-to-DOCX |
| Literary translation plus terminology/cultural consistency QA | 356k–530k | 160k–218k | **516k–748k** | **Future scenario; current literary-translation skill is BETA scaffolding, not end-to-end translation** |
| Production/layout: generate/verify files **using deterministic local code** | 15k–70k | 3k–15k | **18k–85k** | **Future scenario; current book-production skill is BETA, not KDP/EPUB/PDF generation** |
| **Four stages combined** | **1.031M–1.740M** | **247k–425k** | **1.278M–2.165M** | **Not a tested end-to-end product** |

**Optional separate stage**: author-approved manuscript revision and verified application could require another ~200k–450k input and ~130k–200k output tokens, or more when many passages are rewritten. **Do not hide this in roast/proofreading estimates.** It is not shipped as a stable apply-to-DOCX feature.

Main uncertainties: hidden reasoning tokens; agent/tool overhead; additional rounds; duplicated long context; response verbosity; cache hits/misses; target-language length; correction density; QA strategy; model and user-level usage limits. No API-price dollar estimates are relevant to the primary $20-subscription product path.

## 3. Language-aware planning

**Never extrapolate a language-independent tokens-per-word constant.** Segment boundaries and morphological/Unicode tokenization differ. The following are only starting hypotheses for **90,000 actual source words** (not 90k English-equivalent words). They must be replaced with direct token counts from representative texts using the chosen provider/model tokenizer.

| Source language | Heuristic source tokens for 90k words | Confidence |
| --- | ---: | --- |
| English | 120k–140k | Rough English rule of thumb |
| Spanish | 135k–160k | Unverified planning estimate |
| French | 140k–170k | Unverified planning estimate |
| Indonesian | 135k–180k | Unverified planning estimate |
| German | 155k–195k | Unverified planning estimate |
| Russian | 180k–240k | Unverified planning estimate |
| Korean | 225k–310k | Unverified planning estimate |

Chinese and Japanese: **do not normalize by '90,000 words' without a documented word segmentation standard**. Use characters plus actual model tokenizer counts. Translating from English to another language can substantially change both word count and target tokens; the 160k–218k translation-output planning interval is **one illustrative scenario**, not valid for every pair.

### Fragment sizing

- Replace or supplement the fixed 5,000-word boundary with a **token-aware target of 6k–8k source tokens** per fragment as an initial *configurable* heuristic. Count after extraction; preserve paragraph/scene/chapter boundaries and stable source IDs.
- Formula: `planned_chunks = ceil(measured_source_tokens / target_source_tokens_per_chunk)`; allow additional splits caused by chapter boundaries.
- English 120k–140k source tokens → ~15–24 token-sized fragments at a 6k–8k target; an example 120k-token / 8k target equals 15. The current 5k-word default ≈18 fragments for 90k English words **is not equivalent** to token-aware chunking.
- Russian 180k–240k → ~23–40 token-sized fragments at a 6k–8k target; Korean 225k–310k → ~29–52. These are ranges derived from **unverified heuristics** and do not describe guaranteed provider limits.
- Track `source_words`, `source_characters`, `measured_source_tokens`, `tokenizer_id`, `target_language`, `chunk_count`, `estimated_output_tokens` and estimation method/version; do not silently substitute word counts for token counts.

## 4. Batches, sessions and quota

- A **work batch** is a progress checkpoint (e.g. 3–6 fragments), **not a five-hour usage window**. For 18 fragments, each full-book stage might take **3–6 batches** and several manual resumption points; this says nothing definitive about how many subscription resets are needed.
- Progress states should distinguish `pending`, `in_progress`, `completed`, `validated`, `blocked_by_usage_limit`, `needs_author_decision`. On quota exhaustion, persist the pending/finished chunk IDs, reports, glossary, selected provider, known usage warnings and timestamps; do not mark unfinished reports complete.
- Resume using a compact manuscript map/style sheet/glossary, not a re-upload or complete reread of all previously processed chunks on every turn. Preserve evidence traceability and correct cross-book synthesis.
- Never automatically purchase credits, trigger a paid reset, fall back to separately billed APIs, upgrade a subscription, or swap providers. A user must explicitly authorize any billing-affecting action.
- Quote a model's context-window specification separately from the subscription's dynamic allowance; a book fitting the window does **not** imply sufficient quota to run all passes.

## 5. Measurement protocol (required before external claims)

Run a **real** end-to-end benchmark with a permissioned 90k-word manuscript (or licensed/synthetic equivalent); start with 2–3 representative chapters for each language and scale only after quality and checkpointing pass.

For **each** of ChatGPT Plus/Codex and Claude Pro/Claude Code:
1. Record date, OS, skill release SHA, agent version, model, reasoning/effort mode, account subscription (no keys), language pair, number of words/characters, and measured tokenizer/source-token counts.
2. Record each stage's input/output totals **only if the agent/provider exposes reliable numbers**. Report `not exposed` rather than fabricated precision; save per-pass chunk counts, output text size, time, failed retries, and context size.
3. Capture usage status before/after each batch and any five-hour/weekly reset warning. Provider status may be a percent/allowance, **not tokens**; log its units exactly.
4. Test interruption at an artificial stop and at a real usage limit if reached; ensure no duplicated work and validate manifest coverage, source quotes, consistency, and the final output.
5. Report human QA: severity/utility of roast findings, false-positive corrections, translation voice/terminology agreement, and rendering/preflight integrity. Source integrity checks alone do not establish editorial quality.
6. Only after repeatable measurements, publish provider-specific ranges with test conditions. Do not claim universal `$0 per book`, `N books/month`, `completed in one session`, or exact subscription-equivalent tokens.

### Candidate capacity report (planned interface, not implemented)

Show: `source_words`, `language_pair`, `measured_source_tokens (if available)`, `proposed_chunks`, `per-stage estimated input/output`, `active provider and plan`, `completed/validated chunks`, `last known usage reset`, `remaining quota if exposed by provider`, `source and confidence of every figure`, and `next safe resume action`.

Public marketing-safe wording:

> The EditRoast skills are free and MIT licensed. They can run through your existing ChatGPT Plus + Codex or Claude Pro + Claude Code access without a mandatory API key. Provider usage limits apply; large books may require multiple sessions. Preparation and validation run locally, but text analyzed by the AI agent is handled by its provider. Full-book translation and publish-ready layout remain beta/future capabilities until independently tested.

## 6. Implementation tasks (not shipped)

1. Measure tokens by provider/model and auto-size source chunks while retaining stable chapter/paragraph identifiers and current CLI behavior.
2. Show a **clearly labelled estimate** and provenance/confidence before a run; never invent plan-token balances.
3. Add checkpoint/restore and a quota-stop state with tests for resumes, nonduplication and integrity.
4. Benchmark actual model calls under both $20 subscriptions, in English and at least Russian, Indonesian and a CJK test case, including translation output size and quality.
5. Only after acceptance testing, ship full translation, author-approved revision application and deterministic DOCX/EPUB/PDF production, each with independent usage and quality budgets.

This is a design and measurement contract, **not a claim that these features or quantified performance exist in the current release**.

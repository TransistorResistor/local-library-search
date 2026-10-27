# Local Library Search — plan v1.2

Date: 27 October 2026
Status: items 1–5 implemented locally on 27 October 2026. Network-share integration and local-model quality comparisons remain release gates; no new GitHub publication.

## Purpose

Make ingestion progress predictable, repair supported UNC clipper access, and make the search assistant easier to direct and quicker to finish once it has adequate evidence. Preserve the local-first native interface, source attribution, configurable context cap and optional reranker.

The user reports an erratic refresh bar, a UNC error when importing a network-hosted clipper, syntax failures around quotes/hyphens, infrequent proximity searches, and repeated searching after an answer appears established. These are distinct problems: avoid treating a larger context window, more searches or reranking as a general solution.

## Pre-implementation baseline

- Folder ingestion discovers and processes files in one pass. Its total is unknown until the end, and the GUI switches between activity and determinate modes as sources change. Per-source counts reset. This explains a plausible cause of the reported bar behaviour; the exact visual sequence has not been captured.
- Clipper connections use `Path.as_uri() + '?mode=ro'`. A Windows UNC path becomes `file://server/share/database`, with a non-local URI authority. SQLite normally rejects that authority unless built with a special compile-time option. This is a strong candidate for the reported error, not an end-to-end reproduction on the user's share.
- The model chooses when to return the `answer` action. The host checks action syntax, string types and whether cited source IDs were exposed. It does not establish semantic support, require coverage of every question part, or independently determine that enough evidence was found. The normal loop has a 24-response limit; exhausting it currently returns `round_limit` instead of reserving a final synthesis step.
- Search strings go directly to FTS5. Quotes, punctuation, hyphens and operator syntax therefore reach the model and user. The prompt has one short NEAR example; it does not provide an explicit evidence checklist or stopping policy.
- Optional reranking scores bounded FTS candidates against the original question or an explicit relevance question. It cannot compensate for an invalid query or evidence outside the candidate pool.

## Implementation status — 27 October 2026

- **1 — Refresh progress:** one discovery pass builds bounded, spillable manifests for folders and selected clip rows before processing. One frozen total and monotonic processed count spans all sources, with partial-scan warnings, separate reconciliation/completion phases and cancellation. Cancellation skips unfinished deletion reconciliation; clipper cancellation rolls back that collection. Hashing and isolated extraction check cancellation; native filesystem calls can still delay it. The GUI retains its single worker: Stop operation returns control to interactive work; concurrent scheduling is not introduced.
- **2 — Network sources:** shared read-only SQLite URI/connection helpers preserve encoding and empty UNC authority. Network/mapped-drive sources require an explicit closed snapshot or completed-export mode and are copied with sidecar/change checks to private local scratch storage. Local source connections remain read-only. Active network search indexes are refused; open errors include SQLite/compile diagnostics. Real-share validation requires a supplied closed/exported database and remains pending.
- **3 — Search input:** a shared literal compiler supports all/any/exclude/near, simple words/quoted phrases, and explicit advanced FTS5. GUI simple mode is the default; CLI/raw query callers retain advanced compatibility. Catalog title queries share the compiler. Reranker pool bindings use compiled queries and normalised defaults; GUI continuation retains submitted search settings. Compiled queries and proximity help are visible.
- **4 — Completion:** selectable non-legacy policies retain a small evidence checklist and referenced evidence through checkpoints, reject invalid references, and issue a warning after three unproductive retrieval calls. The model can attach coverage to an ordinary action without a mandatory planning call. Current-prompt source validation applies to non-legacy answers; unresolved/conflicting needs are displayed as gaps. Status records reflect assessed coverage rather than semantic verification.
- **5 — Effort:** Quick/Balanced/Thorough use 6/12/24 total responses including one reserved answer-only final response; malformed, invalid and note/checkpoint responses consume budget. CLI overrides include the final response in their cap. The final prompt retains checklist evidence within the context cap, no further tool loop is granted, and malformed/truncated final output is incomplete. Mode, budget, checklist, stall events and stopping reason are traced. Legacy retains the previous completion loop and remains the default pending paired model-quality evaluation; the GUI provides a comparison checkbox beside the three-stop slider.

Implementation validation: 70 desktop-search tests and four metadata-demo tests pass. One additional real-share integration test is skipped unless a closed UNC export is supplied through `LOCAL_SEARCH_TEST_UNC_DB`. Scripted model responses verify protocol/state transitions, not answer quality. Paired local-model quality and actual network-share trials remain pending.

Layout tidy-up: resizable library-results/reader split, Activity and Timing tabs, collapsible search options, grouped settings pages, source-table scrollbars, persistent settings actions and local result previews. Component interaction checks pass; the native screenshot preview could not complete because Computer Use app approval timed out.

Additional completed work: explicit tool/action rejection diagnostics and a configurable notebook (6,000 tokens by default, 64–32,768 allowed), with used/remaining space reported to the model, live display and trace, clear failed-save messages, and total-context enforcement.

**Scope update:** the user is satisfied with current search performance. Broader query/candidate-fetch tuning and concurrent-refresh performance work are parked; this implementation does not introduce ranking optimisations. Recency/relevance mixing remains the separate exploration below. Full RAM snapshots remain dropped.

Tool-format exploration completed locally: two initial passes across three synthetic questions compared flat raw FTS5 and structured literals through a pinned OpenRouter Qwen3.8 27B provider. Both had zero JSON/search-syntax errors; structured literals used 15 requests versus 18. Three early-answer rejections were missing coverage, prompting explicit guidance and four rejection-free follow-up trials. Keep structured literals preferred for ordinary searches and raw FTS5 available for advanced expressions; no default-policy change or broad reliability claim. A detailed tool guide accompanies the prompt clarification; evaluation reports/traces remain local.

## Delivery order

| Order | Deliverable | Reason | Risk |
|---|---|---|---|
| 1 | Inventory-based refresh progress | Direct usability regression; independent of answer quality. | Low/moderate: extra discovery latency and filesystem races. |
| 2 | UNC connection handling and supported network-source workflow | Reported import failure. | Low/moderate for URI construction; higher for live remote SQLite consistency. |
| 3 | Shared safe search compiler and simple/advanced input modes | Remove common parser failures before changing model policy. | Moderate: accidental changes to Boolean/tokenisation semantics. |
| 4 | Prompt examples and explicit answer-completion criteria | Improve evidence gathering and reduce redundant searches. | Moderate: prompt changes can reduce recall or encourage early answers. |
| 5 | Faster/Balanced/Thorough control and bounded finishing | Expose a useful effort tradeoff with measurable behaviour. | Moderate: early-stop and finalisation policies need paired evaluation. |

Implement and check each feature separately. Keep the current behaviour available for comparisons. Previous cache/retry-guard experiments degraded performance: do not revive those policies wholesale or combine all changes in one evaluation.

## 1. Count candidates, then show a stable percentage

### Proposed behaviour

Use two explicit phases for one refresh job:

1. **Discover/count:** enumerate eligible PDF paths across selected roots and obtain a consistent clip selection. Show `Finding candidates: N found`, current source and elapsed time. An activity indicator is honest here because the total is still being established.
2. **Process:** freeze a run manifest and denominator. Show `Processed 327 / 1,482 candidates — 22.1%`, current file, operation, source number and outcomes. The main bar is determinate and monotonic across the entire job, rather than resetting for each source.

Count candidate occurrences that will actually be checked, including cached files, duplicates and files later skipped for size/text quality. The percentage measures work items attempted, not newly added documents, bytes transferred, pages extracted, successful indexing or remaining time. Keep those outcome counts separately labelled; cached counts overlap indexed/skipped outcomes.

Use the inventory directly for processing rather than walking the tree again. Store larger manifests in application-owned temporary storage when useful. Keep symlink/junction exclusions identical between discovery and processing. Do not pre-hash or extract PDFs just to determine the count.

For clipper data, avoid counting one database version and later reading another. Use a short supported read snapshot to materialise the selected clip records into local scratch storage, or another consistent bounded spool. Do not hold a remote database transaction open throughout hours of PDF processing. The network-source limitations in section 2 still apply.

### Completion and changes during a run

- Increment the processed numerator once per candidate, after its outcome is known: indexed, skipped, cached outcome or failed. Multiple phases for a file do not increment it multiple times.
- Files arriving after inventory are deferred to the next refresh, so the denominator does not jump. Re-stat/hash a candidate when processing it; preserve the existing post-extraction change checks.
- A vanished or inaccessible candidate is recorded and accounted for without quietly shrinking the denominator. Preserve existing stale-content and deletion safeguards.
- Incomplete traversal or unavailable sources must remain visible. Do not claim a complete library scan or perform unsafe deletion reconciliation. A partial inventory can be processed with an explicit warning; its percentage describes only the enumerated manifest.
- Show `Finalising / reconciling` distinctly. Reaching 100% of candidate attempts does not mean the whole job is finished or successful. If needed, keep the finalising phase visible at 100% until transactions/cleanup end, then show `Complete` or `Complete with errors`.
- Empty roots finish explicitly as zero candidates; no division by zero or indefinite animation.
- Continue current terminal output and rotating failure logs. Discovery itself must remain responsive on slow shares. Preserve a last-event age/elapsed display during long hashing/extraction calls.

### Acceptance

Check mixed roots and clippers; empty, cached and duplicate-heavy runs; errors and skips; source disconnection; arrivals/deletions/modifications after inventory; partial traversal; large manifests; slow enumeration; final cleanup. Percentage must never move backwards within the processing phase. Verify that counts agree with the manifest and outcome summary, and that no extra full directory traversal or extraction pass was added.

## 2. Fix UNC clipper access without weakening database safety

### Investigate and repair the path boundary

Capture a sanitised exception and SQLite compile options locally. Separate URI-authority errors from authentication, share availability, permissions, locking and WAL failures.

Add one tested SQLite read-only filename/URI helper for local drive paths, UNC paths and supported mapped drives. A UNC-compatible empty-authority URI is a candidate; confirm the precise Windows SQLite VFS behaviour on the installed builds rather than relying only on a string transformation. Preserve percent-encoding for spaces, Unicode, `%`, `#` and `?` and retain `mode=ro` plus `query_only`.

Do not work around the problem by silently falling back to a normal read/write connection, depending on a mapped drive letter existing in every process, requesting a custom SQLite build, or stripping path components. Keep browser/file-opening URIs separate from SQLite connection URIs: their requirements differ.

Apply the helper to supported read-only database consumers, including the standalone demo where appropriate. Keep the authoritative search index on local storage; a fix for remote source paths is not approval to place the active WAL search index on a share.

### Network database modes

| Source state | Proposed handling |
|---|---|
| Stable/closed clipper DB on a reachable share | Supported UNC import; existing explicit closed-snapshot route can create a verified private local copy. |
| Export/snapshot produced consistently by the clipper host | Preferred workflow for an actively used remote clipper. Import an atomically published completed export locally. |
| Live remote WAL database | Do not promise safety from a URI fix. SQLite's normal WAL model is not supported across network filesystems. Explain the limitation and offer a supported export/closed-snapshot workflow. |
| Unknown lock/snapshot state or disconnected source | Fail clearly, retain the prior index, and log the cause; never guess that copying only the main DB is consistent. |

Do not delete WAL/journal files, enable `immutable=1` on a live source, ignore sidecars or silently change the source's journal mode. SQLite backup/export is useful only when the source can be opened safely; it does not make unsafe remote locking reliable. A producer-side snapshot/export is the robust option for a changing remote source.

### Acceptance

Test actual Windows UNC paths and mapped drives, spaces/Unicode/reserved URI characters, unavailable shares, access denied, read-only enforcement, closed-copy verification and sidecar refusal. Include a real-share integration check in addition to mocked path tests. A code fix alone must not be reported as proving safe live network WAL access. Source content must remain unchanged and failed imports must not prune the existing collection.

## 3. Simpler search input shared by the GUI and model

### Simple mode and structured tool arguments

Keep raw FTS5 as an explicit **Advanced FTS5** mode. Add a plain/safe mode for ordinary user searches and structured literal arguments for the model. Compile these to parameterised MATCH queries in application code.

An illustrative model call:

```json
{
  "action": "search",
  "all": ["export controls"],
  "any": ["ASPI", "Australian Strategic Policy Institute"],
  "exclude": ["draft"],
  "near": {"terms": ["critical minerals", "supply chain"], "distance": 10}
}
```

The initial schema should stay small: `all` means all literal terms/phrases; `any` is one parenthesised OR group; `exclude` applies binary NOT to a positive query; optional `near` adds one unordered proximity group. Only require the fields used. Lists contain literal text, so the model does not need to manually nest FTS quotes inside JSON strings. JSON itself still has normal string-escaping rules.

Keep raw `query` as a backwards-compatible alternative. Reject calls mixing raw and structured representations instead of guessing precedence. Preserve source/date filters, sort, limits, paging, reranking scope and relevance questions. Require a positive clause; negative-only queries produce an actionable message. Bound clause counts, lengths and NEAR distances.

For the GUI, ordinary whitespace-separated words can compile as quoted literals joined with AND. Provide an explicit phrase/proximity affordance or clear examples rather than interpreting an entire multiword question as one exact phrase. Treat hyphenated identifiers such as `ABC-123` as one quoted literal input. Handle straight/typographic quotes predictably, document unmatched-quote behaviour, and expose the compiled query for inspection.

Quoting solves parser ambiguity; it does not change the tokenizer. Under the current tokenizer, a hyphenated code can behave like adjacent tokens and match a spacing variant. Do not advertise character-exact identifier matching without a separate exactness check. Prefix matching, arbitrary nested Boolean expressions and column syntax remain advanced features initially.

### Proximity examples

Include a few targeted examples in the editable prompt and GUI help:

| Need | Structured proximity | Equivalent advanced FTS5 |
|---|---|---|
| Nearby concept words | `{"terms":["export","control"],"distance":5}` | `NEAR(export control, 5)` |
| Nearby multiword concepts | `{"terms":["critical minerals","supply chain"],"distance":10}` | `NEAR("critical minerals" "supply chain", 10)` |
| A code near an action | `{"terms":["ABC-123","approval"],"distance":6}` | `NEAR("ABC-123" approval, 6)` |

NEAR is unordered and operates within indexed passages, currently around 220 whitespace-separated words. It is useful for locating associated concepts, not proof of a semantic relationship. Do not force it for every query. Start with a few selective terms, use proximity when association matters or broad results need narrowing, and relax it after zero hits.

### Acceptance

Test hyphens, embedded/typographic quotes, punctuation, organisation names, numeric codes, operator words used literally, negative-only input, OR grouping, empty clauses, zero-hit queries and malformed advanced syntax. Compare compiler output against manually verified FTS results. Preserve exclusions and filters; never silently repair a raw syntax error into a broader query. Run the same compiled search through lexical and reranked paths, including frozen pagination.

## 4. Evidence checklist and an explicit stopping standard

### Evidence standard

An answer is ready when each requested part is supported by relevant retrieved evidence or is explicitly unresolved, and no material unresolved contradiction undermines the supported conclusion. An unknown can be an honest completion state after proportionate searching; zero hits alone do not establish absence.

- A straightforward factual question can be answered from one clear, directly supporting source. Do not require several copies or redundant searches merely to reach a source count.
- Comparisons need evidence for each compared side. Compound questions need coverage of their separate parts.
- Current/historical questions require appropriate publication/information-date interpretation. Recent capture time is insufficient.
- Conflicts or revisions justify targeted additional checks. If unresolved, describe the disagreement rather than manufacture certainty.
- Broader synthesis may benefit from independent sources; copied passages do not count as corroboration.
- Factual claims should identify the supporting sources. Host-valid citation IDs are necessary for attribution but do not prove entailment. Reranker scores and the model's self-confidence are not substitutes for evidence.

### Prompt shape

Ask the model to identify a small number of evidence needs before broad searching, then resolve them one at a time. Keep the checklist concise, for example: publisher — supported by source 4; publication date — unknown; applicable exception — source 7; current status — conflicting.

Use evidence IDs/source references and short statuses such as `needed`, `supported`, `conflicting`, `unresolved`. This is an auditable task checklist, not a request for verbose private reasoning. Avoid adding a mandatory planning tool call and a large new JSON state machine before testing whether a short prompt/payload addition suffices. Keep it within the existing context cap and make sure checkpointing does not discard the references required to use it.

After a useful result, decide what missing item the next call will resolve. Once material needs are satisfied, answer. Do not repeatedly search to reconfirm the same conclusion or read a full document when a sufficient source passage is already available. If another search is warranted, it should target a concrete gap or contradiction.

### Loop detection

Track repeated query/filter combinations, returned passage IDs and exposed excerpt ranges. Detect consecutive retrieval calls that add no useful new evidence or checklist progress. Distinguish failed/empty searches from note/checkpoint actions; exact duplicates across sources are not new corroboration.

Start with a visible stall warning and a finalisation reminder. An initial three-unproductive-retrieval trigger is a hypothesis to evaluate, not an established threshold. A broader mode may allow one deliberate alternative strategy. Do not reuse the earlier regressing retry guard without paired evidence. Log why the assistant continued or stopped so users can distinguish completed coverage, unresolved gaps, stall and budget exhaustion.

## 5. Faster versus more complete answers

Use a slider with **three labelled stops** initially, rather than implying that a smooth numeric scale measures reliability. The setting changes effort/depth and optional corroboration; all positions share the same honesty, citation and evidence requirements.

| Mode | Intended behaviour | Initial response-budget hypothesis |
|---|---|---|
| Quick | Address the requested parts directly, finish when evidence is sufficient, and disclose remaining gaps. | Up to 6 model responses, including a reserved final response. |
| Balanced | Resolve material ambiguity and perform targeted checks where warranted. | Up to 12 responses, including finalisation. |
| Thorough | Broader coverage, revision/conflict checks and proportionate independent corroboration. | Up to 24 responses, including finalisation. |

These are starting values for evaluation, not promised quality or wall-clock limits. Do not spend the whole budget when an answer is ready. Model loading, long prefill and a single large search can dominate elapsed time even in Quick mode. Preserve stop/cancellation controls and exact prompt-plus-output cap enforcement. Do not tie the slider implicitly to context allocation, thinking, reranker device or candidate count; those remain separate controls.

Reserve a final response before the normal tool budget runs out. At that boundary, request synthesis from already retrieved evidence and allow only a final answer, including explicit unknowns. Do not grant an unbounded new tool loop or invent evidence to satisfy the finish request. Handle malformed/truncated final output as an explicit incomplete result. Distinguish `answered`, `answered_with_gaps`, `cancelled`, `incomplete_output` and `budget_exhausted` in the review design; avoid pretending a budget-limited partial response is exhaustive.

Store the selected mode and stopping reason in the local trace and show an unobtrusive summary, such as `Balanced · 5 model rounds · evidence complete`. A checklist marked complete is the model's assessed coverage, not a correctness certification. Retain the legacy policy for evaluation; choose the new default only after comparisons.

## Validation and release gates

1. Ship progress counting and supported UNC handling independently of answer-policy changes. They should not change retrieval relevance.
2. Test the safe compiler independently before changing prompt examples. Keep advanced mode and the existing raw protocol available.
3. Compare the current baseline, prompt-only completion criteria, and effort/finalisation policies separately on identical documents/questions. Keep reranking, thinking, model/quantisation, context and candidate settings fixed within a comparison.
4. Include simple answers, multipart questions, missing information, contradictory revisions, date-sensitive questions, duplicate-heavy results, hyphenated identifiers, phrase/NEAR cases and genuinely necessary long searches.
5. Measure answer correctness/claim support, requested-part coverage, honest unknowns, source association, syntax errors, repeated/nonproductive calls, time to final answer, stage totals and tokens. Faster answers with more false claims are regressions; more searching without improved coverage is also not a gain.
6. Run local process/protocol tests without calling them a model-quality evaluation. Evaluate the intended 27B setup where available; an 8B wiring check remains irrelevant to that quality claim.
7. Preserve source-only publication boundaries: generic code/prompts/docs and the approved metadata demo may be published; source selections, credentials, personal paths, documents, databases, model weights, tests and evaluation artifacts remain excluded.

## Codebase review notes — 27 October 2026

The v1.1/v1.2 code review identified the following implementation dependencies and opportunities. These are design requirements and validation targets, not claims that the proposed features are already implemented.

1. **Persist the evidence checklist explicitly.** `agent.py` rebuilds each request from the question, saved evidence and recent tool results; prior model responses are not retained, and checkpointing clears recent results. A prompt-only checklist can disappear. Define a small host-retained coverage record with evidence references, validation and a token budget before using it to guide stopping. Keep this addition smaller than a general planning state machine.
2. **Reserve usable evidence for finalisation.** Citation validation currently accepts sources exposed earlier even when their excerpts have been evicted or cleared. Reserve a bounded final evidence bundle as well as a final response. Test eviction and checkpointing immediately before finalisation, including whether the last retrieval result reaches the final prompt. Historically valid citation IDs do not establish current evidential support.
3. **Include refresh interruption and scheduling in the progress design.** The GUI currently uses one worker, disables search controls during refresh and enables Stop only for inference. Inventory-first discovery can lengthen the period before processing starts. Add cancellation at safe discovery/processing boundaries and define partial-run reconciliation behaviour. Bring interactive scheduling forward from v1.1; separate workers require separately owned database connections. Questions retain a read transaction across inference, so concurrent refresh must be checked for WAL growth, transaction duration and I/O interference. Native filesystem calls may still delay cancellation.
4. **Enforce the UNC source policy in the application.** Ordinary clipper connections currently attempt direct reads; closed-snapshot handling is opt-in. Specify explicit source modes, rejection behaviour and actionable errors so a URI repair does not implicitly authorise unsafe live remote access. The existing ordinary connection begins a read transaction before counting/selecting clips; preserve that consistency when materialising the proposed bounded spool, then release the remote read promptly.
5. **Compile and normalise search arguments before frozen-pool binding.** `QuestionSearch` binds reranker pagination to serialised arguments. Apply the shared compiler and default normalisation before that binding, preserving filters, ranking scope and relevance questions. Include `catalog(title_query=...)`, which currently accepts raw FTS5, and define compatible GUI, model and CLI simple/advanced behaviour. Test equivalent inputs, omitted defaults and continuation pages.
6. **Candidate retrieval optimisation — parked after user feedback.** Reranking currently gathers candidates through repeated lexical searches of at most 20 passages; 1,000 candidates can require 50 query executions with increasing offsets and repeated provenance work. Benchmark a dedicated bounded candidate-fetch path and delayed/batched location retrieval alongside v1.1's ranking-query optimisation. Preserve qualifying filters, lexical ordering, evidence and pagination semantics; use full-corpus measurements to establish benefit.
7. **Design metadata migrations and durable identity before the panel.** Current libraries accept schema version 1 and return early on an existing schema. Document identity includes extracted segments/page geometry, and unused documents are garbage-collected. Define migration handling, content-version identity, durable source-specific manual corrections, stale-result checks and restart recovery before attaching enrichment/jobs to document IDs. Metadata enrichment remains a separate workstream.
8. **Use one effort-mode result contract across consumers.** Current outcomes include `answered`, `round_limit`, `output_budget_exhausted` and `cancelled`; the GUI specifically recognises `answered` as answer completion. Specify new statuses and stopping reasons together for the agent, GUI, CLI, timing, traces and evaluation. Define whether invalid actions, note operations and checkpoints consume response budgets, and how `--max-rounds` interacts with a selected mode. Test Quick's reserved final response and the display of answers with gaps.

Delivery clarification: keep progress, UNC handling and the compiler independently reviewable. Specify checklist persistence, final evidence retention and result/status handling before evaluating the effort slider. Refresh interruption belongs with the progress work; concurrent scheduling needs its own transaction and responsiveness checks.

Review validation: all 45 existing desktop-search tests passed with temporary files redirected to the workspace. This establishes a local baseline only; real-share UNC integration, full-corpus latency and intended 27B answer quality remain unverified.

## Exploration: relevance, recency and changed conclusions

Status: investigate and evaluate independently; no production default or fixed mixing weight is approved yet. The motivating failure case is a detailed older document dominating retrieval while a short newer notice withdraws, qualifies or contradicts its conclusion.

- **Two candidate routes:** explore a relevance-first pool across all dates plus a recent matching-document pool selected from the corpus before relevance truncation. A 70/30 split within a fixed total candidate budget is an initial experiment, not an established optimum. Merge duplicates, refill unused slots, limit domination by one document and preserve qualifying filters. Compare protected relevant recent slots in the evidence delivered to the model against unrestricted reranking, which could otherwise bury the update again. Freeze the merged ordering for pagination and retain exhaustive lexical access.
- **Reliable dates and provenance:** prefer supported publication dates, with effective dates/reporting periods interpreted separately where available. PDF publication-date coverage is currently incomplete. Keep undated material eligible through the relevance route and disclose coverage and date precision; never silently substitute arrival/capture time. Grouped passages currently inherit a maximum date across matching source occurrences: prevent reprints or newly imported copies from making an old claim appear current. Keep source-specific date attribution attached to evidence.
- **Bounded update check:** for current-status questions, add a checklist need to look for later material that changes the apparent answer before finalisation. Search identifiers/entities and alternate wording so a terse notice need not repeat all terms from an older explanation. Revision/withdrawal terms can supplement searches but must not be required filters. Distinguish explicit supersession, unresolved conflict and differences in scope; newer does not automatically mean authoritative or correct. If no update is found, describe the latest relevant evidence found in the library without claiming universal verification. Account for this check within the selected effort budget and disclose an unresolved check when the budget ends.
- **Question-sensitive controls:** explore Relevance, Balanced and Current position preferences separately from Quick/Balanced/Thorough effort. Historical questions must respect the requested period. Start with mixed retrieval and the update check before introducing calibrated score blending or decay. Raw relevance scores and document ages cannot be treated as interchangeable percentages.
- **Evaluation:** compare baseline, mixed candidates and mixed candidates plus update checks separately, using fixed corpus/model/reranker settings. Include older-detail/newer-reversal pairs, newer irrelevant mentions, unchanged authoritative older material, differing jurisdictions/periods, unknown or imprecise dates, historical questions and reprints. Measure retrieval of corrective evidence, its exposure in the final prompt, correct temporal interpretation, supported answers, latency and fallback rates. Test CPU/GPU costs within the existing bounded candidate budget.

Reranker comparison can support this exploration but must remain a separate variable: retain a reranker-off baseline and compare a small set of compatible single-score retrieval CrossEncoders on identical candidate pools. Record exact model revision, token limits, device, timing and whether reranking actually ran. Relevance rerankers do not independently establish freshness, truth or supersession, and cannot recover a corrective document omitted from the candidate pool.

## Remaining roadmap

Folder-selected production metadata enrichment and organisation/tag normalisation remain separate from this release's stopping/search changes. The metadata demo is not that production workflow. Date-aware ranking is now an explicit exploration above. Larger-library performance work is parked because the user is satisfied with current performance; OCR, semantic retrieval and broader citation-support validation remain outstanding. CPU/CUDA reranking is already implemented; this plan preserves its semantics while simplifying search input and tracking evidence, with candidate mixing subject to separate evaluation.

**Scope decision: full RAM search snapshots are dropped, not deferred.** Retain the disk-backed authoritative index, persistent connections and bounded SQLite page caching. This supersedes the earlier v1.1 RAM-snapshot proposal. Consistent per-question read transactions and supported clipper exports/closed snapshots remain part of database correctness and ingestion; they are separate from a full in-memory search copy.

## Primary references

- [SQLite URI filenames](https://www.sqlite.org/uri.html): authority, escaping and read-only modes.
- [SQLite compile options](https://www.sqlite.org/compile.html#allow_uri_authority): optional UNC URI authority support.
- [SQLite WAL](https://www.sqlite.org/wal.html): shared-memory and network-filesystem restrictions.
- [SQLite FTS5](https://www.sqlite.org/fts5.html): phrase, Boolean, tokenisation and NEAR behaviour.

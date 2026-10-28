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

Selected follow-up work implemented locally: queued between-turn steering including bounded late-answer revision, manual FTS5 plus a separate reranking question, runtime/model diagnostics, separate Thinking display, Chrome/Acrobat page-opening adapters and AppData working libraries with explicit/opt-in snapshot sync and conflict retention. Managed copies use schema 2 for identity/revisions. Metadata extraction/migration and information-date/mixed-ranking work remain planned. Actual network-share and viewer-navigation trials remain pending; see [new controls](LOCAL_SEARCH_NEW_CONTROLS.md).

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

Status: evaluation approach approved on 28 October 2026; implementation pending. Keep Basic as the production baseline while evaluating reserved recent results and a targeted update check. No production default change or optimal mixing weight is established. The motivating failure case is a detailed older document dominating retrieval while a short newer notice withdraws, qualifies or contradicts its conclusion.

### Approved first experiment: Basic and evaluation mode

- **Basic:** retain current retrieval and optional relevance reranking, with newest-first still available separately.
- **Mixed:** for ten delivered results, start with seven selected by relevance and reserve three for newer, relevant documents. Apply an explicit relevance threshold to recent candidates; deduplicate and refill unused slots rather than forcing irrelevant recent material into the answer context. Treat 7/3 as a test setting, not an optimum. Acquire recent candidates before the relevance cutoff and protect the reserved slots after reranking as well as during candidate selection.
- **Mixed plus update check:** use the same mix, then perform a bounded search for later amendments, withdrawals, replacements or decisions concerning the specific older evidence supporting a current-status answer. This is a search for material that could change the answer, not merely recent mentions of the topic. Interpret authority, scope and effective dates before concluding that newer evidence supersedes older evidence.

Provide an evaluation-mode option to compare these three variants against the same questions and corpus, keeping model, reranker and ordinary retrieval budgets fixed and recording the update check's additional work separately. Show Basic and experimental results side by side with source dates, candidate origins and ranking changes. Start with retrieval comparison; make paired answer generation an explicit evaluation option because it adds inference time and variability. Record corrective evidence reaching both the result list and final model context, along with update-check queries, outcomes and budget exhaustion. Keep these controls separate from the answer-effort setting.

The primary evaluation question is: **Did we find and correctly interpret the document that changes the answer?** Also measure retention of useful older detail, irrelevant recent results, answer support and extra latency. Include failures where an update is retrieved but reranking or context selection hides it. Compare ranking policies on identical candidate pools when isolating ranking effects; evaluate the added recent-candidate route separately rather than attributing its gains to ranking alone.

Defer weighted rank fusion, calibrated freshness boosts and relevance-band/date tie-breaking until these first three variants establish whether protected recent evidence and targeted update searches help. Recency is a route to possible corrections, not proof of authority or truth.

### Design and validation requirements

- **Two candidate routes:** explore a relevance-first pool across all dates plus a recent matching-document pool selected from the corpus before relevance truncation. A 70/30 split within a fixed total candidate budget is an initial experiment, not an established optimum. Merge duplicates, refill unused slots, limit domination by one document and preserve qualifying filters. Compare protected relevant recent slots in the evidence delivered to the model against unrestricted reranking, which could otherwise bury the update again. Freeze the merged ordering for pagination and retain exhaustive lexical access.
- **Reliable dates and provenance:** use the explicitly selected date policy: publication dates for the initial baseline, or the versioned Information date resolver once implemented and evaluated. Keep effective dates/reporting periods distinct. PDF publication-date coverage is currently incomplete. Keep undated material eligible through the relevance route and disclose coverage and date precision; never silently substitute arrival/capture time. Grouped passages currently inherit a maximum date across matching source occurrences: prevent reprints or newly imported copies from making an old claim appear current. Keep source-specific date attribution attached to evidence.
- **Bounded update check:** for current-status questions, add a checklist need to look for later material that changes the apparent answer before finalisation. Search identifiers/entities and alternate wording so a terse notice need not repeat all terms from an older explanation. Revision/withdrawal terms can supplement searches but must not be required filters. Distinguish explicit supersession, unresolved conflict and differences in scope; newer does not automatically mean authoritative or correct. If no update is found, describe the latest relevant evidence found in the library without claiming universal verification. Account for this check within the selected effort budget and disclose an unresolved check when the budget ends.
- **Question-sensitive controls:** explore Relevance, Balanced and Current position preferences separately from Quick/Balanced/Thorough effort. Historical questions must respect the requested period. Start with mixed retrieval and the update check before introducing calibrated score blending or decay. Raw relevance scores and document ages cannot be treated as interchangeable percentages.
- **Evaluation:** compare baseline, mixed candidates and mixed candidates plus update checks separately, using fixed corpus/model/reranker settings. Include older-detail/newer-reversal pairs, newer irrelevant mentions, unchanged authoritative older material, differing jurisdictions/periods, unknown or imprecise dates, historical questions and reprints. Measure retrieval of corrective evidence, its exposure in the final prompt, correct temporal interpretation, supported answers, latency and fallback rates. Test CPU/GPU costs within the existing bounded candidate budget.

Reranker comparison can support this exploration but must remain a separate variable: retain a reranker-off baseline and compare a small set of compatible single-score retrieval CrossEncoders on identical candidate pools. Record exact model revision, token limits, device, timing and whether reranking actually ran. Relevance rerankers do not independently establish freshness, truth or supersession, and cannot recover a corrective document omitted from the candidate pool.

## Pre-implementation risk review — 28 October 2026

The proposed staged delivery plan is in [Next implementation plan](LOCAL_SEARCH_NEXT_IMPLEMENTATION_PLAN.md). It includes a schema-version-2 compatibility proposal for review; it does not mark these features implemented or authorise migration of an active library.

An independent delivery stage now covers optional PDF page navigation: a toggle, Chrome and Adobe Acrobat/Reader only, configurable executable paths and fallback to the current default-app opening. Installation paths are not hard-coded; tested launch adapters are.

Review outcome: the direction is viable, but automatic network publication and production metadata/date filtering need the contracts below before release. This review is based on the current code, not an implementation or successful network-sync trial. These clarifications take precedence over broad promises elsewhere in this plan.

### High-risk items and release gates

1. **Lost updates during sync (high; data loss).** A remote fingerprint check followed by replacement has a race; a successful upload also must not clear local changes committed after the uploaded snapshot. There is currently no durable library revision or sync state. Define a library UUID, database generation, committed revision and uploaded snapshot revision. Read the revision from the consistent backup itself; acknowledge only that revision after publication. Serialize cooperating publishers with an exclusive ownership mechanism spanning final baseline verification and replacement. Record an upload transaction ID/hash so a crash after remote replacement but before local acknowledgement can be reconciled on restart. A timed-out lease alone must not allow a second writer to overwrite an uncertain first writer. Gate automatic sync on crash injection at each stage, concurrent local commits, two-client conflicts and successful recovery on a real intended share.

2. **Replacing an open database (high; corruption/stale state).** `gui.py:get_library` caches connections by path/cache settings, and other processes can retain independent connections. Same-path replacement does not refresh those connections. Introduce one library lifecycle coordinator used by GUI, CLI and sidecar. Restore/import/migration requires a maintenance barrier, stopped jobs, released connections and explicit reopen; invalidate displayed result IDs, frozen reranker pools and queued work when generation changes. A late extraction result must check generation as well as content hash. Sync uploads can use a consistent local backup without replacing the working library. The remote is a published, closed snapshot, not a directly queried live DB. Locks coordinate supported clients only; do not promise universal detection of unrelated open SQLite handles. Refuse observed sidecars/locks and document unsupported external writers. Gate on restore with open readers, paused jobs and late completions.

3. **Metadata identity and deletion (high; lost or misattributed corrections).** `store.py:put` replaces source fields on refresh; `prune/gc` deletes sources and unreferenced documents. Document hashes include page/segment layout, and integer row IDs are not durable identities after deletion or restore. Freeze the storage contract before adding tables: library/source UUIDs, content-version keys, versioned extraction results and source-specific manual overlays. Define whether source removal tombstones or deletes each record, how reappearance is handled, and how changing a configured collection's root affects identity. Preserve history without preventing core garbage collection or reattaching corrections to a different document at the same path. Gate on delete/reimport, same-path replacement, duplicate sources, changed extraction layout, source-root changes and restore across generations.

4. **Migration compatibility and dry-run writes (high; library damage).** Keeping core schema version 1 is insufficient if older code can delete or mutate records without respecting new metadata/sync rules. Define reader/writer capability versions and reject unsupported writers; do not claim backward write compatibility without testing it. `Library` can create/maintain schema, so inspect and extraction dry-run must use the read-only access pattern already in `demo_metadata_extraction.py:read_sources`. Dry-run may perform ordinary SQLite coordination but must not change logical DB contents, queue state or sync state. Validate library ownership for every tool and protect source databases and their path aliases from being selected as restore/sync destinations. Gate on migration rollback, backup restoration, foreign-key/integrity checks, representative FTS equivalence, old-client behaviour and dry-run non-mutation.

5. **Information date giving a false sense of currency (high; answer correctness).** A report may have several topic-specific cutoffs and a later publication date. A changed web footer or filesystem timestamp does not establish a substantive revision. Only an explicit whole-document cutoff may become the default document information date; preserve scoped dates separately and leave ambiguous cases unresolved or use the documented publication fallback. A partial revision must not rejuvenate unchanged passages. Fix the existing maximum-across-source-occurrences date aggregation before relying on the new date for recency. Define date intervals, overlap semantics and unknown-date handling consistently across search, catalog, UI, tools and pagination. Gate on reviewed cutoff/publication/update conflicts, partial dates, duplicates, historical questions and known reversals. Keep the current date default until the new resolver passes the pilot.

6. **JSON repair passing incorrect metadata (high; silent factual errors).** The demo validates literal quote occurrence, which does not establish that a quote supports a publisher role or date scope. Its cutoff/reporting-period validation is weaker than publication-date validation, and its fields are hard-coded. Implement one typed field registry and validators for all date/interval fields before enabling writes. Extracted, structurally valid and human-reviewed are distinct states. Formatting repair of unparseable text cannot reliably prove that every value was preserved: retain originals and route uncertain/value-changing repairs to review rather than auto-accepting them. Keep unreviewed/conflicting date suggestions out of authoritative filtering until the acceptance policy is explicit. Test truncation, wrong excerpt association, invalid intervals, duplicate keys, out-of-schema values and repair-induced factual changes.

### Significant implementation challenges

- **Sync volume and dirty tracking:** every `put` currently updates `last_seen` and rewrites title FTS entries, even when content is unchanged; each metadata job may also commit queue state. A full backup/upload after every commit would be expensive for large libraries. Define semantic dirty revisions, coalesce uploads at batch/idle boundaries, and set a maximum pending interval. Record which persistent job/history changes are included. Budget free space for working DB, WAL, backup, staged upload and retained versions; use bounded retention and cancellation. Measure realistic database sizes before enabling frequent automatic sync.
- **Field definition is still an open input:** the user has approved the workflow, not an exact final registry. Ship a clearly labelled editable starter based on the demo, and validate the selected registry before a write run. Do not silently make the demo keys permanent. Database tools operate on application-owned records; arbitrary SQL editing is outside the proposed first release.
- **Pilot scale and repeatability:** the demo loads the selected documents' front/back text and page ranges together and rewrites the complete report after each result. Keep that review format for small dry runs, but make bulk extraction stream bounded work and append per-item artifacts so memory and report-writing costs do not grow quadratically. Freeze the eligible ordered population/seed and source hashes for repeatable sampling. A dry-run on N items is a sample, not a whole-library guarantee.
- **Ranking settings need a precise contract:** the current default returns five results, so 7/3 is an example for ten, not a universal quota. Define rounding and refill for all supported limits, separate candidate-pool allocation from delivered-result allocation, and calibrate a relevance gate for the actual reranker rather than interpreting scores as probabilities. The current worker rejects the entire pool when any pair exceeds its token limit; record this fallback and define an evidence-preserving policy before increasing pools. Keep the update check inside the selected response budget for equal-budget comparisons; optionally report a separate extra-budget experiment with its cost labelled.
- **Shared model resources:** a standalone metadata process can load a second large model and exhaust VRAM while search is active. Start with explicit exclusive model use or a documented unload/handoff protocol. Persistence, cancellation and interactive availability need testing with the intended model; native inference may not stop instantly. No throughput promise follows from the existing mocked tests or a small sample.

### Implementation order and go/no-go checks

1. Freeze library identity/lifecycle, revision tracking, metadata identity and writer compatibility contracts. Define sync states and recovery rules. These are prerequisites for migration and automatic publication.
2. Deliver inspect/backup/validate/restore and AppData import on disposable copies, including read-only dry-run access. Verify local maintenance barriers and restoration before changing a real library.
3. Add migration, field registry and bounded metadata dry runs; then a reviewed small write batch with resume/staleness checks. Keep inference and committed-result writes separate.
4. Implement versioned snapshot publication with manual Sync now first for testing, then enable the approved automatic policy after real-share failure/recovery gates pass. Pending local work must remain usable offline.
5. Add and evaluate the information-date resolver on the reviewed sample; only then connect it to filters and the Basic/Mixed/Mixed-plus-update-check experiment. Evaluate date selection and ranking separately before combining them.

Current evidence: local live clipper ingestion succeeded for 45 clips; the desktop suite most recently passed 72 tests with one actual-share test skipped. These checks cover existing behaviour and the new clipper diagnostics, not the proposed migrations, sync lifecycle, repair ladder or date resolver. No implementation of those new features is claimed by this review.

References informing the storage review: [SQLite backup API](https://www.sqlite.org/backup.html) for consistent snapshots and [SQLite over a network](https://www.sqlite.org/useovernet.html) for network locking/durability limits. Share-specific publication behaviour still requires direct testing.

## Local working library and network synchronisation

Status: approved planning scope on 28 October 2026; implementation pending. Assume one user initially, with one active writer. Support importing a network library into a stable local working database, then synchronising local changes back to the configured network location.

- Store each working library under `%LOCALAPPDATA%/LocalLibrarySearch/libraries/<library-id>/library.db`. Keep a registry of the network origin, local path, last successful import/upload, baseline fingerprint and pending changes. Keep identities stable across restarts and separate libraries even when filenames match. Offer an explicit location override.
- Validate library ownership and compatibility before adopting an imported database. Import from a consistent SQLite backup or a verified closed export; never copy only the main file of an actively changing WAL database. A remote live producer must create a consistent export locally on its own host. Open and validate a temporary local import before replacing an existing working copy, after app/sidecar connections have released it.
- Search, ingest, migrations and metadata tools operate on the local working library. Mark successful content/metadata commits dirty. Detect real database changes using committed change tracking and a consistent snapshot fingerprint; main-file modification time alone misses WAL-only changes and can trigger needless syncs.
- Synchronise automatically after changes settle, outside active transactions, with a manual Sync now control. Produce a consistent local SQLite backup, validate it, upload to a temporary file on the destination share, then replace the published database only after the upload completes. Verify the destination and retain a recoverable prior version; confirm the share supports the required replacement behaviour. Never publish a partially copied file or synchronise live WAL/SHM sidecars independently.
- Before upload, compare the network version against the last imported/uploaded baseline under exclusive coordination for supported publishers. Single-user operation does not exclude edits from another machine: if the remote changed independently, stop and offer explicit local/remote recovery choices; do not merge SQLite bytes or silently overwrite either copy. Refuse detected active ownership/sidecars; do not claim universal detection of unrelated open database handles. Track library ownership for supported app clients and require the published snapshot to remain closed in external tools. Apply the crash/revision rules in the risk review above.
- When offline or sync fails, retain the local database and durable pending-sync state, show the last success and current error, and retry with backoff. Closing the app must not discard pending work. Serialise sync with migration/restore and coordinate sidecar commits. Download remote changes only after checking local dirty state; do not silently replace unsynchronised work.
- Validate initial import, WAL-only changes, metadata-only changes, no-op runs, offline/reconnect, restart, interrupted upload, remote conflict, locked destination, failed validation and recovery. This is a disk-backed local working copy, separate from the dropped RAM-snapshot proposal. Generic runtime/docs may be published; actual libraries, backups, sync state and network paths remain private.

## Next priority: metadata tagging sidecar

Status: core sidecar workflow implemented locally. It includes metadata migration, editable field/prompt profiles, seeded random dry runs, bounded JSON recovery, reviewed-report application, manual overrides, atomic ingest queueing, resumable writes and backup/restore tools. The intended-model quality/throughput pilot remains outstanding. Information-date search integration below remains planned. See [metadata workflow](METADATA_WORKFLOW.md).

The metadata demo was the starting point, but the user reported invalid JSON on some runs and five to ten minutes per document to reach a usable result. Keep search, reranking and ingestion independent. The user defines actual metadata fields/prompts; the demo's hard-coded keys are not the final contract.

### Sidecar and field definitions

- Deliver a separate command/program with its own configuration, progress, cancellation and resumable queue. It reads indexed text and writes application-owned metadata records; ingestion and questions must not automatically start tagging. A separate process does not require a second database: the proposed migration adds metadata storage to the existing local library.
- Let the user define stable field names, descriptions, enabled fields, scalar/list/date types, permitted values, unknown/null handling and evidence requirements. Generate the prompt, output schema and validator from one versioned definition. Separate factual extraction from generated tags. Schema changes must support missing-only, changed-only and deliberate reprocessing without overwriting manual corrections.
- Include editable global and per-field extraction prompts/examples with named profiles and exact-prompt preview. Record the resolved prompt/profile version on queued jobs and results. Add optional missing-only/new-or-changed auto-queueing after successful ingestion, with inference in the separate worker. Migration/backfill of existing items remains explicit and scoped. The user confirmed separate clipper and search-library databases: clipper remains a read-only ingestion source, with no consolidation or metadata write-back in this implementation. AppData import/sync applies only to the search library. See the next implementation plan for details.
- Preserve the v1.1 evidence, provenance, date-semantics and path-boundary selection requirements. Start with selected folders/sources and a pilot, not an automatic all-corpus job. A future desktop launcher/status view may invoke the sidecar without merging its model loop into search or ingestion.
- Separate processes still share GPU/CPU/RAM. Make concurrent model-heavy work explicit, pause at safe boundaries for interactive demand, and do not assume linear parallel speedup or automatic sharing of a loaded model between processes. Local processing remains the default; any hosted document transmission needs an explicit choice.

### Migration for an existing populated library

- Provide an inspect/dry-run mode, validate library ownership, and make a consistent recoverable backup before writes. Verify on a copy before migrating the active database.
- Prefer additive field-definition, metadata-value/provenance and resumable-job tables, with a separately versioned metadata schema. User-defined fields should not require an ALTER TABLE for each tag. Confirm the final storage contract after the initial fields are defined.
- Preserve existing source/document/passage identities, FTS results, dates and extraction cache. The app currently accepts core schema version 1: do not bump it and strand existing readers. Preserve compatible core storage with a metadata version, or ship the necessary reader compatibility together.
- Make migration transactional, repeatable and recorded in an applied-migration ledger. Serialize schema changes against active work; keep normal result commits short and never hold a database write transaction during inference.
- Link extraction to durable source identity, content hash, field-schema version and model/prompt version. Recheck identity before committing, mark changed content stale, preserve manual/source-provided corrections and prevent re-ingestion or garbage collection from misattributing results. Resume successful unchanged jobs without duplicating them.
- Never modify source PDFs or clipper databases. Check migrated counts and representative searches. Migration must not silently queue the whole library.

### Database tools and random-sample dry run

- Build on the existing test harness, which the user considers an acceptable starting point. Provide explicit tools for library inspection/status, migration plan/apply, consistent backup, backup validation, restore, metadata field-definition validation, extraction preview/run/resume, stale/failed-job inspection and review/export of results. Use shared application APIs and validated arguments rather than requiring ad hoc SQL edits. Report the selected library and planned writes before destructive restore/reprocess operations.
- Distinguish migration dry-run (no schema or library writes) from extraction dry-run (real model calls and validation, with no library metadata/job/schema changes). An extraction dry-run must work against an unmigrated owned library by keeping its temporary queue/results outside that database. Save review artifacts only to an explicit local output directory, with model/settings, validation errors, recovery attempts and timings.
- Add an extraction option such as `--dry-run --random-items N --seed S`, scoped by the selected collections/folders and eligibility rules. Sample without replacement, record seed and selected stable IDs/content hashes, and report eligible/sample counts; if fewer than N are eligible, use all eligible items and disclose the shortfall. Default to canonical document sampling so duplicate copies do not dominate; offer source-occurrence sampling when testing source-specific fields, with that choice recorded.
- Show field-by-field proposed values, evidence, unknowns/conflicts and any proposed search-date resolution. Record per-item and aggregate latency, first/final validity, repair frequency and estimated bulk duration. Preserve the sample manifest so a subsequent approved write run can process the same items after rechecking hashes and field definitions. Dry-run success must not silently queue the remaining library.
- Exercise migration idempotence/rollback/restore, unchanged search results, resumability, manual correction protection and dry-run non-mutation in the harness. Retain small deterministic fixtures alongside the random real-library pilot; random sampling alone does not guarantee coverage of difficult date or JSON cases.

### Best available information date for search

Status: approved planning scope; define one derived information-date option for search/filtering and the recency experiments while retaining the original date fields. The goal is the best supported date of the information, rather than whichever timestamp happens to be latest.

- Extract and retain distinct information/data cutoff or “as of” dates, publication dates, supported substantive content-modified dates, effective dates, capture/ingest dates and filesystem modification times. Store origin, evidence, precision, confidence/review state and conflicts for each. An effective date describes when a rule applies and is not automatically when the document's information became current.
- Resolve `information_date` by preferring an explicit, document-wide information cutoff/as-of date. A later evidenced substantive revision may supersede that cutoff for the revised content; retain the original cutoff and make scope clear. Otherwise fall back to a supported content-update date, then publication date. Distinguish a document's own information horizon from dates mentioned in historical narrative or references. Do not use the maximum date mentioned in the text.
- Treat filesystem modified times as a low-confidence fallback only when publication/content dates are unavailable and the user enables that fallback. Copies/downloads can change them. Capture/ingest time is an explicitly labelled last-resort option, not an inferred information date. Keep unresolved/unknown dates visible; never invent day precision for a year/month value. Use precision-aware ranges for boundary filtering and disclose uncertain overlap.
- Preserve all component dates, resolution basis and policy version alongside the derived value. Allow manual correction and date-type selection; show why the selected date was chosen in results/diagnostics. Recompute derived values when evidence, corrections or resolution policy changes, without rerunning inference unnecessarily. Source-specific dates must not be pooled using a maximum across duplicate copies.
- Expose Information date as a search date-field option, use the same resolver in filters and experimental recency selection, and show date coverage/fallback use. Preserve existing publication/added/modified choices. Historical/as-of questions must still account for publication availability and effective dates; an information cutoff alone does not prove a document existed at the requested time.
- Evaluate reports with old data but recent publication, later substantive amendments, cosmetic web updates, misleading file modification times, reprints, competing dates, partial precision and missing dates. Check that newer imports do not rejuvenate old information and that a short corrective notice can still be found through the targeted update check.

### JSON gating and a couple of bounded recovery chances

Code inspection: the demo's `run_trial` currently makes one model call per document, parses/validates it, and records exceptions as document errors. It does not have an automatic regeneration/repair ladder. `GGUF.complete` currently calls `create_completion` without a JSON grammar/schema constraint. This differs from the search agent's retries within its response budget.

1. **Prevent malformed output:** investigate schema-constrained generation on the installed runtime, preserving the actual model template and total input/output cap. llama-cpp-python documents JSON/schema support, but the current completion wrapper needs an explicit compatible implementation. Constraints do not prove factual support or correct date/publisher interpretation.
2. **Gate acceptance:** distinguish JSON syntax, schema/type, truncation and evidence/semantic errors. Validate supported keys/types, dates/precision, allowed values and evidence references, and check factual quotes against original supplied excerpts. Keep validated, unknown and needs-review outcomes distinct.
3. **Bound attempts:** start with at most three calls total per document: initial extraction plus up to two recovery attempts, within configurable time/token limits. A formatting-only failure should go directly to compact repair instead of replaying full-document context. Content/evidence failures may justify one targeted regeneration using only necessary original excerpts and validator feedback. No infinite repair loop.
4. **Final formatting-only repair:** use a fresh conversation containing only the short repair instruction, exact schema, near-valid candidate and precise validation errors. Strip earlier conversation and full-document context. Require JSON only, preserving extracted values, quotes and evidence IDs without adding unsupported facts. Bound output/thinking. Substantially truncated output needs a smaller targeted extraction or an explicit incomplete result; formatting cannot reconstruct missing facts.
5. **Revalidate before writing:** retain original evidence locally, outside the compact repair prompt, and rerun schema and evidence checks on repaired output. Preserve original/repaired output, reasons, fixes, timings and final validation state. Do not silently change values to make a schema pass. Ambiguous/value-changing repairs require review or fail; missing facts remain explicitly unknown. On exhaustion, record the failure and continue to the next document.

### Throughput first, then bulk processing

Measure the five-to-ten-minute duration before attributing it to JSON. Separate model load/reuse, excerpt preparation/input size, prompt processing/first output where measurable, generation including thinking, validation, recovery and commit time. Report first-attempt latency separately from time to a valid/reviewable record.

- Start with bounded front matter, headings and relevant closing publication material. Use a bounded deeper pass only for a concrete missing field. The demo's existing 48,000-token/120,000-character ceilings are not a routine bibliographic-extraction target. Read indexed text without re-extracting PDFs.
- Pilot smaller inputs, bounded outputs and thinking off versus short thinking where supported. Keep field-quality and evidence-association checks fixed; accept faster settings only if quality remains acceptable. Use deterministic/source-provided metadata first where reliable.
- Reuse the loaded sidecar model, skip unchanged successful work and share content-level results across exact duplicates, keeping source-specific corrections separate. Persist queue state after each document.
- Measure median/p95 duration, time spent in retries, first-attempt and final JSON/schema validity, field correctness, unsupported values, unknowns and repair-induced changes. Show throughput and remaining-duration estimates with pauses accounted for.

At the user's reported five to ten minutes per document, serial processing without downtime would take:

| Library size | Five minutes/document | Ten minutes/document |
|---|---:|---:|
| 10,000 files | 833 hours / 34.7 days | 1,667 hours / 69.4 days |
| 15,000 files | 1,250 hours / 52.1 days | 2,500 hours / 104.2 days |

These are arithmetic projections, not a measured whole-library prediction. At eight processing hours per day, calendar days would be approximately three times higher. Valid JSON is a format gate, not proof of correct values.

Review roughly 20–50 mixed documents before a bulk run: long reports, multiple organisations/dates, missing metadata, quote/hyphen cases, duplicates and insufficient extracted text. Acceptance requires repeatable migration, restart/resume, bounded retries, no silent value changes, preserved search/ingestion and an explicit throughput decision based on the pilot. Establish realistic time targets from measurements rather than promising them in advance.

Reference: [llama-cpp-python JSON and JSON Schema modes](https://llama-cpp-python.readthedocs.io/en/latest/#json-and-json-schema-mode). Check the installed API/template path before implementation.

## Remaining roadmap

Folder/collection-selected metadata enrichment now has a separate sidecar implementation, with its intended-model quality and throughput pilot still required. Organisation/tag normalisation remains related follow-up work. Information-date search integration and date-aware ranking remain planned. Larger-library performance work is parked because the user is satisfied with current performance; OCR, semantic retrieval and broader citation-support validation remain outstanding. CPU/CUDA reranking is already implemented; this plan preserves its semantics while simplifying search input and tracking evidence, with candidate mixing subject to separate evaluation.

**Scope decision: full RAM search snapshots are dropped, not deferred.** Retain the disk-backed authoritative index, persistent connections and bounded SQLite page caching. This supersedes the earlier v1.1 RAM-snapshot proposal. Consistent per-question read transactions and supported clipper exports/closed snapshots remain part of database correctness and ingestion; they are separate from a full in-memory search copy.

## Primary references

- [SQLite URI filenames](https://www.sqlite.org/uri.html): authority, escaping and read-only modes.
- [SQLite compile options](https://www.sqlite.org/compile.html#allow_uri_authority): optional UNC URI authority support.
- [SQLite WAL](https://www.sqlite.org/wal.html): shared-memory and network-filesystem restrictions.
- [SQLite FTS5](https://www.sqlite.org/fts5.html): phrase, Boolean, tokenisation and NEAR behaviour.

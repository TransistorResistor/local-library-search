# Local Library Search — next implementation plan

Prepared: 28 October 2026

Status: selected stages implemented; remaining work and integration checks are listed below.

Basis: the approved additions and risk review in plan v1.2.

Selected implementation update: between-turn steering, manual question-based reranking, runtime diagnostics, separate thinking display, optional Chrome/Acrobat page launches and managed local/network libraries are now implemented locally. Managed-library schema 2 supplies library identity/revision tracking. Snapshot sync/recovery has local filesystem regression coverage; actual network-share and installed-viewer navigation checks remain pending. Automatic sync is opt-in and initially off. See [new controls](LOCAL_SEARCH_NEW_CONTROLS.md) for behaviour and limitations.

Metadata follow-up: migration, stable source UUIDs, editable fields/prompts, read-only seeded random samples, three-call JSON recovery, reviewed-report application without regenerating, manual overrides, atomic ingest queueing, resumable jobs and backup/restore tools are now implemented locally. See [metadata workflow](METADATA_WORKFLOW.md). These are functional/protocol checks, not a production-model field-quality result. The real-library pilot remains required before bulk extraction. Managed-library write workers currently require closing the desktop owner; shared GGUF ownership prevents competing large model loads. Full concurrent scheduling, the derived information-date search field and recency mixing remain outstanding.

## Intended result

The app works from a local AppData library, can import and publish a consistent copy on a network share, and provides a separate metadata extraction workflow. The user can define fields, run a reproducible trial on N random documents, review results and apply validated metadata. Search can use a traceable information date and compare current retrieval against recent-result mixing and a targeted update check.

Deliver this in eight reviewable stages. Each stage must pass its own checks before the dependent stage is enabled. Stage 8 is independent of the storage/metadata work and can be delivered earlier. Existing candidate discovery remains unchanged. No bulk extraction, active-library replacement or network publication occurs merely because the code is installed.

## Decisions proposed for this implementation

| Area | Proposed decision |
|---|---|
| Working location | `%LOCALAPPDATA%/LocalLibrarySearch/libraries/<library-id>/library.db`, with an explicit override. |
| Network model | One user; each device works locally. The share holds a closed published snapshot. Conflicting edits stop sync and remain recoverable. |
| Clipper integration | Separate databases, confirmed by the user. The clipper remains a read-only ingestion source; metadata extraction and library sync operate on the search library. |
| Writer compatibility | Migrate the core library to schema version 2 when introducing durable identity/revisions. Existing version-1 binaries already reject a different version, so they cannot silently bypass the new write rules. Update all new readers/writers together. |
| Migration | Explicit apply after a preview and validated backup; transactional and repeatable. Read-only inspection/dry-run continues to support version 1. |
| Metadata process | Separate sidecar, using the same library resolver and maintenance controls as the main app. One inference job at a time initially. |
| Metadata fields and prompts | Editable versioned registry and named prompt profiles, with global instructions and field-specific extraction instructions. User-selected definitions govern a write run. |
| Recovery | At most three model calls per item, within configured token/time limits; original outputs and review state retained. |
| Search dates | Add an opt-in Information date field. Preserve existing date choices and current default until evaluation supports changing it. |
| Ranking | Basic, Mixed and Mixed plus update check, initially available for evaluation. |

The schema-version decision deliberately chooses the coordinated-reader-upgrade option allowed in v1.2. An extra capability table alone cannot stop an old binary that does not read it. Restoring the pre-migration backup is the rollback route to the old app; there is no automatic destructive downgrade.

## Stage 1 — library identity, lifecycle and recovery tools

**Deliverable:** shared library management used by the GUI, command line and metadata sidecar, plus working backup/restore tools on disposable libraries.

1. Introduce a library registry that distinguishes library identity, local working path and optional network publication path. Keep machine-specific paths and sync bookkeeping outside the portable database.
2. Define a persistent library UUID, content revision and a local activation generation. A restore/import changes the activation generation so pending jobs and displayed IDs from the prior database cannot be reused. Forking an independent library creates a new UUID; adopting a newer snapshot of the same library preserves its UUID.
3. Add a library maintenance coordinator. Normal reads/writes use their own connections; restore, import replacement and migration require jobs to stop at safe boundaries, connections to close, and all consumers to reopen explicitly afterwards. Block conflicting maintenance across GUI/CLI/sidecar processes.
4. Add inspect/status, backup, validate and restore commands. Inspect checks ownership/version without creating or upgrading a database. Backup uses SQLite's consistent backup facility. Validation checks integrity, foreign keys, schema and basic FTS consistency. Restore validates a staged local copy before replacement and retains the previous working copy until successful reopen.
5. Invalidate GUI source selections, question state, reranker pools and queued work on library activation changes. Never restore underneath a running answer or metadata commit.
6. Guard library, backup, restore and sync destinations against source databases and filesystem aliases where resolvable. Display the resolved source/destination and planned replacement in maintenance previews.

**Code touchpoints:** new library-management module(s); `store.py`, configuration loading in `__main__.py`, GUI connection ownership and metadata read helpers. Exact module names are implementation choices.

**Acceptance:** backup includes committed WAL contents; invalid/foreign databases are refused; restoration succeeds after simulated interruption; existing connections are released and reopened; late jobs from an old activation cannot write. Tests use disposable libraries. The user can inspect the maintenance preview and recovery result before applying it to a real library.

## Stage 2 — versioned migration and AppData import

**Deliverable:** explicit migration of an existing populated library and a usable local working copy.

1. Define migration SQL and the ownership contract before creating metadata jobs. Add a migration ledger, revision state and durable source identities while preserving existing integer IDs for current search/citation APIs.
2. Store metadata in dedicated tables: field definitions, extraction runs, field values/evidence, manual overrides and job state. Separate content-version results from source-specific values. Define tombstones/history for deleted sources so corrections do not transfer to unrelated replacement files or prevent core garbage collection.
3. Treat source collection/root identity explicitly. Replacing a configured root must not reuse manual corrections solely because relative filenames match. Content-version checks cover re-extraction/layout changes as well as file changes.
4. Apply migrations transactionally after a validated backup, then update the core schema version. New app code supports version-1 inspection and explicit migration; it does not silently upgrade a library merely by opening Search.
5. Import an existing local library using a consistent backup. For a network library, accept a closed, stable export; an actively written remote WAL database needs an export produced on its host. Stage and validate the import locally, then register it under AppData.
6. Route GUI, CLI and sidecar through the resolved local path. Preserve existing explicit local-library configurations and provide a previewed move/import action instead of silently relocating them.

**Acceptance:** counts, representative search results, dates, passages and extraction cache survive migration. Re-running migration is a no-op. Failed migration rolls back. Old version-1 binaries refuse the migrated library. Delete/reimport, duplicates, source-root changes and restore cannot misattribute metadata. Import failure preserves the previous working copy.

**Review checkpoint:** inspect a migrated copy and its comparison report before migrating the user's active library.

## Stage 3 — metadata registry and N-item dry run

**Deliverable:** a separate metadata command/program that builds on the accepted demo and produces reviewable output without changing the library.

1. Define a registry format with field name, description, type, list/scalar shape, allowed values, null rules and evidence requirements. Add editable global extraction instructions, per-field prompts/examples and named profiles for different source collections/document types. Preview the exact assembled prompt and schema before a trial. Store resolved prompt text/hash and profile/registry versions with every run; protect evidence/schema requirements even when user prompts change. Group compatible fields into one extraction call rather than requiring one call per field. Prompt changes mark affected results eligible for deliberate reprocessing without overwriting them or silently queuing the whole library.
2. Provide a starter registry for publisher, publication date, scoped information cutoffs, reporting periods, reference numbers and generated keywords. Add substantive update/effective-date definitions for the date experiment. Clearly distinguish extracted facts from suggested tags. The user can enable, remove or revise fields before a write run.
3. Implement scoped selection by collections/folder boundaries, status and missing/changed eligibility. Filter scope before grouping duplicates. Default to canonical-document sampling; record selected source occurrences so provenance remains reviewable.
4. Add `--dry-run --random-items N --seed S` and an explicit output directory. Freeze a stable ordered eligible population and record selected UUIDs/content hashes, seed, field-registry hash, model and prompt settings. Sample without replacement; disclose any shortfall.
5. Use short read-only snapshots and bounded per-item text loading. Do not create library tables/jobs, mark sync dirty or hold a transaction during inference. A dry run must work on an unmigrated version-1 library using legacy identity plus content hashes for its manifest.
6. Retain the demo's HTML/JSON review experience: proposed values, exact evidence, unknowns, conflicts, evaluated-text coverage, raw output, attempts and timings. Keep incremental per-item artifacts for larger runs rather than rewriting an ever-growing full report after each item.

**Acceptance:** the same unchanged library/seed/selection reproduces the sample; excluded folders never enter it; dry-run logical database contents and revision remain unchanged; all failures are attributed to the correct item. Run deterministic edge-case fixtures plus an initial real-library sample of around 20 items.

**Review checkpoint:** confirm the selected field definitions and inspect the sample report. The number of random items is configurable; 20 is a suggested first trial, not a fixed product limit.

## Stage 4 — bounded extraction, review and resumable writes

**Deliverable:** reliable metadata jobs with explicit quality states and short, protected commits.

1. Separate preparation, inference, parsing, validation and commit. Start with bounded front/back excerpts; a deeper pass must target a specific missing field and stay within the total item budget.
2. Add compatible JSON/schema constraints where supported by the installed model template. Test thinking-enabled and thinking-disabled output explicitly; do not apply a JSON-only grammar across a template's free-text thinking region without a compatible transition.
3. Implement the three-call ceiling: initial extraction; a compact formatting repair or targeted content retry; at most one final recovery. Every attempt consumes the same item time/token allowance. Truncated output gets an explicit incomplete outcome or targeted smaller extraction.
4. Validate every typed date/interval and evidence reference. Quotes must occur in the supplied excerpt, but that alone must not label a factual interpretation verified. Reject duplicate JSON keys and unsupported fields. Preserve original/repaired candidates; uncertain value-changing repairs require review.
5. Store states such as pending, running, extracted, needs-review, accepted, unknown, stale and failed, with documented transitions. Automatically accepted fields need an explicit rule in the registry; ambiguous or unsupported date suggestions remain excluded from authoritative date filters.
6. Before committing, check library activation, source identity, content version and registry version. Commit results and revision together, without overwriting manual or preferred source-provided values. Do not overwrite a later correction with a delayed result.
7. Add resume/retry-failed/changed-only/missing-only operations and review export/apply. Reuse a saved sample manifest for the reviewed batch after checking hashes. Recover interrupted jobs without duplicating completed unchanged work.
8. Start with exclusive inference ownership across the app and sidecar. Show that metadata inference needs the question model released, or provide a controlled unload/handoff. Test cancellation and model-load failure before considering concurrent models.
9. Add optional automatic metadata queueing for selected ingestion sources: off, missing-only or new/changed content. In the successful ingest transaction, record a lightweight, idempotent queue/outbox entry keyed by source/content/profile version; the separate worker runs inference later. A rollback must not leave a job, unchanged refreshes must not duplicate jobs, and deleted content must invalidate pending work. Ingestion remains searchable immediately and never waits for model generation. Persist the resolved profile version so queued work does not silently change when prompts are edited.
10. Migration creates the metadata structures and reports existing eligible items; backfilling existing documents is a separate previewed, scoped action. Support a migration-plus-backfill workflow only with an explicit selection/profile and displayed count. New/changed auto-queueing starts after the configured policy is enabled; it does not imply a full historical backfill. Verify restart recovery between ingest and worker execution.

**Acceptance:** malformed and near-valid JSON, truncation, unsupported facts, wrong evidence, invalid dates, mid-job content changes and process termination all produce bounded, recoverable outcomes. Manual corrections survive refresh and reprocessing. Record median/p95 time, retry frequency and reviewed field quality; the couple-of-minutes target remains a measurement goal.

**Review checkpoint:** a small reviewed write batch, followed by restart/resume and search checks. Whole-library processing is a separate explicit action.

## Stage 5 — network snapshot publication and automatic sync

**Deliverable:** manual sync first for validation, followed by the approved automatic sync policy.

1. Define durable states: clean, local changes pending, preparing snapshot, uploading, verifying, conflict and retry pending. Store the expected remote baseline, snapshot revision/hash and operation ID so restart recovery does not depend on memory.
2. Track committed persistent changes across ingestion, metadata and maintenance. Exclude machine-local sync bookkeeping from dirty detection. Existing `last_seen`/title-index rewrites mean an unchanged refresh currently writes; distinguish content change from bookkeeping and document which persistent job/history updates are published.
3. Build a consistent local backup and read its revision from that backup. Later local commits remain pending. Do not clear all dirty state just because an older snapshot uploaded successfully.
4. Acquire exclusive publication ownership for supported clients, verify the remote baseline, upload to a unique temporary file, validate/checksum it and recheck the baseline before replacement. Keep ownership through publication. Use a recoverable previous version and an operation record to reconcile a lost acknowledgement.
5. Treat the remote as a closed published snapshot. Refuse detected ownership conflicts or WAL/journal sidecars. External writers are unsupported; the app must not promise it can identify every unrelated open database handle. Stale ownership recovery requires checking the interrupted operation, not blindly expiring a lease.
6. Add Sync now, pending status, last successful sync, conflict details and retry. For automatic sync, propose a 60-second quiet period and a five-minute maximum pending interval while online and outside maintenance; batch commits are coalesced. These settings remain configurable and must be tuned against actual snapshot size/transfer time. Never run overlapping uploads.
7. Keep local work usable offline. Retry transient failures with backoff; preserve pending state on exit. If both local and remote changed, offer a previewed choice to retain local, adopt remote or save an independent fork. Preserve both copies before resolving; no automatic SQLite merge.
8. Before any import/backup/upload, estimate required temporary space. Bound retained backup versions and avoid pruning the last verified recovery copy until the new publication is confirmed.

**Acceptance:** test interrupted copy, disk full, lost connection before/after replacement, local commits during upload, two clients using the same baseline, remote external edits, stale ownership, restart and corrupt upload. Repeat relevant cases on the intended network share. Automatic publication stays disabled until these pass; manual successful copying alone is insufficient evidence.

**Review checkpoint:** inspect the sync status/error/recovery flow and real-share results before enabling automatic sync for the working library.

## Stage 6 — information-date resolution and search integration

**Deliverable:** a versioned, explainable Information date option with preserved original dates.

1. Retain publication, explicit cutoff/as-of, substantive update, effective, capture and filesystem dates independently, with scope, precision, evidence and review state.
2. Resolve the document date from an accepted whole-document cutoff when available. A supported substantive revision can supersede it only for the affected scope. Otherwise use accepted content-update date, then publication. Conflicting or topic-specific cutoffs do not automatically become document-wide dates.
3. Keep filesystem modified/capture fallbacks opt-in and visibly labelled. Do not infer a day from year/month precision. Represent uncertain dates as intervals; define definite versus possible boundary matches and test inclusive/exclusive semantics.
4. Preserve source-occurrence provenance. Replace maximum-date aggregation for the new policy so a copied/reprinted source cannot rejuvenate an unchanged passage. If revision scope cannot be mapped to passages, report uncertainty rather than applying a new date to all old text.
5. Add the date choice to GUI, CLI/model tools, filters, result details and traces using one resolver. Materialise/index resolved values where necessary; avoid running model extraction during search. Policy/evidence changes can rebuild derived values without repeating inference.
6. Show date coverage and excluded unknowns when filtering. Historical queries also need publication availability/effective-date interpretation; information cutoff alone is insufficient.

**Acceptance:** reviewed cases cover multiple cutoffs, old data/recent publication, partial amendment, cosmetic update, misleading file timestamps, duplicates, unknown dates and month/year precision. Compare date-only changes separately from ranking changes and preserve existing search-date defaults during evaluation.

## Stage 7 — relevance/recency evaluation

**Deliverable:** Basic, Mixed and Mixed plus update check comparison with reproducible traces.

1. Keep Basic as the comparison baseline. Fetch relevance and recent candidates separately before relevance truncation, using one explicit date policy and the same qualifying filters.
2. Reserve approximately 30% of delivered slots for relevant recent evidence: initially 3 of 10 or 2 of 5, with at least one relevance slot when the limit exceeds one. For a limit of one, return the best relevance result and flag that a mixed allocation is unavailable. Deduplicate, limit one-document domination and refill unused slots.
3. Specify candidate-pool allocation separately from delivered slots. Calibrate the relevance gate against the selected reranker on the pilot; do not reuse a numeric threshold across unrelated models. Log unscorable/oversized pairs and define a bounded evidence-preserving fallback before increasing candidate counts.
4. Protect reserved recent evidence after reranking and through final context selection. Freeze result ordering for pagination and keep exhaustive lexical access available.
5. In Mixed plus update check, search for later changes to the actual supporting document/claim using identifiers and alternate wording. Amendments/withdrawals are useful clues, not mandatory lexical filters. Consume the selected answer budget and report an unresolved check if it cannot finish.
6. Show paired result lists first; make paired answer generation explicit. Compare equal-budget runs, with any extra-budget update-check trial reported separately. Use the same candidate pool when isolating ranking effects, and a separate comparison for the new candidate route.

**Acceptance:** measure corrective-document discovery, exposure in final context, correct interpretation, preservation of useful older detail, irrelevant recent results, latency and fallbacks. Include deterministic known reversals plus the reviewed library sample. Do not promote Mixed to the default solely because it produces newer results.

## Stage 8 — optional PDF opening at the cited page

**Deliverable:** a Settings toggle to open a PDF at its cited page, using a configurable supported viewer and the existing file-opening behaviour as fallback.

1. Add Open PDFs at cited page, initially off. With it disabled, retain the existing registered-default-app opening. With it enabled, offer Chrome or Adobe Acrobat/Reader and a Browse control for its executable, with installation auto-detection for the selected viewer. The user has explicitly limited support to these two viewer families.
2. Hard-code only the tested argument adapters, not installation paths. Discover common installations where practical and allow the user to override the executable location. Persist the selected preset/path; no requirement to change the Windows default PDF reader. Avoid arbitrary executable-argument templates in the first implementation.
3. Resolve the PDF path and physical, one-based page from the actual cited source occurrence/passage location. Never guess a page from model prose, printed page labels or a different duplicate copy. When an answer cites only a source ID without a unique page association, open normally or offer the known passage locations instead of choosing an arbitrary page.
4. Invoke the selected executable with a structured argument list, without a shell. Validate the page and file, and use the registered source locator. The model cannot supply executable paths or launch arguments.
5. Fall back to the existing default-file opening when there is no known page, no supported viewer, a missing executable or a launch error. Log the fallback reason and show a concise status when useful. A successful process launch does not prove the viewer navigated correctly; do not promise automatic detection of a silently ignored page parameter. Missing source files retain the current useful error behaviour.
6. For Chrome, construct a correctly encoded local file URI with a `#page=N` fragment and launch it in Chrome; verify local-file page navigation with the installed browser. For Acrobat/Reader, use its `/A page=N` launch adapter. Test both cold and already-running. Cover paths with spaces/non-ASCII characters and literal `#`/`%`, duplicate sources, invalid or missing page associations, disabled toggle and launch failure. Preserve ordinary HTTP/HTTPS opening for web clips. The PDF's physical page count can differ from printed page numbers; label page references accordingly.

**Acceptance:** toggling the feature off reproduces current opening; configured supported viewers navigate to the verified page in real viewer trials; unavailable viewers fall back gracefully. Mocked launch tests verify argument handling, but actual navigation remains a viewer-integration check.

## Verification and delivery

- Each stage gets focused regression tests and a short report stating what passed and what remains unverified. Existing desktop tests remain the baseline; mocked inference verifies protocol behaviour, not field quality.
- Use disposable copies for failure injection. Existing source PDFs and clipper databases remain read-only. Real-library migration, write batches and network publication have explicit user-facing operations.
- Keep generic source, prompts, examples and documentation synchronised with the publication checkout. Keep test corpora, sample reports, paths, credentials, databases, backups and model weights local under the existing publication rules. Commit/push is a separate delivery action.
- Update documentation and UI labels with each completed stage. Record the migration/version boundary and rollback instructions before distributing a writer-capability change.

## Clipper integration — decision recorded

Decision: retain separate clipper and search-library databases. Do not consolidate their tables or introduce metadata write-back in this implementation. Keep configurable metadata/prompt contracts portable for possible later reuse. AppData import and network publication apply only to the search library; they must not replace or synchronise over the clipper source database.

The following assessment is retained as background for a future consolidation project, outside the current implementation. Inspection of the clipper's `service/db.py` shows clips, assets, tags, annotations, revisions, metadata corrections and a trigger-maintained clip-level FTS index. This app uses canonical documents, source occurrences, passage locations, extraction cache and passage-level FTS. They are not currently interchangeable schemas; the search library's ownership checks correctly refuse an ordinary clipper database.

Three levels are possible:

1. **Shared metadata contract, separate databases (smallest integration):** use the same field/prompt profiles, evidence format and date resolver in both apps. Preserve clip/source UUID mapping and content hashes. Expose optional reviewed metadata write-back through the clipper's supported API/service so its normalisation and update hooks run. Define conflict precedence before write-back; metadata suggestions must not silently replace clipper tags/corrections.
2. **One local SQLite file with separately owned table families (moderate/large):** retain the clipper tables and add namespaced search/metadata tables plus explicit clip-to-source mapping. Coordinate migrations, lifecycle, revision tracking and cache invalidation in both apps; clipper edits must invalidate search/metadata jobs. Retain both FTS representations until parity is demonstrated. Both processes must use the same local working database and library coordinator; whole-file network sync must include clipper commits and coordinate both processes. Snapshot adoption/restore requires the clipper to release its connections too.
3. **One canonical document schema for both apps (largest):** define a common document/source/version model, then migrate both applications' readers/writers, attachment handling, annotation anchors, revisions, normalised dates, tags, search and APIs. Preserve existing clip IDs/URLs and annotation/revision history. Folder PDFs must not become synthetic web clips without a deliberate type/provenance design. This is a separate consolidation project with end-to-end regression work.

For any future consolidation, revisit ownership and writer compatibility across both apps before changing storage. Avoid direct model writes into the clipper's free-form `meta_json` as the sole metadata store: it lacks the proposed typed field definitions, per-value evidence, extraction versions and review/job lifecycle. Existing clipper scope-level `metadata_corrections` also differs from document-specific manual overrides.

## Review choices

The proposed defaults above make the plan executable, but these choices deserve review before their stage is enabled:

1. Core schema version 2 with coordinated app upgrade and backup-based rollback.
2. Initial metadata field definitions and which values may be accepted automatically versus reviewed.
3. Closed network snapshot publication, with conflicts preserved rather than automatically merged.
4. Information date as an opt-in field until its reviewed evaluation passes.

Implementation can begin with Stage 1 after this plan is approved. The early database tools and dry runs provide concrete review artifacts before any bulk extraction or automatic sync.

# Local Library Search — plan v1.1

Date: 26 October 2026
Status: initial low-risk desktop features implemented locally; remaining work is planned. Not yet published as a new release.

## Current implementation status

Implemented: named source links with title/path/date hover hints and external PDF/HTTP opening; bounded, batched live generation display; response and search timing; optional exact search counts; persistent worker-owned SQLite connection with configurable bounded cache; current-schema opens without repair writes; explicit 65,536-total / 8,192-output context preset.

Ingestion now has live phase/file/count feedback, a one-pass folder activity indicator, clipper row totals, and terminal plus rotating local failure logs. Scheduling and ingestion semantics are unchanged.

Question timing now includes click-to-visible-final-answer completion and non-overlapping stage totals across all rounds, exposed in the GUI and local trace.

The search system prompt now lives in a named section of editable `prompts.txt`, with configurable overrides and per-question loading/hash provenance. A metadata draft is included but inactive.

The live pane labels both thinking and action JSON as unvalidated. It is separate from the final answer. The 64k preset is opt-in and does not change notebook or tool-result budgets. Exact model-token enforcement includes chat-template formatting and the entire output reserve.

Still planned: measured full-corpus query optimisation, concurrent refresh scheduling, separate thinking/action presentation, page-specific external navigation, full RAM snapshots, and folder-selected metadata enrichment. Organisation/tag alias alignment, reference/endnote-aware evidence extraction and date-aware reranking remain future optional work. Unknown values, provenance and partial coverage must remain visible when these are introduced.

## Purpose and reasons for this release

Make the existing native desktop application more responsive, make its answers easier to verify, and add optional metadata enrichment without making ordinary ingestion depend on an LLM.

The reported working library contains approximately 15,000 files and 50 million words. Direct manual FTS5 searches can take more than three to four seconds. That is an interactive-search problem independently of answer generation: the user observed it using the FTS5 search control, without asking the model a question. Query cost, queueing, disk access and background refresh interference need to be measured separately before selecting an optimisation.

Answers currently display numeric source IDs rather than convenient file links. This makes checking a quotation unnecessarily laborious even though the library already stores source paths, titles and passage locations. Model questions also provide little visible feedback until a complete response arrives, making useful work difficult to distinguish from a stalled operation. Timing information is needed both to explain that wait and to measure whether changes help.

Publication and information dates, publishing organisations and report identifiers would improve browsing, filtering and interpretation. They are inconsistently available in source files, and a file's recent arrival does not mean that its information is recent. The user wants optional model-assisted completion of these fields during slow time, restricted to selected folders, while preserving immediate FTS5 availability.

The same GPU drives the display and inference. Keep the Tkinter interface lightweight, bound background work, and avoid loading an additional model or reserving large amounts of memory implicitly.

## Existing foundation and boundaries

The desktop app already provides a persistent SQLite FTS5 library, webclipper ingestion, recursive PDF ingestion with text-quality checks, hash-based extraction reuse, document/passage duplicate grouping, source dates, direct GGUF loading, a bounded search-and-answer loop, and K/V-cache controls. Each question reads a consistent database snapshot. These are the starting point, not new v1.1 deliverables.

Implement one feature at a time and check it against the current behaviour. Keep lexical search, source evidence and the existing context cap as the foundation. Improvements in latency, convenience or metadata coverage do not by themselves establish improved answer correctness.

No automatic source-file changes, OCR requirement, model download or cloud enrichment is introduced. The user subsequently authorised an optional larger context cap; the implementation exposes a 64k preset while preserving existing defaults. Generated metadata and operational logs remain local application state. Published source releases continue to exclude personal paths, credentials, documents, databases, model weights, demo data and evaluation scripts/artifacts.

## Priority and dependency order

| Order | Work | Why now | Initial scope |
|---|---|---|---|
| 1 | Direct-search measurements and optimisation | Manual FTS5 latency is an observed problem. | Separate queue, connection, query and result-processing costs; optimise the measured bottleneck. |
| 2 | Clickable, named citations | Users need to check the actual supporting file easily. | Host-resolved source names and external file/URL opening. |
| 3 | Streaming and LLM timings | Long silent waits obscure progress and performance. | Throttled output/progress events, elapsed time, first-output latency and generation rate. |
| 4 | Configurable database caching | A large working set may benefit from more RAM. | Persistent connections and bounded cache/mapping experiments after query changes. |
| 5 | Folder-selected metadata enrichment | Better metadata is useful but must not delay ingestion. | Resumable opt-in queue, limited fields, evidence-backed extraction and idle scheduling. |
| 6 | Optional full RAM snapshot | Only justified if disk access remains a material bottleneck. | Conditional experiment, not a required v1.1 feature or default. |

Streaming and timing should share the same event mechanism. Initial timing instrumentation can be added before streaming so the latter has a measured baseline.

## 1. Direct FTS5 search performance

### Reason and evidence

The reported three-to-four-second delay concerns ordinary lexical searches. Code inspection found an exact count of all matching passages before every result page, source/date subqueries, metadata queries for individual hits, connection-time maintenance, and sorting that can use a temporary B-tree. These are plausible contributors, not a measured diagnosis of the user's complete library. Automatic refresh also reads every PDF to compute its hash; the GUI currently serializes work, so a search can wait behind ingestion.

### Assessment and proposed work

1. Measure user-perceived time from submission to displayed results. Separately record queue wait, connection/setup, matching/counting/ranking, source metadata retrieval and result rendering.
2. Record corpus passage counts, database bytes, storage type, available RAM, refresh activity and relevant SQLite settings alongside measurements. Distinguish cold and warm searches.
3. Use representative rare phrases, ordinary multiword searches, broad/common terms, Boolean/proximity searches, date-filtered searches, newest-first searches and paginated results. Check empty and syntax-error cases too.
4. Move migrations, schema setup and title-index repair out of ordinary query connections. Reuse thread-owned search connections where practical.
5. Make exact total counts optional or explicitly requested. Return a first page plus a truthful continuation/has-more indicator without always counting the full match set.
6. Compare the actual FTS5 hidden `rank` column with the current explicit BM25-plus-sort query. Renaming an expression to `rank` is not the same optimisation. Preserve ranking/filter semantics and test tie/pagination behaviour.
7. Select qualifying results before fetching bulky provenance where possible. Batch metadata retrieval and avoid calculating dates for discarded candidates unnecessarily.
8. Separate interactive search scheduling from refresh work. Investigate bounded write transactions and I/O interference. Preserve strict SHA256 checking as an option; assess a faster size/mtime change screen plus scheduled full verification as an explicit alternative with different detection guarantees.
9. Evaluate index maintenance and storage-layout changes only after profiling. Do not silently rebuild a large index or compact it on the interactive search path.

### Acceptance

- Report before/after first-page latency, median and p95 for repeated queries, including queue wait. Use the same corpus and machine for comparisons.
- Set numerical latency targets after measuring that baseline; aim to remove routine multi-second waits, without promising a universal sub-second result for every broad query.
- Preserve required terms, exclusions, phrases, NEAR, source/date filters, duplicate grouping and access to the complete matching set.
- Apply filters correctly before selecting qualifying top results. Do not silently take an unfiltered shortlist and lose eligible results outside it.
- Exact-count requests remain available; omitted counts are labelled unavailable rather than reported as exact or zero.
- Improvement claims distinguish faster execution from reduced retrieval scope or changed ranking.

## 2. Clickable source names and original-file access

### Reason

Numeric IDs are useful internally but inconvenient when verifying a quotation or finding its original file. The application already has most of the required metadata, so this is a relatively contained usability improvement.

### Proposed behaviour

- Keep stable source IDs in the model protocol. Resolve display labels and destinations in application code, rather than asking the model to generate paths or URLs.
- Display a document title or filename, with page information when the cited evidence identifies it. Disambiguate identical filenames with a parent-folder label or another concise source attribute.
- Add a clickable source list beneath an answer. Inline citation links can follow where model markers identify the source unambiguously; do not guess which source supports an unmarked sentence.
- Open local PDFs in the operating system's configured PDF viewer. Opening the correct file is the basic feature; page-specific opening is viewer-dependent and must have an ordinary-file-opening fallback.
- Open a webclip's original HTTP/HTTPS URL when no original file exists. Keep indexed-text inspection available inside the app.
- Resolve destinations from the trusted source registry and allow only supported local-file/HTTP(S) actions. Never execute model-generated commands. Show a useful message if a file has moved or disappeared.
- A repeated passage may have several source locations. Let the user inspect the alternatives and do not present copies as independent corroboration.

### Acceptance

Check local paths containing spaces and non-ASCII characters, duplicate filenames, multiple cited pages, webclips, missing files and invalid source IDs. Labels must resolve to the cited source, not a similarly named file. Citation validity and successful file opening do not establish semantic support for the claim.

## 3. Streaming, progress and LLM response measurements

### Reason

The current model call waits for a complete response, leaving the UI largely silent. Streaming helps users see progress and notice unproductive loops; measurements explain where time goes. Neither feature inherently increases inference speed.

### Proposed behaviour

- Consume the runtime's generation stream while preserving the complete output for the existing action parser.
- Use worker-to-UI events. Batch screen updates roughly every 50–100 ms rather than redrawing for every token.
- Show model-emitted thinking, when available, in a separate collapsible pane. Models/templates differ: thinking is not always emitted as a separate stream or preceded by an opening tag in generated text. Handle boundaries split across chunks and avoid displaying tool-action JSON as a finished answer.
- Display explicit phases such as loading model, processing prompt, generating, searching and reading a source. When thinking is off, progress reporting still works.
- Execute a tool only after its complete response has passed validation. Preserve cancellation, truncation handling and the existing input/output token cap.
- Keep the display buffer bounded. UI streaming history must not be silently reinserted into model context.

### Measurements

| Metric | Meaning |
|---|---|
| Total time to answer | Submission through completion, including queueing and tools. |
| Model load time | Cold loading, reported separately from normal warm inference. |
| First-output latency | Time from a generation request to the first emitted text; distinguish from the total user wait. |
| Per-round timings | Prompt preparation/evaluation, generation and subsequent tool work where measurable. |
| Generation tokens/second | Generated tokens over generation time; label an approximation if runtime counters are unavailable. |
| Work counts | Model rounds, searches, retrieved pages and generated tokens. |

Use a monotonic clock. Count thinking and action tokens as generated work. Do not call tokens divided by total question time the model's decoding rate; that mixes disk/tool waits and prompt processing into the result. Prefer reliable runtime counters, otherwise keep a clearly labelled, simple approximation. Do not add a brittle dependency on private runtime internals solely for a more precise speed number.

### Acceptance

Check thinking on/off, split tags, cancellation, output exhaustion, malformed actions and multiple tool rounds. Streamed and non-streamed assembly should produce equivalent final parser input. UI updates must not materially reduce generation throughput or increase the context allocation.

## 4. RAM and database-cache options

### Reason and practical recommendation

A large passage library can benefit from a larger working set in RAM, but query computation can remain slow even with all pages cached. The actual database size and available memory determine feasibility; 50 million words or the total PDF size is not enough to calculate the requirement.

Start with a disk-backed authoritative database, persistent search connections and a configurable bounded SQLite page cache. Assess memory-mapped reads as a separate measured option. Mapping a file is not a guarantee that the entire file resides in physical RAM, and SQLite documents cases where mapping does not improve performance.

Show configured versus observed memory settings where available. Account for cache budgets per connection/database and for total system RAM used by the model, CPU-offloaded weights, extraction, Windows and other applications. K/V cache and database cache are different controls. Avoid increasing memory until the system begins paging.

### Conditional full RAM mode

- Use SQLite's backup/snapshot facilities to construct a consistent in-memory search copy. Do not copy only a live database file while ignoring its WAL.
- Keep disk authoritative. A RAM snapshot is read-only, replaceable and explicitly versioned; ingestion/enrichment writes continue to disk.
- Expose loading time, estimated/observed memory use and snapshot age. Refresh or swap snapshots between questions, preserving a consistent source/version mapping for each active question.
- Copying the whole current database also copies document text and extraction-cache tables, not only the inverted index. A smaller search-only snapshot requires deliberate schema/dependency design; it is not a free configuration change.
- Provide a reload/discard control and a disk-search fallback. Do not create a second full copy per question or promise transparent live synchronization.

### Acceptance and decision gate

Compare disk/cache/mapped/RAM variants on identical queries, both cold and warm, with startup/reload costs and memory recorded. Adopt RAM snapshots only if the benefit justifies extra memory and synchronization complexity. Otherwise retain them as deferred work.

## 5. Optional metadata enrichment for selected folders

### Reason

Better bibliographic and temporal metadata would help users find relevant reports, distinguish publication from arrival, and assess whether information is current. Model processing is far slower and less certain than lexical ingestion. Therefore it must be a separate, opt-in, resumable process, not an ingestion prerequisite.

### Fields and semantics

| Field | Initial representation and rules |
|---|---|
| Publishing organisation | Organisation responsible for issuing the document, with the role recorded. Do not equate it automatically with author affiliation, website host or document subject. |
| Publication date | Supported date plus raw wording and year/month/day precision. Never substitute file capture, filesystem creation, PDF creation metadata or enrichment time. |
| Information date / reporting period | Separate `as_of` value or period start/end, with its meaning retained. A document can contain several; preserve ambiguity instead of selecting the newest visible date automatically. |
| Reference identifiers | A list of typed values, such as report number, DOI or ISBN. Preserve the exact identifier and supported association. |
| Keywords | Bounded suggested terms, labelled generated unless explicitly supplied by the source. Keep them separate from evidence text. |

Prefer existing source metadata and inexpensive deterministic extraction where reliable, then use the model to fill gaps. These passes can also run after ingestion. A source-supplied normalized date may still need review; record its origin rather than silently treating it as independently verified.

### Source evidence and storage

- Store enrichment in separate application-owned records, linked to document identity and content hash. Ordinary re-ingestion must not erase it.
- For factual fields, retain the raw extracted value, normalized value, supporting quote/page or source range, field status, model/prompt version and processing timestamp.
- Distinguish source-provided, manually reviewed, model-extracted, inferred, unknown and conflicting values. A model's confidence label is not a calibrated probability or proof.
- Preserve manual corrections and source-provided values. Conflicts are reviewable; do not silently overwrite them with a later model suggestion.
- Never invent missing values. An absent publisher/date/reference is a valid result, not a reason to search prior knowledge for a plausible answer.
- Validate schema, identifier/date formats and actual occurrence of quoted evidence. Literal occurrence alone does not verify the publisher role or date interpretation.
- Share content-level extraction across identical documents where appropriate, but keep capture dates, paths and source-specific corrections separate.
- Mark enrichment stale when source content changes. Check the hash again before committing a job result.

### Folder selection and user controls

Add a native metadata panel with folder/subfolder inclusion, exclusions, selected fields, and a preview of matching documents before queueing. Folder membership must follow actual path boundaries, not a loose text prefix. Provide missing-only, changed-only and explicit reprocess modes. Start with folder PDFs; selection of webclipper collections can be a later extension rather than an implicit all-corpus operation.

Show pending, running, completed, unknown, stale and failed counts. Support pause, resume and cancellation between safe boundaries, with persistent progress across restarts. Offer a small review batch before a large run, together with measured throughput and an estimated remaining duration.

### Scheduling and cost control

- Commit ordinary text ingestion first so FTS5 search works immediately; queue enrichment separately. No LLM call may block a file becoming searchable.
- Read indexed text instead of re-extracting PDFs. Begin with bounded front matter/headings and relevant closing material; use a bounded deeper pass only when warranted. If required pages were rejected during PDF ingestion, report insufficient evidence rather than pretend they were read.
- On a shared GPU, prefer reusing the selected loaded model through a single scheduler. Background work checks for interactive demand between documents and at supported cancellation boundaries. Low operating-system priority alone does not prevent GPU contention.
- Give interactive questions priority and keep direct FTS5 search available. Native model loading/prompt evaluation may not be instantly interruptible; expose that limitation and bound per-job input/output.
- Claim a job, release the database transaction, run inference, then commit a short validated result transaction. Do not hold a write lock during generation.
- Set per-document limits, bounded retries and explicit failure states. No infinite repair loop or hidden network/model download. Local processing is the default; any future hosted mode requires an explicit user choice about document transmission.
- Run a pilot to establish actual speed and field quality. For scale only, 15,000 documents at an illustrative ten seconds per document is roughly 42 serial hours; this is not a performance prediction.

### Search integration

Expose origin and review status beside enriched fields. Let the user select the relevant date type. Generated metadata is a retrieval aid, not new source evidence or authority. Do not exclude all unprocessed documents from ordinary searches or quietly treat missing generated metadata as a negative finding. Any explicit filter over partially populated metadata must disclose that coverage limitation. Keep keyword expansion opt-in and preserve raw FTS5 semantics.

### Acceptance

- FTS5 ingestion works with enrichment disabled, paused, unavailable or failed.
- Selecting one folder cannot queue unrelated paths; jobs resume without duplicating successful unchanged work.
- Updates/deletions, duplicate files and source-specific corrections do not produce stale or misattributed metadata.
- A reviewed mixed sample checks each field separately: correctness, missingness, conflicts, date precision and evidence association. Include multiple dates, multiple organisations, filenames with misleading dates, scans and documents without reference numbers.
- Review queue behaviour while a user searches or asks a question on the same GPU. Record foreground latency and enrichment throughput.
- Source files and source databases remain unchanged. Partial metadata coverage and unknown values remain visible.

## Other roadmap work and its current status

| Item | Status and recommendation |
|---|---|
| Optional local CPU reranker | Implemented in the earlier evaluation harness, not integrated into the desktop app. Retain an opt-in desktop mode, a local model-folder picker and bounded candidate scoring as follow-on work. Weights, tokenizer and configuration are supplied separately; weights are not published with the source. Profile FTS5 first. |
| User-supplied alias dictionary | CSV ingestion and scoped suggestions exist in the evaluation harness. Desktop import/preview/lookup remains outstanding. Do not merge ambiguous groups or silently rewrite required/excluded terms. |
| Structured query assistance | A safer way to build quoted phrases, alternatives and exclusions remains pending. Raw FTS5 should stay available. |
| Local 27B / weight- and KV-quantisation checks | Still needed to establish actual local speed, memory and quality. An 8B loading check does not establish 27B answer performance. |
| Memory/completion variants | External answer fields and other experimental policies exist in the harness; they are not equivalent to the desktop's simple note loop. Do not migrate complexity automatically. Successful-page cache/retry-guard policies previously regressed and stay disabled unless separately justified. Database page caching in section 4 is a different mechanism. |
| Broader answer/citation validation | Passage exposure and valid source IDs do not prove entailment. Mixed full-document, revision/conflict and genuine missing-information checks remain useful before stronger reliability claims. |
| Semantic retrieval | Deferred optional experiment for vocabulary mismatch, not a prerequisite for v1.1 or a proposed cure for slow lexical queries. |
| OCR and Chinese segmentation | Deferred extensions. The current ASCII-oriented PDF quality gate and default tokenizer do not provide these capabilities. |
| Fuzzy duplicates and document summaries | Deferred. Exact/grouped passages remain the baseline. Metadata enrichment does not imply generating summaries for every document. |

## Delivery and review rules

Deliver independently reviewable changes in the priority order above. Record each feature's default, measured effect and known limitations. Preserve the previous behaviour as a comparison or rollback option where useful. Review search correctness alongside speed; review metadata evidence alongside coverage. Avoid combining a new ranker, new memory policy and new metadata expansion in one quality comparison.

Update the setup guide for new controls and migrations. Keep source-only publication boundaries in place. Tests and benchmark artifacts may be retained locally but are not added to the public source release under the existing publication instructions.

## Primary technical references

- [SQLite FTS5 ranking and sorting](https://www.sqlite.org/fts5.html#sorting_by_auxiliary_function_results): the hidden rank column can improve limited-result sorting; validate the actual query plan.
- [SQLite page-cache settings](https://www.sqlite.org/pragma.html#pragma_cache_size): connection/database cache sizing and its scope.
- [SQLite memory-mapped I/O](https://www.sqlite.org/mmap.html): potential benefits, limitations and platform considerations.
- [SQLite online backup API](https://www.sqlite.org/backup.html): consistent database snapshots, including in-memory use cases.

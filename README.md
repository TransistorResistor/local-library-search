# Local Library Search

Roadmap: [plan v1.2 — counted progress, UNC access and search completion](PLAN_V1_2.md). Previous scope: [v1.1](PLAN_V1_1.md).

New controls: [between-turn steering, manual reranking, diagnostics, Chrome/Acrobat and managed libraries](LOCAL_SEARCH_NEW_CONTROLS.md).

Metadata sidecar: [custom fields/prompts, random dry runs, migration, reviewed writes and JSON recovery](METADATA_WORKFLOW.md). Start with a dry run before applying metadata to a working library.

This native desktop program indexes a webclipper SQLite database and PDF files anywhere beneath configured folders. It keeps its own SQLite FTS5 library. You can search directly without a model, or load a GGUF inside the program and ask questions in ordinary language. Sources and inference stay local; no OpenRouter call is made by the desktop program. A command-line interface is also available.


The main screen has a resizable split: library results on the left and an Answer / source reader on the right. Select a result to preview its existing excerpt without another search; double-click for surrounding context. The Activity and Timing tabs share the reader space, so detailed diagnostics do not squeeze the answer. Search options (input mode, dates, ordering and exact counts) expand when needed. Enter searches; Ctrl+Enter asks the model. Settings are grouped into Library & sources, Local model, Reranker and Preferences, with Save settings and Refresh index always available at the bottom. Tables have scrollbars for long lists and paths.

Detailed model-facing tool instructions, JSON examples and rejection recovery: [Local search tool guide](LOCAL_SEARCH_TOOL_GUIDE.md).

## Refresh and answer controls (v1.2)

Refresh now discovers candidates first, then processes a fixed manifest with one percentage across all sources. The count includes cached, skipped and failed candidates; it measures attempts, not newly indexed documents or estimated time remaining. New arrivals wait for the next refresh. Finalisation/commit is shown separately. A failed/incomplete source remains visible and does not trigger deletion reconciliation. Empty sources complete explicitly. Larger manifests spill to local scratch storage.

**Stop operation** cancels refresh as well as inference. Cancellation is checked between discovery entries, hashing chunks and isolated PDF extraction polls; blocked native filesystem calls may take longer to return. Completed PDF updates remain committed, unfinished folder scans do not prune, and cancelled clipper processing rolls back that collection. Search remains on the existing single worker; stop a long refresh to return to interactive searches.

For network/UNC or mapped-drive clipper sources, explicitly choose **Closed snapshot** after stopping/checkpointing the producer, or set `network_mode: "export"` for an atomically published completed export. Both workflows verify a private local copy and refuse WAL/journal sidecars or a changing main file. Live remote WAL access is unsupported. The authoritative search library must remain local. Local-source SQLite opens stay read-only; connection failures record the SQLite build options for diagnosis. Do not use the browser's file URI as a SQLite connection filename. Actual share availability and permissions still need local validation.

The GUI **Search input** selector defaults to **simple**: whitespace words are ANDed, quoted phrases stay together, and punctuation such as `ABC-123` is literal. Straight and typographic phrase quotes are accepted; unmatched quotes produce an error. Doubled quotes escape a quote within a phrase. **advanced** preserves FTS5 expressions without silent repair. Compiled FTS5 is shown with results; continuation pages retain the submitted query and filters. Search help provides unordered proximity examples such as `NEAR("critical minerals" "supply chain", 10)`. Matching is still token-based, not character-exact. CLI search/catalog remain advanced by default; pass `--input-mode simple` for literal input.

Model searches also accept structured literals, for example `{"action":"search","all":["export controls"],"any":["ASPI","Australian Strategic Policy Institute"],"exclude":["draft"],"near":{"terms":["critical minerals","supply chain"],"distance":10}}`. Omit unused fields, require a positive clause, and do not mix these fields with raw `query`. Title searches accept `input_mode:"simple"`. Filtering, reranking and exhaustive access retain their existing semantics.

For new completion policies, clear **Legacy policy (comparison)** and choose the Quick/Balanced/Thorough slider position. These allow 6/12/24 model responses in total, including an answer-only final response. All responses, including invalid actions and notebook operations, use budget. The CLI supports `ask ... --effort balanced`; `--max-rounds` overrides the total budget including finalisation. Save the GUI choice or set `agent_options.effort` in configuration. Existing settings default to `legacy` until model-quality comparisons justify a change.

In the new modes the model maintains a small public evidence checklist using optional `coverage` on any action. Entries contain `need`, `status` (needed/supported/conflicting/unresolved), and delivered `evidence_ids`. Supported/conflicting needs require retrieved source evidence. Coverage and its referenced excerpts survive checkpoints within the total context cap. Three consecutive retrieval calls adding no new evidence produce a warning, not an automatic claim that information is absent. Note/checkpoint calls do not themselves count as failed retrieval. The live pane and trace show assessed coverage; the summary records effort and stopping reason. This is not factual verification or calibrated confidence.

Finalisation accepts only an answer and validates citations against currently available evidence. Unresolved/conflicting needs are shown below the answer. Results distinguish `answered`, `answered_with_gaps`, `cancelled`, and `incomplete_output`; `stop_reason` identifies budget exhaustion separately from completion. Truncated or malformed final output does not start another tool loop. Legacy retains the earlier completion loop, including `round_limit` and `output_budget_exhausted`. The generic prompt library contains updated safe-search examples; use a frozen prompt file as well as Legacy for an exact historical prompt comparison.

Broader search-performance tuning is parked following satisfactory user feedback. Recency/relevance candidate mixing remains experimental and is not enabled by these controls.

## v1.1 desktop updates

- **Source links:** search results, expanded passages, document pages and model answers include named source links. Hover over a link for the full title, registered path/URL and available source dates. PDFs open in the default external viewer; webclips open their original HTTP(S) URL. Page-specific viewer navigation is not included. Missing files produce an explanation.
- **Live generation:** open the **Activity** tab to inspect thinking and action JSON in a separate, bounded pane. This is unvalidated model output, not the final answer. Tools execute only after the complete action is validated. Updates are batched; the pane retains at most 32,000 characters and 1,000 lines and is never fed back into context. It may contain source excerpts.
- **Measurements:** direct search reports queue, connection, count, ranking, metadata and render time. Model answers report model loading, model rounds, generation-request first-output latency and an approximate last-round decoding rate, excluding the first emitted token. **Displayed in** includes the full GUI operation, loading and tools. Approximate generated-token counts include thinking and action JSON; these are not native runtime performance counters.
- **Search totals:** the GUI skips the full exact count by default, retaining ordinary ranking, filters and pagination. Select **Exact total** when required. CLI searches retain exact counts; add `--no-count` to skip them. An omitted count is `null`, never an invented total.
- **Database cache:** **Settings → Preferences** exposes a 1–1024 MiB SQLite cache budget per connection, default 32 MiB. The GUI reuses one worker-owned connection until its database/cache configuration changes. This is a suggested page-cache maximum, not preallocated RAM or a full in-memory index. Current-library opens no longer run schema setup or a full title-index repair scan. Refresh and inference still share the existing serial worker, so ingestion can delay an interactive operation.
- **64k context:** the preset sets total context to **65,536 tokens** and output reserve to **8,192**, leaving at most **57,344 prompt tokens** after chat-template formatting. Thinking, action JSON and answer generation share the output reserve. Model/runtime support and sufficient KV-cache memory are required. Defaults and existing configurations remain 12,000/2,000; choose the preset explicitly. The larger cap retains more recent evidence before eviction; the notebook limit is configured separately (6,000 tokens by default), and tool-result sizes remain bounded.

Optional configuration at the top level:

```json
"database_options": {"cache_mb": 32}
```

Inferred metadata filtering, synonym rewriting and full RAM snapshots remain planned. Optional reranking is described below. Folder-selected LLM enrichment remains a separate planned feature. Existing added/modified/publication dates are displayed as stored; added dates do not establish information recency.

### End-to-end question timing

For model questions, the summary shows **Click → final answer complete**. The clock starts at entry to the Ask button handler, before settings/configuration work, and ends after the final answer and named sources have been inserted and Tk has processed idle redraws. Cancelled or incomplete runs are labelled **Click → stopped**, never completed answers.

Open the **Timing** tab for non-overlapping totals: settings/UI setup, worker queue, model load/reuse, database connection, model prompt formatting, model inference, model response processing, FTS searches, catalogue lookups, context expansion, document reads, notebook operations, other tools, context budgeting/validation/source resolution, worker bookkeeping/initial trace save, UI delivery/polling, and final-answer rendering. Repeated operations are accumulated across all rounds; failed tool calls are included. These stages sum to the end-to-end total, subject to floating-point/rounding precision.

Model inference includes native prompt evaluation and generation; the app does not claim to measure those separately. First-generation-request-to-first-output latency is an additional diagnostic already included in the stages, not another duration to add. Background live-output redraws overlap worker execution and are not added a second time. The existing approximate decoding-rate diagnostic remains available.

The local `last-gui-search.json` trace includes `gui_timing` with the complete breakdown, plus per-round model and per-tool timings. Final trace persistence runs asynchronously after visible answer completion and is excluded from that total. The initial trace save is included in worker bookkeeping. Timing-label rendering and opening a source viewer are outside answer completion. Direct FTS searches retain their detailed query-phase measurements and **Displayed in** summary.

Rejected actions are explicit in the question trace: each attempted tool entry has its model `round` and a `status` of `completed` or `rejected`. Rejections include `rejection.stage` (`parse`, `validation`, or `tool`), `error_type`, and `reason`, alongside the existing `submitted_action` and `result.error`. If parsing fails, `model_response` preserves the original response. These diagnostics are trace-only and do not consume model context. The accepted final answer remains in the top-level result; it is not an additional tool entry.

### Live ingestion feedback and failure logs

Refreshing the index shows the active source, current filename, phase, elapsed time and processed/indexed/cached/extracted/skipped/error counts. Phases include database opening, folder discovery, hashing, PDF extraction, indexing and deletion reconciliation. The status remains visible while extraction is running; a long PDF may take up to the existing 90-second worker timeout. A single progress bar is shown on Search, and manual refresh switches to that tab.

Discovery walks each configured folder recursively once, collecting files with a case-insensitive `.pdf` extension. It skips symbolic-link files and does not descend into symbolic-link directories or Windows junctions. Discovery does not read PDF contents or apply size/text-quality checks. Clipper discovery reads every row of the supported `clips` table in ID order into a frozen inventory and releases the source transaction before indexing.

The candidate count is unknown during discovery. Once all available sources have been inventoried, refresh freezes one total and processes that inventory with a determinate progress bar. New arrivals wait until the next refresh. File size, hashing, cached extraction and text-quality checks happen during processing; candidates can therefore be counted but later skipped or fail. Incomplete folder traversal prevents deletion reconciliation. Clipper counts are provisional until its collection transaction commits. Cached/extracted are processing-path counts and overlap indexed/skipped outcomes; do not add them together as separate files.

Failures are printed immediately to **stderr** when a terminal exists. The CLI also prints throttled progress to stderr, keeping its final stdout report as JSON. Console-free `pythonw` launches retain failures in **`ingestion.log` beside the library database**, with two rotated backups (`.1`, `.2`), each capped at approximately 5 MiB. The log includes paths and reasons for source/file errors, scan failures, skipped files and partially accepted PDFs. These are local operational records and can contain private filenames. The GUI completion summary shows counts and the log path.

To see terminal output while using the GUI:

```powershell
.\.venv\Scripts\python.exe -m local_search gui
```

Progress updates are coalesced so a fast ingest does not fill the UI event queue. This does not change extraction quality gates, hashing, transactions or deletion safety, and it does not add ingest cancellation or parallel ingestion/search.

## Editing prompts

The editable UTF-8 `prompts.txt` beside this README contains named sections:

```text
[search.system]
The system instructions for library questions...

[metadata.system]
Draft instructions for future metadata extraction...
```

`search.system` supplies the system prompt for **Ask local model** and the CLI `ask` command. Its initial text matches the previous built-in prompt. Preserve the documented JSON action names and fields when editing it. Host-side tool validation, citation checks and context limits remain enforced.

`metadata.system` is used by the separate [metadata extraction demo](METADATA_DEMO_README.md); desktop enrichment remains planned. Add other uniquely named sections as needed. A future task can select them through `load_prompt(name, path)`; adding a section alone does not activate a feature.

The app uses the bundled file by default. Select another file under **Settings → Preferences → Prompt library**, or add `"prompts_file": "my-prompts.txt"` at the top level of your JSON configuration. Custom relative paths resolve against the configuration directory; blank/unset uses the bundled file, independently of the working directory. CLI and GUI use the same selection. **Load & test model** retains its fixed READY probe.

Edits apply to the next question without restarting or reloading the model. Each question reads its prompt once; its local result/trace records the selected section and a SHA256 of its text. Prompt text, chat formatting and evidence all count toward the total token cap. Missing files, duplicate/empty sections, or a missing `search.system` fail explicitly rather than silently falling back.

Use a section header on its own line. Text is literal: there is no variable substitution, include processing or code execution. Preamble lines beginning with `#` are ignored before the first section; within a section every line, including `#` lines, is prompt content. Prompt files are limited to 1 MiB. Keep custom private prompts outside the source-release directory; the supplied generic `prompts.txt` is intended to be published with the app.

## 1. Install

Open PowerShell in this project's directory. Use 64-bit Python 3.11 or newer (Python 3.12 was tested).

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-local-search.txt
Copy-Item local-search.example.json local-search.json
```

If `.venv` already exists, reuse it and skip its creation. Plain indexing/search requires `pypdf` and Python's SQLite with FTS5. To add model answers:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-gguf.txt
```

The native runtime may need a compiler or an appropriate prebuilt wheel. Use the [official llama-cpp-python installation instructions](https://llama-cpp-python.readthedocs.io/en/latest/) for your operating system and GPU. Your installed runtime must support the architecture inside the GGUF; a newer model may require a newer runtime. The program does not download model weights.

### Start the desktop interface

```powershell
.\run-local-search.ps1
# Or, to keep startup errors visible in the console:
.\.venv\Scripts\python.exe -m local_search gui
```

The interface uses standard-library Tkinter/ttk: no browser, web server or Electron. Under **Settings → Library & sources**, choose the index location and add PDF folders or clipper databases. Select the GGUF under **Local model**, then save. Click **Refresh index**, then use **Search** for keyword queries or **Ask local model** for questions. Double-click results for surrounding text; **Read source** and **Next page** provide full indexed-text access. **Browse sources** includes skipped files and their reasons; **More results** pages through matches or catalogue entries.

Indexing and inference run on a worker thread; GUI rendering stays lightweight. The model is cached until settings change or **Unload model** is pressed. **Stop operation** cancels at the next generation/agent boundary; native model loading and prompt evaluation may take time before they can stop. Set **Auto-refresh seconds** to 300 for five-minute refreshes while the GUI is open, or 0 for manual updates. Refresh and inference are serialized in the GUI. Each model question uses a consistent SQLite read snapshot; the WAL library permits an external updater to commit newer data for the next question.

For a console-free Windows launch, use `.venv\Scripts\pythonw.exe -m local_search gui` (put that command and the project working directory in a shortcut). Python distributions without Tkinter need their platform's Tk package; the normal Windows Python installer includes it.

## 2. Configure your sources

Edit `local-search.json`. Paths can be absolute or relative to the configuration file. Keep source names unique and stable: they identify collections during updates and deletion reconciliation.

```json
{
  "database": "data/library.sqlite3",
  "sources": [
    {
      "name": "webclips",
      "type": "clipper",
      "path": "C:/Library/clipper.sqlite3",
      "closed_snapshot": false
    },
    {
      "name": "papers",
      "type": "folder",
      "path": "C:/Library/PDFs",
      "ascii_ratio": 0.8,
      "min_words": 20,
      "max_file_mb": 200,
      "max_pages": 2000
    }
  ],
  "model": {
    "path": "C:/Models/model.gguf",
    "context": 12000,
    "output": 2000,
    "gpu_layers": 0,
    "thinking": false,
    "cache_k": "f16",
    "cache_v": "f16",
    "flash_attention": false,
    "kv_offload": true
  }
}
```

You may configure multiple clipper databases and multiple folder roots. Start with a small folder to inspect extraction quality before scanning a large library. Changing a source path while retaining its name replaces that collection's contents on the next complete refresh. Removing an entry from the configuration does not delete its old collection; to start over, choose a new library database path.

The supported clipper schema is `clips(id, title, captured_at, content_text/content_html, ...)`. Configure the path to your own compatible database. The adapter reads current clips, not revision history, asset blobs or annotation bodies. Plain text is preferred; HTML is converted when plain text is missing. Clips with only PDF attachments and no stored text are reported as skipped; index those PDF files through a folder source.

The source database is opened read-only in a SQLite read transaction, including committed WAL contents. On read-only media where SQLite cannot open its WAL infrastructure, close the clipper and set `closed_snapshot` to `true`. That mode makes a temporary private copy, verifies unchanged source bytes, and refuses any WAL/journal sidecar. Never use it as a workaround for an actively changing database. It does not discard a live WAL or alter the source database.

## 3. Index, inspect, and update

```powershell
.\.venv\Scripts\python.exe -m local_search ingest
.\.venv\Scripts\python.exe -m local_search status
.\.venv\Scripts\python.exe -m local_search catalog --status skipped
.\.venv\Scripts\python.exe -m local_search catalog --status error
```

Run `ingest` again whenever files or clips change. To poll every five minutes while the process remains open:

```powershell
.\.venv\Scripts\python.exe -m local_search ingest --watch 300
```

Ctrl+C stops polling. For unattended updates, Windows Task Scheduler can run the absolute path to `.venv\Scripts\python.exe` with arguments `-m local_search --config C:\full\path\local-search.json ingest`; set **Start in** to the project directory. Run one updater at a time. No scheduled task is installed automatically.

Every PDF is SHA256-hashed on refresh, so content changes are detected even if size and timestamps were preserved. Cached extraction is reused for identical bytes and the same extraction policy/parser version, including across copied files. A changed file is re-extracted. Renames become a new location plus removal of the old location; cached extraction is reused. Missing files are removed only after a complete folder traversal. An unavailable root or incomplete directory walk does not prune the collection. Known failed file updates stop serving the previous text under that path. Errors are recorded and retried on subsequent refreshes; a rejected quality result is cached until bytes or quality settings change. Extraction-cache entries for files no longer referenced by any source are removed during successful reconciliation.

PDF extraction runs in a separate process with a 90-second timeout per file. No OCR is performed. Each page must contain enough plausible ASCII-dominant words and printable text; character-mapping damage, fragmented strings, too little text and strongly non-ASCII text are rejected. A mixed PDF can contribute its accepted pages; page acceptance and rejection reasons are retained in the catalogue's `detail`. This heuristic can reject equations, tables or legitimate unusual prose and cannot prove that text is meaningful. Inspect results before relaxing `ascii_ratio` or `min_words`. Relaxing the ASCII threshold alone does not make this an effective Chinese extractor.

## 4. Search without a model

```powershell
.\.venv\Scripts\python.exe -m local_search search 'renewable AND capacity'
.\.venv\Scripts\python.exe -m local_search search '(solar OR wind) NOT offshore'
.\.venv\Scripts\python.exe -m local_search search 'NEAR(Adam optimizer, 5)'
.\.venv\Scripts\python.exe -m local_search search 'transformer*' --collection papers
.\.venv\Scripts\python.exe -m local_search search 'revenue' --after 2024-01-01 --date-field published_at --sort newest
.\.venv\Scripts\python.exe -m local_search catalog --title-query 'attention AND need'
```

FTS5 supports quoted phrases, AND/OR, binary NOT, parentheses, prefixes and unordered NEAR groups. Plain words are implicitly ANDed. Queries are passed as SQL parameters; the model never executes SQL. Body search operates on passages of at most 220 whitespace-separated words, so Boolean/proximity matches must occur in one passage. It is not a whole-document Boolean join. Titles have a separate catalogue search. Chinese word segmentation and semantic search are not included.

Results include passage IDs, source IDs, document IDs, page/character locations and dates. Expand a result or read its full document through successive pages:

```powershell
.\.venv\Scripts\python.exe -m local_search context 12 --radius-chars 1500
.\.venv\Scripts\python.exe -m local_search document 3 --start-char 0 --count-chars 8000
```

Replace IDs with those returned by your search. If a repeated passage appears in different documents, expansion requires `--doc-id` from the selected result so it does not silently choose another version's surrounding text. Follow `next_start` to read the remainder. Offsets refer to extracted indexed text. Rejected PDF pages are absent; a full indexed document is not a promise that every original PDF page was readable. Search supports `--offset`/`--limit`; catalogue supports the same pagination. Each returned passage lists up to 20 matching source locations and a total count; document reads show up to 20 sources. Use the catalogue to inspect additional copies.

## Dates and duplicates

| Field | Meaning |
|---|---|
| `added_at` | Webclipper capture time; Windows file creation time; index-first-seen fallback when creation time is unavailable. |
| `added_basis` | Which of those clocks was used. |
| `modified_at` | Filesystem last modification time, for PDFs. |
| `published_at` | Explicit normalized publication date from the clipper, if present. |
| `date_precision` | Publication date precision, when the clipper supplies it. |

Times are normalized to UTC. `after` includes its boundary; `before` excludes its boundary. Date filters apply to individual source occurrences before grouping duplicates. Unknown dates do not pass a date filter. Copying a file can make its creation date recent even when its contents are old. PDF creation metadata is retained for inspection but is never promoted to publication date. Index-first-seen is the date this library first observed the path, not a recovered historical arrival date.

Identical file bytes share an extraction-cache entry. Identical extracted text with the same page/offset layout shares a document. Different layouts retain their page provenance. Paragraphs are split into bounded passages, normalized for Unicode/whitespace, hashed and indexed once; repeated passages show multiple locations instead of filling every result slot. Case and punctuation remain significant. This also handles repeated text between webclips and PDFs when passage boundaries line up. It does not perform semantic or fuzzy deduplication, and a sentence repeated inside otherwise different paragraphs may still appear in separate results. Copies are not independent corroboration. Shared boilerplate remains searchable but is grouped rather than removed.

## 5. Load a GGUF and ask questions

Set `model.path` to your local instruction GGUF, then run:

```powershell
.\.venv\Scripts\python.exe -m local_search model-check
.\.venv\Scripts\python.exe -m local_search ask 'What changed in the recent revenue reports?' --trace data/last-search.json
```

The model loads directly into this process. No separate server is needed. Its embedded chat template and native tokenizer format/count the exact prompt. The configured `context` is a total token cap; `output` is reserved within it and includes any thinking. The assistant can refine searches, inspect titles/dates, expand evidence, read documents in pages, and save exact tool evidence to a configurable notebook (6,000 tokens by default). It can checkpoint recent results; old unsaved results are dropped if needed to fit the cap. At most 24 model turns are allowed by default (`--max-rounds` changes this). The program returns an explicit failure status for output exhaustion or the round limit rather than inventing an answer. Tool response JSON is validated; citations must identify sources from returned evidence. This validates source exposure, not semantic entailment.

Set **Notebook token limit (64–32768)** under **Settings → Preferences**, or `agent_options.note_tokens` in the settings file. The CLI accepts `ask ... --note-tokens 8000` as a per-question override. Existing settings without this field use 6,000 tokens. This is a maximum saved-evidence allowance within the existing total context/output cap, not extra model context. Saves that cannot fit the total context are rejected before changing the notebook. Each saved item includes the whole tool result and its metadata. The model receives used/remaining tokens and item count; the live pane, final summary and local trace also show usage. Oversized saves report current usage, proposed usage and excess tokens, and leave saved evidence unchanged. Remove a note or retrieve a smaller passage to make space; checkpoint retains notes. Empty notebooks report zero tokens.


`thinking: true` passes `enable_thinking` to templates that support it. Template behavior varies by model; there is no claim that all GGUFs implement that switch. The local assistant uses a small JSON action protocol rather than relying on model-specific native function-call parsing.

### Windows, PyTorch DLLs and GPU layers

Before importing llama.cpp, the loader discovers the active environment's `torch/lib`, registers it with `os.add_dll_directory`, and loads available `cudart64`, `cublasLt64` and `cublas64` DLLs from that exact directory. Handles remain alive for the model lifetime. PyTorch itself is not the GGUF inference engine.

To use another installed PyTorch library directory, add this to `model`:

```json
"torch_lib": "C:/PythonEnv/Lib/site-packages/torch/lib"
```

For NVIDIA acceleration, install a compatible CUDA-enabled llama-cpp-python build and matching PyTorch/CUDA dependencies. Use the [official PyTorch installer selector](https://pytorch.org/get-started/locally/) and the runtime's [backend instructions](https://llama-cpp-python.readthedocs.io/en/latest/#supported-backends). CPU-only PyTorch does not contain the CUDA DLLs. Adding DLLs cannot turn a CPU-only llama.cpp build into a CUDA build; the program reports that mismatch if GPU layers were requested. Set `gpu_layers` to `-1` for all supported layers on the GPU, or a positive number for partial offload. Start with `0` to verify the rest of setup. Model weights and KV cache must fit available memory; reducing context or GPU layers can help.

### K/V cache controls and display headroom

The GUI and config expose K and V precision independently: `f16`, `q8_0`, or `q4_0`. They map directly to llama.cpp's `type_k` and `type_v`. F16 is the conservative default; quantized caches reduce cache memory but can affect quality. No local 27B cache-quality comparison has been run here. Quantized V requires `flash_attention: true`; unsupported types or that incompatible combination are rejected before loading. Model/backend support still depends on the installed runtime. `kv_offload` maps to its `offload_kqv` option and can be disabled to keep those operations off the GPU.

When one GPU drives both the desktop and inference, leave headroom by reducing GPU layers, context length and/or cache precision. These controls do not impose a hard VRAM reservation for Windows. The Tk GUI itself does not load a second model or use a GPU-rendered browser engine. Keep only one inference instance loaded; CLI model commands alongside the GUI can load another copy.

## Troubleshooting and validation

- **Missing PDF library:** install `requirements-local-search.txt` with the same Python that runs the program.
- **Unable to open clipper database:** verify the main path; for a closed database on read-only media use the explicit closed-snapshot mode above. Do not delete WAL files.
- **Clipper discovery diagnostics:** the refresh report and `ingestion.log` include the failing stage, resolved source path, local/network classification, access mode, WAL/SHM/journal presence, rows discovered and available SQLite error details. Schema failures also list discovered clip columns. Hints distinguish local live access from closed snapshots and network exports; clip titles and bodies are not included in these diagnostics.
- **No PDF results:** inspect `catalog --status skipped` and `catalog --status error`; the file may be a scan, encrypted, malformed, or rejected by text-quality checks.
- **No search matches:** broaden the query, quote punctuation, try fewer AND terms, and use title search. Zero hits do not prove absence.
- **DLL/model architecture errors:** verify 64-bit Python, runtime/model compatibility and CUDA versions; test CPU mode first.
- **Wrong answer despite a correct citation:** inspect the saved trace and original source. A real citation does not guarantee a correct interpretation.

## License

MIT. See [LICENSE](LICENSE). Optional dependencies retain their respective licenses; model weights must be obtained and used under their own licenses.

## Optional CPU or GPU reranking

Reranking is **off by default** and applies only to **Ask local model** and CLI `ask`. Manual **Search FTS5**, catalogue browsing, document reading and ingestion retain their existing behaviour.

FTS5 first retrieves a bounded pool of passages satisfying the original Boolean/proximity query and source/date filters. A separate CrossEncoder then scores each **(original natural-language question, passage text)** pair. The search model may provide `relevance_query` to focus that comparison. The scores estimate relevance; they are not confidence probabilities, factual verification or freshness scores. The reranker cannot discover documents outside the FTS candidate pool.

### Install the optional runtime

Use the same Python environment as the application. Install the CPU or CUDA PyTorch build appropriate to your machine using the [official PyTorch installation selector](https://pytorch.org/get-started/locally/), then:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-reranker.txt
```

CPU-only PyTorch can run CPU reranking but cannot run it on CUDA simply by changing the dropdown. PyTorch's CUDA support is separate from the llama.cpp CUDA build used by the GGUF. Reranking runs in an isolated process so its framework/DLL state is separate from GGUF inference. No reranker dependency is imported or model loaded when the feature is disabled.

### Use local weights

Obtain a compatible **trained scalar CrossEncoder reranker** and keep its entire exported model folder locally, for example `C:/Models/Reranker`. This is **not a GGUF**, and a lone weights file is insufficient. The folder needs its architecture/configuration, tokenizer files, weights (`.safetensors` or the model's supported format) and any module/submodule files supplied by the model author. For a modular Sentence Transformers export, preserve `modules.json` and folders such as pooling/dense/normalisation modules; copying only the root encoder can omit the trained scoring head.

The local CPU integration was checked with `cross-encoder/ettin-reranker-17m-v1` (revision `9e4aa35321a6dd1a43ca313f500c4b4f7cfb5cc6`). The app does not bundle, download or automatically update those weights. Use the complete corresponding local export and retain its licence/provenance separately. A generative chat model or embedding-only model is not interchangeable with a trained reranker.

In **Settings → Reranker**:

1. Browse to the local model folder.
2. Select **cpu** or explicitly **cuda:0** (the first visible CUDA GPU); **cuda:1** is also available for a second GPU. CPU is the default. Advanced configurations can use another `cuda:N` device.
3. Set **Candidates to rerank**, from 1 to 1,000 (default 40). This controls how many eligible lexical passages are scored, not the size of the whole library.
4. Set the maximum results returned per model search, from 1 to 20 (default 5). The model can request fewer. Further pages cover the same frozen candidate pool.
5. Enable reranking, save settings and ask a question.

A corresponding top-level configuration section is:

```json
"reranker": {
  "enabled": false,
  "model_dir": "C:/Models/Reranker",
  "device": "cpu",
  "candidates": 40,
  "top_k": 5,
  "batch_size": 8,
  "threads": 2,
  "max_pair_tokens": 512,
  "timeout_seconds": 5,
  "startup_seconds": 60
}
```

Relative model-folder paths resolve against the configuration directory. GUI setting changes apply on the next question. The process/model is reused across questions with unchanged settings. **Unload / reset reranker** releases it and permits retrying a previously failed load. **Unload model** releases both models. CLI `ask` uses the same settings and releases its reranker on exit.

### Limits, GPU use and diagnostics

Larger candidate pools cost more work; increasing batch size can improve throughput at the cost of more memory. Increase the scoring timeout if larger pools exceed it. The pair-token limit counts the complete query/passage pair using the reranker's tokenizer. If a pair exceeds that budget, the entire pool retains lexical order rather than silently truncating evidence. Raise the pair budget only within the selected model's supported capacity.

CUDA use is opt-in. Its model, framework context and batches consume VRAM alongside the GGUF, K/V cache and display. There is no automatic memory reservation or GPU quantisation. Reduce batch size, choose CPU, or unload the reranker if necessary. GPU execution requires a compatible GPU/PyTorch installation; it has not been performance-tested on this development machine's CPU-only PyTorch build.

Model loading is local-files-only with remote code disabled. Missing models/dependencies, unavailable CUDA, out-of-memory errors, invalid scores and deadline failures produce an explicit lexical-order fallback **within the candidate pool**. A timed-out worker is terminated, and fatal failures are not repeatedly retried until reset/settings change. Stop inference also cancels reranking at a short polling boundary.

For complete lexical enumeration, the model can set `search_scope:"exhaustive"`; `sort:"newest"` also bypasses reranking. Shortlist responses identify `match_scope:"candidate_pool"`, its size and whether more lexical results exist outside it. Paging is frozen within a question's database snapshot. Expired pools require restarting at offset zero; they are not silently rescored halfway through pagination.

The timing breakdown separates **FTS searches** from **Reranking / shortlist work**. The local question trace records candidate IDs, ranked IDs, relevance question, device, scores, pair-token counts, fallback reasons and cold/warm load/scoring time. Compare identical questions and sources with the toggle off/on before treating it as an improvement. Neither an answered question nor a higher score proves better answer quality.

## Standalone metadata extraction demo

Use [demo_metadata_extraction.py](demo_metadata_extraction.py) to extract reviewable metadata from any number of source row IDs in your own local text file. It reads the library without modifying it and creates an HTML/JSON report with evidence, exact evaluated excerpts, filenames, paths and original-source links. See the [demo setup and prompt guide](METADATA_DEMO_README.md) before running. Source-ID lists, documents and generated reports are not included in this repository.

# Local Library Search

Roadmap: [plan v1.1 — reasons, priorities and acceptance checks](PLAN_V1_1.md).

This native desktop program indexes a webclipper SQLite database and PDF files anywhere beneath configured folders. It keeps its own SQLite FTS5 library. You can search directly without a model, or load a GGUF inside the program and ask questions in ordinary language. Sources and inference stay local; no OpenRouter call is made by the desktop program. A command-line interface is also available.


## v1.1 desktop updates

- **Source links:** search results, expanded passages, document pages and model answers include named source links. Hover over a link for the full title, registered path/URL and available source dates. PDFs open in the default external viewer; webclips open their original HTTP(S) URL. Page-specific viewer navigation is not included. Missing files produce an explanation.
- **Live generation:** enable **Show live model output** to inspect thinking and action JSON in a separate, bounded pane. This is unvalidated model output, not the final answer. Tools execute only after the complete action is validated. Updates are batched; the pane retains at most 32,000 characters and 1,000 lines and is never fed back into context. It may contain source excerpts.
- **Measurements:** direct search reports queue, connection, count, ranking, metadata and render time. Model answers report model loading, model rounds, generation-request first-output latency and an approximate last-round decoding rate, excluding the first emitted token. **Displayed in** includes the full GUI operation, loading and tools. Approximate generated-token counts include thinking and action JSON; these are not native runtime performance counters.
- **Search totals:** the GUI skips the full exact count by default, retaining ordinary ranking, filters and pagination. Select **Exact total** when required. CLI searches retain exact counts; add `--no-count` to skip them. An omitted count is `null`, never an invented total.
- **Database cache:** **Sources & model** exposes a 1–1024 MiB SQLite cache budget per connection, default 32 MiB. The GUI reuses one worker-owned connection until its database/cache configuration changes. This is a suggested page-cache maximum, not preallocated RAM or a full in-memory index. Current-library opens no longer run schema setup or a full title-index repair scan. Refresh and inference still share the existing serial worker, so ingestion can delay an interactive operation.
- **64k context:** the preset sets total context to **65,536 tokens** and output reserve to **8,192**, leaving at most **57,344 prompt tokens** after chat-template formatting. Thinking, action JSON and answer generation share the output reserve. Model/runtime support and sufficient KV-cache memory are required. Defaults and existing configurations remain 12,000/2,000; choose the preset explicitly. The larger cap retains more recent evidence before eviction; the 1,500-token notebook and bounded tool-result sizes stay conservative.

Optional configuration at the top level:

```json
"database_options": {"cache_mb": 32}
```

These changes do not introduce inferred metadata filtering, a reranker, synonym rewriting or a full RAM snapshot. Folder-selected LLM enrichment remains a separate planned feature. Existing added/modified/publication dates are displayed as stored; added dates do not establish information recency.

### End-to-end question timing

For model questions, the summary shows **Click → final answer complete**. The clock starts at entry to the Ask button handler, before settings/configuration work, and ends after the final answer and named sources have been inserted and Tk has processed idle redraws. Cancelled or incomplete runs are labelled **Click → stopped**, never completed answers.

Enable **Show question timing breakdown** for non-overlapping totals: settings/UI setup, worker queue, model load/reuse, database connection, model prompt formatting, model inference, model response processing, FTS searches, catalogue lookups, context expansion, document reads, notebook operations, other tools, context budgeting/validation/source resolution, worker bookkeeping/initial trace save, UI delivery/polling, and final-answer rendering. Repeated operations are accumulated across all rounds; failed tool calls are included. These stages sum to the end-to-end total, subject to floating-point/rounding precision.

Model inference includes native prompt evaluation and generation; the app does not claim to measure those separately. First-generation-request-to-first-output latency is an additional diagnostic already included in the stages, not another duration to add. Background live-output redraws overlap worker execution and are not added a second time. The existing approximate decoding-rate diagnostic remains available.

The local `last-gui-search.json` trace includes `gui_timing` with the complete breakdown, plus per-round model and per-tool timings. Final trace persistence runs asynchronously after visible answer completion and is excluded from that total. The initial trace save is included in worker bookkeeping. Timing-label rendering and opening a source viewer are outside answer completion. Direct FTS searches retain their detailed query-phase measurements and **Displayed in** summary.

### Live ingestion feedback and failure logs

Refreshing the index shows the active source, current filename, phase, elapsed time and processed/indexed/cached/extracted/skipped/error counts. Phases include database opening, folder discovery, hashing, PDF extraction, indexing and deletion reconciliation. The status remains visible while extraction is running; a long PDF may take up to the existing 90-second worker timeout. A single progress bar is shown on Search, and manual refresh switches to that tab.

Folder traversal remains one pass: the final PDF count is unknown until traversal finishes, so its bar is indeterminate and the processed count increases as files are found. No speculative percentage or ETA is shown. Webclipper imports have an exact row total and a determinate bar; intermediate clip counts are provisional until the existing transaction commits. Counts are per source, with source number/total shown. Cached/extracted are processing-path counts and overlap indexed/skipped outcomes; do not add them together as separate files.

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

`metadata.system` is a draft for the planned extraction workflow; it is not currently used. Add other uniquely named sections as needed. A future task can select them through `load_prompt(name, path)`; adding a section alone does not activate a feature.

The app uses the bundled file by default. Select another file under **Sources & model → Prompt library**, or add `"prompts_file": "my-prompts.txt"` at the top level of your JSON configuration. Custom relative paths resolve against the configuration directory; blank/unset uses the bundled file, independently of the working directory. CLI and GUI use the same selection. **Load & test GGUF** retains its fixed READY probe.

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

The interface uses standard-library Tkinter/ttk: no browser, web server or Electron. On **Sources & model**, add the webclipper database and PDF folders, select the GGUF, choose the library database, and save. Click **Refresh index**, then use **Search** for keyword queries or **Ask local model** for questions. Double-click results for surrounding text; **Read document from start** and **Next document page** provide full indexed-text access. **Browse documents** includes skipped files and their reasons; **More results** pages through matches or catalogue entries.

Indexing and inference run on a worker thread; GUI rendering stays lightweight. The model is cached until settings change or **Unload model** is pressed. **Stop inference** cancels at the next generation/agent boundary; native model loading and prompt evaluation may take time before they can stop. Set **Auto-refresh seconds** to 300 for five-minute refreshes while the GUI is open, or 0 for manual updates. Refresh and inference are serialized in the GUI. Each model question uses a consistent SQLite read snapshot; the WAL library permits an external updater to commit newer data for the next question.

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

The model loads directly into this process. No separate server is needed. Its embedded chat template and native tokenizer format/count the exact prompt. The configured `context` is a total token cap; `output` is reserved within it and includes any thinking. The assistant can refine searches, inspect titles/dates, expand evidence, read documents in pages, and save exact tool evidence to a 1,500-token notebook. It can checkpoint recent results; old unsaved results are dropped if needed to fit the cap. At most 24 model turns are allowed by default (`--max-rounds` changes this). The program returns an explicit failure status for output exhaustion or the round limit rather than inventing an answer. Tool response JSON is validated; citations must identify sources from returned evidence. This validates source exposure, not semantic entailment.

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
- **No PDF results:** inspect `catalog --status skipped` and `catalog --status error`; the file may be a scan, encrypted, malformed, or rejected by text-quality checks.
- **No search matches:** broaden the query, quote punctuation, try fewer AND terms, and use title search. Zero hits do not prove absence.
- **DLL/model architecture errors:** verify 64-bit Python, runtime/model compatibility and CUDA versions; test CPU mode first.
- **Wrong answer despite a correct citation:** inspect the saved trace and original source. A real citation does not guarantee a correct interpretation.

## License

MIT. See [LICENSE](LICENSE). Optional dependencies retain their respective licenses; model weights must be obtained and used under their own licenses.

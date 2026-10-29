# New question, search, viewer and library controls

## Search and conversation

The Search tab contains keyword/FTS5 search, optional question-based ranking, filters and the source reader. Search syntax opens a small reference with phrases, AND/OR/NOT, prefixes and NEAR examples.

The Ask tab keeps a conversation: send a question, then follow up in the same view. The composer supports multiple lines; Ctrl+Enter sends. New conversation starts a fresh context and saves the previous chat. Switching libraries clears the active chat after saving it. Status shows Thinking, Selecting reports and cycling titles of reports actually selected by tools. These labels describe activity, not proof that a report has been understood.

Conversation → Saved conversations restores local chats for the active library. Chats are automatically saved beneath `%LOCALAPPDATA%/LocalLibrarySearch/conversations/`, separately from network library snapshots. They contain private questions, answers, sources and the latest 20 detailed turn traces; protect this folder as you would the library. The displayed chat is bounded, while the saved transcript retains earlier text. Model history is limited to the latest 40 messages and can be trimmed further for context; an answer notes omitted context or previous excerpts dropped after source checks. Conversation also provides Copy last answer and Export conversation. Retry last question appears after an incomplete/failed answer and uses that question's original prior context.

Click an answer's source link to inspect the exact saved tool passages in an evidence window, then use Open original PDF / web source to launch the viewer. The window identifies previously reused evidence and shows source dates where known. A saved passage records what the model received; it does not establish that no newer reports exist. Earlier saved text may remain available after its detailed trace has aged out; the evidence window states when a passage body is unavailable.

Settings are now an explicit draft: Save settings applies them. Search, Ask and other operations use saved settings, so an unfinished model edit cannot block ordinary keyword search. Answer effort remains a per-question control; Legacy comparison is under Preferences. The footer shows the active library and local/sync state. Replacing a library clears selections and page cursors, and document reading checks the saved document fingerprint. Index refresh clears search results while keeping conversation history for revalidation on the next question.

Search results persistently identify question-ranked results, keyword fallback or newest-first bypass, with candidate counts and any matches outside a bounded pool. Diagnostic/status reports open in their own details window instead of replacing the source reader.

Details opens the tucked-away activity, thinking and timing views. Select Current turn or an earlier turn to inspect tool calls, rejections and generated responses.

Follow-ups receive bounded conversation history and up to eight cited excerpts from the preceding answer, within a budget of 4,000 tokens or a quarter of the model context, whichever is smaller. Only excerpts still visible to the model at completion are eligible. Sources, indexed content and excerpt locations are checked again against the current library before reuse; changed or removed evidence is dropped. Retained excerpts survive checkpoints, but may be evicted under context pressure. These excerpts can support citations directly; older assistant text alone cannot. Unchanged passages do not establish that newer contradictory documents are absent: the model can still search for updates. Oversized excerpts are omitted rather than silently shortened.

## Steering an active question

Settings → Preferences offers two independent, opt-in Ask JSON controls. **Use non-thinking JSON for the first Ask response** constrains only the first model response of each submitted question (including follow-ups) to the supported action schema. It may select a search, another tool or an answer. All later normal responses resume the configured Thinking setting.

**Repair invalid Ask JSON** allows at most two additional formatting calls per submitted question. These calls temporarily disable thinking, constrain JSON and receive only the candidate action, schema and formatting error, without the original question, retrieved documents or earlier reasoning. They are instructed to preserve the intended action and answer rather than answer the question again; exact preservation cannot be proven for malformed input. Repaired returns still pass the ordinary tool, citation, coverage, finalisation and steering checks. Invalid citation IDs or unresolved evidence needs use normal model retries instead of formatting repair. Output cutoffs and cancellation are not treated as repairable complete answers. Repair attempts, original/repaired text, constraint status and timings are recorded in Details; the extra calls count in generation timing. Failed repairs remain rejected, and normal attempts retain the existing round limit.

The configuration keys are `agent_options.first_turn_json` and `agent_options.repair_invalid_json`, both false by default. The CLI Ask command honours the same settings. Metadata extraction is unchanged by these options.

While Ask local model is running, enter a correction in the instruction box and click Queue instruction. Activity shows queued/applied status, and the question trace records the text, queue timestamp and applied round.

Instructions join the next model request and persist through notebook checkpoints. They do not interrupt the generation currently running. A final answer is held back if an instruction arrived during its generation. At most two extra responses are allowed to handle such a late correction; if that allowance or the context limit is exhausted, the question is reported incomplete with its steering history retained. No accepted queued instruction is silently discarded. Instructions are scoped to one question, with a maximum of 12 instructions / 8,000 characters. After completion, start another question.

Details → Thinking shows tagged/implicit model thinking separately from tool actions in Activity. Both are bounded, unvalidated live displays; saved turns retain the generated responses. The full generated response still goes to the existing action parser. The display does not grant the model additional tools or context.

## Keyword search with a relevance question

Configure and enable the CrossEncoder in Settings → Reranker. Enter keywords/FTS5 in the main search field and a natural-language question in Rank search results for question, then Search library. FTS5 selects candidates; the reranker scores their exact text against the separate question. This does not load the GGUF or generate an answer.

Clear the relevance question for ordinary manual FTS5. Newest-first bypasses reranking. Results explicitly identify a bounded shortlist; More results pages that frozen pool without scoring it again. A new search rebuilds the pool. Existing candidate count, top-k, batch size, device, timeout and lexical-fallback settings apply. A separate question cannot recover material omitted by the FTS candidate query.

Command line:

```text
python -m local_search --config local-search.json search "critical minerals" --input-mode simple --relevance-question "What restrictions apply to investment?"
```

## Model diagnostics

Load the model, then Settings → Local model → Model diagnostics. The report includes requested GPU layers, observed offloaded layers when the native runtime emits them, model size/runtime, requested context/KV/Flash Attention settings, load time and the latest generation timing. Missing actual-placement data is explicitly unknown; requested settings are not presented as verified placement.

Best-effort memory observations include this process's RAM on Windows and device-wide GPU totals when `nvidia-smi` is available. Device totals include the display and other apps. System RAM, dedicated VRAM and shared GPU memory are not interchangeable measures. Question traces also retain model/load diagnostics; current memory probing happens only on demand.

## Chrome and Adobe page opening

Settings → Preferences provides Open PDFs at known cited page, Chrome/Acrobat selection and an optional executable path. Leave the path blank to detect common installations. Windows' default reader does not need to change.

With the toggle off, opening uses the current Windows default. With it on, Chrome receives an encoded local-file URL with `#page=N`; Acrobat/Reader receives `/A page=N`. Source paths come from registered evidence, and launch arguments never go through a shell. A known unique physical PDF page is required. Multiple possible pages or source-only citations fall back to ordinary opening. Physical page numbers include the cover and can differ from printed numbering.

A missing viewer or launch exception also falls back. Successfully launching a viewer does not prove it honoured a page parameter; silent viewer-specific failures need an actual integration check. Web clips retain normal HTTP/HTTPS opening.

## AppData working libraries and network publication

In Settings → Library & sources, select the published database path and enable Use AppData working copy. UNC/mapped-drive library paths use managed local storage automatically. Each origin gets a stable folder beneath `%LOCALAPPDATA%/LocalLibrarySearch/libraries/`; Library / sync status shows the exact local path, published path, pending state and last error/success.

An existing published database must be a closed search-library export with no WAL/journal sidecars. The app copies and validates it before opening the local working copy. It never adopts a clipper database as the search library. The managed local copy is upgraded to schema 2 for identity/revision tracking; older app binaries refuse that version. A `before-management.db` backup is retained, and initial import does not modify the published file.

Sync now makes a consistent SQLite backup, uploads to a temporary file, verifies its hash and replaces the closed published snapshot under a publisher lock. The previous published file is retained as `<database>.previous`. Pending work committed after the snapshot remains pending. Independent remote edits cause a conflict and preserve local work. Ingestion/model search operate on the local database; offline use continues after an initial successful import.

Automatically sync changes back is opt-in and initially off. Test Sync now on the intended share before enabling it. GUI auto-sync coalesces changes after about 60 seconds of quiet, or five minutes pending while the worker is available, and retries transient failures with backoff. A native operation or blocked network call can delay it. CLI ingestion with auto-sync enabled attempts publication after each refresh. Exit preserves pending state; it does not promise to finish an upload before closing.

Load published copy explicitly adopts the remote version and invalidates displayed result/document state. It first closes the local connection and saves the previous local contents in `before-network-import.db`. This is also the UI conflict-resolution route for choosing the remote copy; pending local work remains in that backup. There is no automatic merge. Copy a recovery backup elsewhere before another adoption if you need longer history.

Configuration additions:

```json
{
  "library_sync": {"enabled": false, "auto_sync": false},
  "pdf_viewer": {"enabled": false, "viewer": "acrobat", "executable": ""}
}
```

`library_sync.local_root` optionally overrides the AppData root. Keep it on local storage. The configured `database` is the publication origin when management is enabled; ordinary local-library configurations continue to open directly when management is off.

Maintenance commands:

```text
python -m local_search --config local-search.json library-status
python -m local_search --config local-search.json library-sync
python -m local_search --config local-search.json library-adopt
python -m local_search --config local-search.json library-adopt --preserve-local
```

Plain `library-adopt` refuses pending local changes; `--preserve-local` explicitly saves them in the recovery backup before adopting the publication.

The main app and metadata extraction worker retain their existing ownership rules. The separate metadata review window can read and save findings while the main app is open. Shared access guards prevent migrations, restore and working-library replacement while review is active; close the review window before those operations. A `.publish-lock` coordinates supported publishers; a crashed publisher may leave a lock. Verify no publisher is running and inspect sync status before manually removing a stale lock. Do not directly edit/open the published SQLite file while publishing. These mechanisms cannot detect every unrelated external writer. Real share locking/durability behaviour still requires testing on the actual destination.

The separate [metadata sidecar](METADATA_WORKFLOW.md) provides migration, custom fields/prompts, random dry runs, bounded JSON recovery, graphical review and resumable writes. Its Extraction controls panel exposes profile editing, seeded trials, queue previews and bounded worker batches. Information-date resolution and mixed recency ranking remain planned. The clipper database remains separate.

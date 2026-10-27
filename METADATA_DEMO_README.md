# Standalone metadata extraction trial

This standalone demo reads selected source rows from the app's SQLite library, runs the configured local GGUF, and writes a review report. It never writes extracted fields back into the library. It is separate from normal ingestion and the desktop GUI. The generic demo script and this guide are published with the application. Your source-ID selection file, tests, documents and generated reports remain excluded from the public source export.

## Select a repeatable batch

Create `metadata-demo-ids.txt` beside the script (or edit your existing local file). Put **one `sources.id` per line**. Any nonempty number of IDs is supported. No source-ID selection is shipped with the repository. Blank lines and `#` comments are supported. IDs must be unique, positive integers; order is preserved. Missing or unindexed rows fail preflight rather than being substituted.

You can create an empty selection file with `New-Item metadata-demo-ids.txt -ItemType File`, then edit it in your text editor. Find IDs with the existing catalogue:

```powershell
.\.venv\Scripts\python.exe -m local_search --config local-search.json catalog --limit 50
# Additional pages:
.\.venv\Scripts\python.exe -m local_search --config local-search.json catalog --offset 50 --limit 50
```

Use the catalogue's source `id`, not `doc_id`, passage IDs or the original clipper's row IDs. Keep the selection file unchanged when comparing prompts. A stable ID does not guarantee stable content: re-ingestion may change a document, and deleted SQLite IDs may eventually be reused. Reports record title, locator, document identity/hash and selected IDs so these changes can be spotted.

## Run

Use the same environment and model configuration as the desktop app:

```powershell
.\.venv\Scripts\python.exe demo_metadata_extraction.py --config local-search.json

# Explicit selection and output location:
.\.venv\Scripts\python.exe demo_metadata_extraction.py `
  --config local-search.json `
  --ids-file metadata-demo-ids.txt `
  --output-dir data/metadata-review-01
```

Output directories must be new or empty. Without `--output-dir`, a timestamped folder is created under `data/`. The script prints the report path and each row's status. It uses local inference only and does not download weights or send text to a hosted API. Run it while the GUI model is unloaded to avoid a second model allocation.

The default input ceiling is 48,000 formatted prompt tokens and 120,000 document characters, further bounded by the model's configured total context minus output reserve. Both include the complete instructions/schema/payload where relevant; text is fitted using the actual model tokenizer. The model's configuration, including thinking and K/V-cache settings, is reused. A 64k configuration with an 8k output reserve is a reasonable initial trial if supported by your model and memory. Smaller configured contexts are honoured automatically. If output is truncated, the report records an error rather than treating incomplete JSON as a successful extraction.

```powershell
# Smaller input trial:
.\.venv\Scripts\python.exe demo_metadata_extraction.py --max-input-tokens 10000 --max-chars 30000
```

When a document fits, all its indexed text is evaluated. Otherwise the demo uses approximately 75% front matter and 25% closing material, and explicitly marks the evaluation **partial**. It is a simple first experiment, not a search-assisted extraction pipeline. Middle sections, omitted/rejected PDF pages and OCR content may contain metadata the model never sees. Bounds and page hints refer to indexed extracted text, not original PDF byte offsets.

## Review output

Open `report.html` from the printed output directory. For every selected source it includes:

- The source row ID, document title, filename, full path/URL, original-file link and stored document hash.
- Publisher, publication date/precision, scoped information cutoffs, reporting periods, typed reference numbers, suggested keywords and uncertainties.
- Evidence quotes, literal-evidence/schema warnings, raw model output and timings.
- The **exact evaluated text**, character ranges and page hints, with separate `.txt` files for inspection.
- Full-versus-partial coverage, evaluated character counts and the exact system/task prompts used.

`results.json` contains the structured run and per-source data for comparison. HTML escapes source text and model output; only registered absolute PDF destinations and HTTP(S) URLs become original-source links. Browser/platform settings may determine whether a PDF opens in a browser or external viewer. Missing original files can still have useful indexed text; the displayed path lets you investigate.

Results are saved after each source, so later failures do not discard earlier outputs. Structural/evidence checks are limited: a quote occurring in the text does **not** prove that it identifies this document's publisher or publication date. Every field still needs human evaluation. Generated keyword suggestions are separate from factual metadata.

## Starting prompt and suggested experiments

The demo loads `[metadata.system]` from the configured prompt library, defaulting to [prompts.txt](prompts.txt). The fixed task/schema is in `TASK` in [demo_metadata_extraction.py](demo_metadata_extraction.py). Both are recorded in the report. The production desktop still does not perform metadata extraction.

To compare prompt variants without changing the search prompt, copy `prompts.txt`, edit its metadata section and run:

```powershell
.\.venv\Scripts\python.exe demo_metadata_extraction.py --prompts-file metadata-prompts-v2.txt
```

Useful changes to test, one at a time:

1. **One short example of an endnote trap:** show a cited organisation/date being rejected as the current document's publisher/date. Avoid a long list of examples that consumes the input budget or dominates behaviour.
2. **Raw organisation names first:** establish extraction correctness before adding an alias dictionary. Later supply a small scoped alias list with explicit ambiguity rules; avoid asking the model to expand unfamiliar acronyms from memory.
3. **Separate date tasks if confusion persists:** first locate date statements and their roles, then normalise only supported values. Preserve scoped information dates instead of choosing the latest date mentioned.
4. **Separate keyword suggestions if they are noisy:** keep factual extraction focused; request a small number of tags from a supplied vocabulary in a later pass.
5. **Compare thinking on/off with the same IDs and text budget:** use the model configuration's `thinking` flag. Record correctness, missing fields, false positives, output truncation and runtime, rather than judging only how many fields are filled.
6. **Change coverage independently of the prompt:** front/back sampling can miss middle sections. Compare a larger input budget or a later targeted-pass design before trying to fix missing evidence through stronger wording.

Review publisher role, date interpretation, identifier ownership and evidence correctness separately. Include reports with cover dates, closing imprints, dense bibliographies, ambiguous acronyms and genuinely missing metadata. Retain unknowns as valid outcomes.

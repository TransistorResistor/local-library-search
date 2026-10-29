# Metadata extraction and review

Metadata is a separate local sidecar. It reads indexed text, writes only the search library's metadata tables and leaves source PDFs/clipper databases unchanged. The workflow supports custom fields/prompts, random trials, JSON recovery, migration, reviewed writes and resumable queued jobs.

Use the same configuration file as the desktop app. The examples below use `local-search.json`.

## 1. Define fields and prompts

Copy `metadata-profile.example.json` to your own `metadata-profile.json`. Edit the global `instructions` and each field's `prompt`, type, evidence requirement and review policy. Preview the exact prompt and output schema:

```text
python -m local_search.metadata profile --profile metadata-profile.json
```

Supported types: string, number, integer, boolean, date, string_list, date_list and interval_list. String fields/lists can have allowed `enum` values. Dates preserve year/month/day precision; cutoff dates and reporting intervals carry scopes. Generated keywords are labelled suggestions. Every factual field requires quoted evidence by default. `auto_accept` is initially false in the starter; literal quote matching does not establish correct interpretation.

A non-null field is a value/evidence object. For example, a publication-date field may return:

```json
{"value":"2024-02","precision":"month","evidence":[{"excerpt_id":"E1","quote":"Published February 2024."}]}
```

Unsupported fields are null. A date-list value contains date objects with value, precision and scope. Interval-list items have scoped start/end date objects, either endpoint optionally null. The generated schema in the preview shows the complete structure for your selected fields.

The profile hash includes the resolved prompt, including the host instructions. Jobs retain the registered profile/prompt version; editing a file does not silently change already queued work. Register an updated policy or explicitly queue/run the updated profile to use it. An incompatible prompt-engine update requires deliberate requeueing.

## 2. Try N random documents before migration

```text
python -m local_search.metadata --config local-search.json run --profile metadata-profile.json --dry-run --random-items 20 --seed 42 --output-dir data/metadata-trial-01
```

This performs real local inference but makes no metadata/schema/job/revision changes in the library. It works on an unmigrated library. A managed network library must already have been imported locally by the app; the trial reads that local copy without changing the registry.

Use `--collection NAME` or `--folder PATH` to scope the population; either can repeat. Folder matching follows path boundaries. Sampling is without replacement, deduplicated by existing canonical document identity after scope filtering. `--source-sampling` samples individual source occurrences instead. A smaller eligible population is reported honestly. The seed and exact source identities/content hashes are saved; omit the seed for a fresh random sample. The default is 20 items. Only explicit `--all` selects the entire eligible population.

Open `report.html` in the output directory. It links to each item's proposed fields, exact excerpts, raw outputs, validation issues and recovery attempts/requests. `metrics.json` reports first-attempt validity, repair use, unknowns, failures and median/p95 item times. The manifest records the sample/profile/model identity and model startup separately. Local models are reused within a batch; loaded artifacts are fingerprinted once.

Start with a mixed sample and inspect each field's correctness. Successful JSON parsing alone is insufficient. Missing/rejected PDF text is not reconstructed; long documents use bounded front/back excerpts and can legitimately produce unknowns.

## JSON errors and recovery

Every item gets at most three calls total, configurable downwards, within its input/output and cooperative time budget. The default item budget is 180 seconds, configurable in the profile.

- Malformed JSON is caught, recorded and sent to a fresh formatting-repair request containing the schema, candidate and error, without the document or earlier conversation.
- Formatting repair temporarily disables thinking and uses schema-constrained generation when supported. It is revalidated against the original retained excerpts. Repaired output needs review because value preservation cannot be proven for malformed input.
- Type/date/evidence failures can trigger a targeted retry using the bounded original excerpts and validation feedback. Truncation is handled as incomplete extraction, rather than pretending formatting can recover missing facts.
- Exhaustion marks that item failed or needing review and continues to the next item. Invalid evidence is not committed as a valid field. Original attempts, outputs, prompts and timings remain reviewable.

Ordinary extraction with thinking enabled leaves the thinking region unconstrained; thinking-off generation can use the installed llama.cpp JSON-schema grammar. Each attempt records whether constraints were active. Cancellation/time limits are cooperative: native prompt processing may not stop immediately.

## 3. Inspect and migrate the working library

Close the app and other library users before applying migration. For a managed library, the sidecar needs sole ownership of the local working copy. Read-only inspection/trials can coexist with the app, provided its GGUF is unloaded before another model starts.

```text
python -m local_search.metadata --config local-search.json inspect
python -m local_search.metadata --config local-search.json migrate
python -m local_search.metadata --config local-search.json migrate --apply
```

The first two commands are previews. Apply makes and validates a consistent pre-migration backup, takes an exclusive SQLite maintenance lock, then transactionally adds stable source UUIDs, profiles, jobs, results/history and manual overrides. Core schema becomes 2, with a separate metadata schema version. Repeating the migration is a no-op. Existing source IDs, text, passages, FTS and dates are preserved. Migration does not queue or extract the existing library.

The reported recovery backup is normally `before-metadata.db`; an existing backup is retained and a unique filename used instead. Old application versions reject schema 2. Use the current application after migration.

## 4. Apply the reviewed trial without generating again

```text
python -m local_search.metadata --config local-search.json apply-report --report-dir data/metadata-trial-01
python -m local_search.metadata --config local-search.json apply-report --report-dir data/metadata-trial-01 --apply
```

The first command previews. Apply rechecks source identity/content, the profile and the exact excerpt text against the current index, then saves valid proposed fields with their review status. No model call is made. Changed/deleted sources are stale, and invalid candidates are skipped/refused. Existing manual overrides are preserved.

After manually reviewing the candidates, `--apply --accept-reviewed` explicitly marks valid imported values accepted. Without that flag, the starter's values remain needing review. `--force` is required to replace an already processed job through this route; it still cannot override manual corrections.

Alternatively, rerun extraction over the same sample using `run --manifest data/metadata-trial-01/manifest.json --profile metadata-profile.json --output-dir data/metadata-write-01`. That generates new outputs for the same unchanged items; it does not promote the earlier suggestions. Successful unchanged jobs are skipped unless deliberately reprocessed or the model identity changed.

## 5. Queue existing or newly ingested documents

Preview a scoped historical backfill before applying it:

```text
python -m local_search.metadata --config local-search.json queue --profile metadata-profile.json --collection papers --all
python -m local_search.metadata --config local-search.json queue --profile metadata-profile.json --collection papers --all --apply
```

Opt into automatic queueing for an ingestion collection:

```text
python -m local_search.metadata --config local-search.json policy --collection papers --profile metadata-profile.json --mode changed
```

Modes are `off`, `missing` and `changed`. These add lightweight durable jobs in the ingest transaction; they never run a model during ingestion. Rolled-back ingestion leaves no job, unchanged refreshes do not duplicate jobs, and content/identity changes invalidate stale work. Deletion/history and manual corrections are kept separately from current source rows.

Run the separate worker when convenient:

```text
python -m local_search.metadata --config local-search.json work --limit 20 --output-dir data/metadata-worker-01
python -m local_search.metadata --config local-search.json work --limit 20 --retry-failed --output-dir data/metadata-worker-retry-01
```

It processes up to that many pending/interrupted jobs, reuses one loaded model, commits short transactions after validation, and exits. It is not an automatically started daemon. Repeat it for subsequent batches; a higher explicit limit supports bulk work after the pilot. Ctrl+C stops the process; interrupted running jobs can resume on the next invocation. Retry-failed is explicit.

The desktop, normal CLI and metadata worker share a GGUF ownership lock to avoid loading two large models accidentally. On managed libraries, close the desktop while the write worker owns the library. Full concurrent app/worker scheduling is a later extension. Reopen afterwards to search and synchronise pending metadata changes, or invoke `library-sync` explicitly.

## Review and database tools

Open the separate review window from the main app's Settings → Metadata review button, run `run-metadata-review.ps1`, or use:

```text
python -m local_search.metadata --config local-search.json gui
```

The window starts with `needs_review` findings. Select a report, inspect its proposed fields and supporting quotes, and edit values as needed. Lists and date intervals have structured editors. Save draft keeps the finding awaiting review; Accept finding validates and accepts it. Fields you edit become protected manual overrides on acceptance. Evidence, original excerpts, model attempts and the exact extraction prompt remain available alongside the fields. No model is loaded by this window.

The review window can stay open alongside the main app. Saves use short transactions and reject findings changed elsewhere or based on changed/deleted sources. Close review windows before migrations, restores or replacing a working library. The database must already have its metadata migration applied; opening review does not migrate it or start extraction.

```text
python -m local_search.metadata --config local-search.json jobs --status needs_review
python -m local_search.metadata --config local-search.json review --job JOB_ID
python -m local_search.metadata --config local-search.json review --job JOB_ID --accept
python -m local_search.metadata --config local-search.json values --source-id SOURCE_ID
python -m local_search.metadata --config local-search.json validate
python -m local_search.metadata --config local-search.json backup --output data/library-backup.db
python -m local_search.metadata --config local-search.json restore --backup data/library-backup.db
python -m local_search.metadata --config local-search.json restore --backup data/library-backup.db --apply
```

Review acceptance refuses outstanding validation issues or changed source content. `set-field --source-id SOURCE_ID --profile metadata-profile.json --field FIELD --value-json JSON_OBJECT` stores a typed manual override; for example a string uses `{"value":"Corrected publisher","evidence":[]}`. Pass JSON using the quoting rules for your shell. Manual fields are protected against model reprocessing, and values show whether their content version is stale.

Backup chooses a new local output file and does not overwrite an existing file. Restore is a preview unless `--apply` is supplied; it requires library users to close, retains a recovery backup of current contents and refuses a known different-library UUID. Managed restore changes activation/revision so the rollback is pending for publication rather than silently considered synced. Copy a closed network backup locally before restore. A corrupt active database that cannot pass ownership/open checks needs separate manual recovery from a verified backup.

Reports, model/source paths and raw excerpts are private local artifacts. Generic code/profile examples are publishable; actual field configurations, selections, libraries and reports are excluded from the source release.

Extracted dates are now stored as metadata. The derived Information date search option and relevance/recency experiments remain separate roadmap work.

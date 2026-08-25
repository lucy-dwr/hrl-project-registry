# hrl-project-registry

CSV validation and immutable publishing tools for program-assigned HRL restoration project IDs.

## Local development

The project uses Python 3.11+ and pinned dependencies:

```sh
python -m pip install -e '.[dev]'
pytest
ruff check .
```

Do not commit local database files, production IDs, exports, credentials, or `.env` files.

## Azure job contract

An administrator uploads a complete private source revision containing
`project-id-registry.csv`, `registry-audit.csv`, and `_READY` last. Event Grid filters for
`_READY`, queues the source-revision path, and starts this container as a Container Apps Job.
The job writes a validation report and creates an `AWAITING_APPROVAL` candidate only if the
source and its change from the preceding revision are valid.

```sh
hrl-project-registry process-ready-source \
  --source-directory /work/source/2026-08-25 \
  --previous-directory /work/source/2026-08-24 \
  --report-root /work/registry-validation-reports \
  --candidate-root /work/registry-export-candidates
```

The local command has no Azure credentials in source code. The production job requires a
managed-identity queue/storage adapter to download the queued source revision and preceding
approved revision, invoke this command, upload outputs, and acknowledge the queue message.
Promotion after an `_APPROVE` marker is a separate job.

Use an ISO date (`YYYY-MM-DD`) as the normal source and export version. If a
same-day correction is necessary, use `YYYY-MM-DD-r2`, then `-r3`, rather than
overwriting the earlier revision. The job records its own UTC generation time
in manifests and reports.

## Updating the registry

Only authorized registry administrators may update the private source files.
Public exports are read-only and are never an update channel.

1. In Azure Portal, download `project-id-registry.csv` and `registry-audit.csv`
   from the latest approved source revision under
   `registry-admin/project-id-registry/source-revisions/`.
2. Create a new local revision directory named with today's date, for example
   `2026-08-26`. If a revision already exists for that date, use `2026-08-26-r2`.
3. Keep every existing registry row. To allocate a project, append one row to
   `project-id-registry.csv`:

   ```csv
   HRL-003,eligible,Example restoration project,DWR,2026-08-26,
   ```

4. Append the corresponding audit row to `registry-audit.csv`. The
   `source_revision` must exactly match the new directory name:

   ```csv
   2026-08-26,registry-admin,allocated,HRL-003,Approved program allocation
   ```

5. Upload both complete CSV files to:

   ```text
   registry-admin/project-id-registry/source-revisions/2026-08-26/
   ```

6. Upload an empty file named `_READY` **last**. Do not overwrite an earlier
   source revision. The Azure-triggered validation job starts only when this
   marker arrives.
7. Review the generated private validation report. If it failed, correct the
   files locally and upload a new date revision; do not alter the failed one.
8. If it passed, review the `AWAITING_APPROVAL` candidate. An authorized
   reviewer uploads `_APPROVE` to request promotion to the immutable private
   export and, when approved, the sanitized public export.

The validator rejects missing or duplicate IDs, removed prior IDs, invalid
statuses, invalid supersession targets, malformed dates, and lifecycle changes
without a matching audit entry in the new source revision.

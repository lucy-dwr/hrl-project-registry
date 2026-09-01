# hrl-project-registry

The authoritative list of program-assigned HRL restoration project IDs, kept as
one reviewed CSV under version control.

- **Need a new project ID?** &rarr; [Adding a project ID](#adding-a-project-id)
  (the data operator can do this; no coding needed).
- **Retiring or superseding an ID?** &rarr;
  [Retiring or superseding a project ID](#retiring-or-superseding-a-project-id)
  (technical maintainer only).
- **Roles and ownership:**
  [`hrl-azure-infrastructure/DIVISION_OF_RESPONSIBILITIES.md`](https://github.com/Healthy-Rivers-and-Landscapes-Science/hrl-azure-infrastructure/blob/main/DIVISION_OF_RESPONSIBILITIES.md).
- **Cross-repository workflow reference:**
  [`hrl-azure-infrastructure/PIPELINE_INFRA.md`](https://github.com/Healthy-Rivers-and-Landscapes-Science/hrl-azure-infrastructure/blob/main/PIPELINE_INFRA.md).

## The registry

`project-id-registry.csv` is the system of record. Columns:

```text
project_id,status,project_name,assigned_organization_code,assigned_at,superseded_by_project_id
```

- `project_id` - `HRL-` plus a zero-padded number, e.g. `HRL-003`. Never reused,
  never removed.
- `status` - `eligible`, `retired`, or `superseded`. `retired` and `superseded`
  are terminal.
- `assigned_at` - ISO date; immutable once set.
- `superseded_by_project_id` - required when and only when `status` is
  `superseded`; must point at an `eligible` ID.

There is no separate audit file, no database, no immutable export, and no
Azure service. **The pull request that changes the CSV is the audit record** -
put the who and the why in the PR description and commit message.

## Adding a project ID

A submission is blocked until the project ID it uses exists here as an
`eligible` row. The data operator can add it directly through the GitHub
website - no local setup, no command line.

1. Open
   [`project-id-registry.csv`](project-id-registry.csv) on GitHub and click the
   pencil (**Edit this file**).
2. Add **one row at the end**, using the next number in sequence. Columns are
   `project_id,status,project_name,assigned_organization_code,assigned_at,superseded_by_project_id`.

   ```diff
     HRL-036,eligible,Upper Rose Bar Habitat Enhancement Project,YWA,2026-08-24,
   + HRL-037,eligible,Cache Slough Tidal Restoration,DWR,2026-09-15,
   ```

   - `project_id` - `HRL-` plus the next zero-padded number. Never reuse a
     number, even one that was retired.
   - `status` - `eligible`.
   - `project_name` - the project's name as the program refers to it.
   - `assigned_organization_code` - the lead organization's uppercase
     abbreviation. Use the same code that appears in the schema's lead-entity
     catalog (`LeadEntityEnum` in
     [`hrl-restoration-schema`](https://github.com/Healthy-Rivers-and-Landscapes-Science/hrl-restoration-schema)).
   - `assigned_at` - today's date, `YYYY-MM-DD`.
   - `superseded_by_project_id` - leave empty.
3. Below the editor, choose **Create a new branch and start a pull request**.
   In the description, write **who asked for the ID and why** (this is the
   entire audit record).
4. CI validates the file automatically. When it passes and the technical
   maintainer approves and merges, `git pull` the registry locally and re-run
   `hrl-pipeline`.

## Retiring or superseding a project ID

**Technical maintainer only.** These are terminal status changes with rules the
validator enforces.

- **Retire** an ID that will never be used: change only its `status` to
  `retired`.

  ```diff
  - HRL-030,eligible,Duplicate entry for River Bend,WF,2026-08-24,
  + HRL-030,retired,Duplicate entry for River Bend,WF,2026-08-24,
  ```

- **Supersede** an ID that has been replaced by another: set its `status` to
  `superseded` and put the replacement ID in `superseded_by_project_id`. The
  replacement must already exist as an `eligible` row.

  ```diff
  - HRL-031,eligible,Upper River Bend (Phase 1),WF,2026-08-24,
  + HRL-031,superseded,Upper River Bend (Phase 1),WF,2026-08-24,HRL-032
  ```

Keep every other row byte-for-byte. `assigned_at` is immutable. A `retired` or
`superseded` ID cannot revert or change again. Open a pull request with the
authorization and reason.

## Who reviews changes

The **technical maintainer** reviews and merges every pull request against
`project-id-registry.csv` - including the operator's ID additions. A
blocked submission is waiting on that merge, so treat it as time-sensitive. See
the owner table in
[`hrl-azure-infrastructure/MAINTENANCE.md`](https://github.com/Healthy-Rivers-and-Landscapes-Science/hrl-azure-infrastructure/blob/main/MAINTENANCE.md).

The restoration pipeline reads this file pinned at a specific commit; it never
follows `main`, so a merge does not take effect until the operator pulls it.

## Local development

Python 3.11+, no runtime dependencies.

```sh
python -m pip install -e '.[dev]'   # needs pip >= 23.1 for a PEP 660 editable install
pytest
ruff check .
```

Validate the registry the way CI does:

```sh
hrl-project-registry validate project-id-registry.csv
git show origin/main:project-id-registry.csv > /tmp/base.csv
hrl-project-registry validate project-id-registry.csv --base /tmp/base.csv
```

## What this repo does not do

It does not allocate IDs automatically, host an API, run in Azure, or produce
XLSX/JSON exports. The map and other consumers read `project_id`, `status`, and
`project_name` straight from this CSV (or from a snapshot the pipeline pins).

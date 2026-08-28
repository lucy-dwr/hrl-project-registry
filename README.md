# hrl-project-registry

The authoritative list of program-assigned HRL restoration project IDs, kept as
one reviewed CSV under version control.

Cross-repository workflow reference:
[`hrl-azure-infrastructure/PIPELINE_INFRA.md`](https://github.com/lucy-dwr/hrl-azure-infrastructure/blob/main/PIPELINE_INFRA.md).

## The registry

`project-id-registry.csv` is the system of record. Columns:

```text
project_id,status,project_name,assigned_organization_code,assigned_at,superseded_by_project_id
```

- `project_id` — `HRL-` plus a zero-padded number, e.g. `HRL-003`. Never reused,
  never removed.
- `status` — `eligible`, `retired`, or `superseded`. `retired` and `superseded`
  are terminal.
- `assigned_at` — ISO date; immutable once set.
- `superseded_by_project_id` — required when and only when `status` is
  `superseded`; must point at an `eligible` ID.

There is no separate audit file, no database, no immutable export, and no
Azure service. **The pull request that changes the CSV is the audit record** —
put the who and the why in the PR description and commit message.

## Changing the registry

1. Branch. Edit `project-id-registry.csv`.
   - Allocate an ID: add one row with the next `HRL-NNN`, `status=eligible`,
     the project name, the lead organization's abbreviation, and today's date.
   - Retire or supersede an ID: change only its `status` (and
     `superseded_by_project_id` for a supersession). Keep every other row
     byte-for-byte.
2. Open a PR. Describe who authorized the change and why.
3. CI runs `hrl-project-registry validate` on the file and on the change from
   `origin/main`. Merge when it passes and a maintainer approves.

The restoration pipeline reads this file pinned at a specific commit; it never
follows `main`.

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

# Agent instructions: `hrl-project-registry`

## What this repo is

The authoritative list of program-assigned HRL project IDs, kept as a single
reviewed CSV: `project-id-registry.csv`. Plus a small Python validator run in
CI. That is the whole repository.

Cross-repository context is in
[`hrl-azure-infrastructure/PIPELINE_INFRA.md`](https://github.com/Healthy-Rivers-and-Landscapes-Science/hrl-azure-infrastructure/blob/main/PIPELINE_INFRA.md);
role ownership is in
[`hrl-azure-infrastructure/DIVISION_OF_RESPONSIBILITIES.md`](https://github.com/Healthy-Rivers-and-Landscapes-Science/hrl-azure-infrastructure/blob/main/DIVISION_OF_RESPONSIBILITIES.md).

The data operator may add `eligible` rows through the GitHub web UI and open a
pull request; the technical maintainer reviews and merges every change and does
all retirements and supersessions. See the README.

## History (do not rebuild)

This repo previously contained an Azure service: source-revision directories,
an append-only `registry-audit.csv`, Container Apps queue workers
(`azure_worker.py`), a Dockerfile, immutable CSV/JSON/XLSX exports, a
`current.json` pointer, and an image-release workflow. All of that was removed
in favour of one version-controlled CSV whose audit trail is the pull-request
history. Do not reintroduce it.

## Rules

1. `project_id` values are never reused or removed. `assigned_at` is immutable.
   `retired` and `superseded` are terminal statuses.
2. Every registry change is a pull request; the PR description carries the
   authorization and reason. There is no audit file.
3. Keep the validator dependency-free (standard library only) and deterministic.
4. Use small synthetic fixtures in tests. American spellings.
5. Run `pytest` and `ruff check .` before finishing.

## Layout

```text
project-id-registry.csv              the registry
src/hrl_project_registry/registry.py validation logic (load, validate_registry, validate_change)
src/hrl_project_registry/cli.py      `hrl-project-registry validate [PATH] [--base PATH]`
tests/test_registry.py
.github/workflows/ci.yml             test + lint + validate the CSV against origin/main
```

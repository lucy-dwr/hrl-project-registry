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

## Releasing the container image

The `Release container image` workflow is manual and uses GitHub Actions OIDC
workload identity; it does not use an ACR password, client secret, or a stored
registry credential. Before enabling it, configure the protected
`acr-publish` GitHub Environment with required reviewers and these environment
variables:

```text
ACR_NAME                 # Registry resource name, without .azurecr.io
ACR_LOGIN_SERVER         # For example: example.azurecr.io
AZURE_CLIENT_ID          # Federated workload identity client ID
AZURE_TENANT_ID
AZURE_SUBSCRIPTION_ID
```

Grant that federated identity the narrowly scoped ACR publishing role approved
by the platform team. Bind its GitHub OIDC federated credential to this
repository's `acr-publish` environment, and protect both that environment and
the default branch with the required reviewers. The workflow rejects releases
from any other branch. It runs the tests and lint, builds the image, then
pushes a commit-tagged image and records the resolved immutable
`<registry>/hrl-project-registry@sha256:...` reference. It uploads that
reference and release provenance as the `image-release-handoff` artifact.

Use the artifact's digest reference in a reviewed pull request to the Azure
infrastructure repository. That repository alone updates the Container Apps
Job Terraform input. Do not place a tag such as `:main`, `:latest`, or even
the commit tag in Terraform, and do not grant this repository credentials or
permissions to update a running job.

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

The production validation entry point is `consume-validation-queue`. It uses
`DefaultAzureCredential`, so the Container Apps Job must use its managed identity and an
`HRL_STORAGE_ACCOUNT_URL` such as `https://<account>.blob.core.windows.net`; no connection
string is stored in this repository. It downloads the queued source revision and preceding
approved revision, creates private outputs, then acknowledges the queue message.

```sh
hrl-project-registry consume-validation-queue \
  --queue registry-validation-requests \
  --source-container registry-admin \
  --report-container registry-validation-reports \
  --candidate-container registry-export-candidates \
  --export-container registry-exports
```

The Event Grid delivery must contain exactly one `Microsoft.Storage.BlobCreated` event whose
subject and `data.url` both identify:

```text
registry-admin/project-id-registry/source-revisions/<YYYY-MM-DD[-rN]>/_READY
```

The worker treats `status.json` as the final write for reports and candidates. Duplicate
messages see that marker and are acknowledged without changing a source, candidate, or export.
If a job stops before the final marker is written, a retry completes the same immutable paths.
The source revision is read-only after `_READY` and the worker never overwrites it.

The promotion entry point receives one Event Grid `_APPROVE` message from the
separate registry-promotion queue. It verifies the marker path, stages the
candidate, uses the same checksum-verified promotion logic as the local
command, uploads an immutable export, then conditionally updates `current.json`.
It acknowledges the queue message only after that outcome is durable.

```sh
hrl-project-registry consume-promotion-queue \
  --queue registry-promotion-requests \
  --candidate-container registry-export-candidates \
  --export-container registry-exports
```

Use an ISO date (`YYYY-MM-DD`) as the normal source and export version. If a
same-day correction is necessary, use `YYYY-MM-DD-r2`, then `-r3`, rather than
overwriting the earlier revision. The job records its own generation date
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

## Promotion boundary

Validation never publishes an export. A separate, authorized promotion run validates a
reviewer-created `_APPROVE` file in the candidate directory, then runs:

```sh
hrl-project-registry promote \
  --candidate-directory /work/registry-export-candidates/project-id-registry/2026-08-26 \
  --export-root /work/registry-exports
```

`_APPROVE` must be a JSON object with `export_version`, `approved_by`, `approved_on` (an ISO
date), and `candidate_manifest_sha256`. The command checks the candidate artifacts and manifest
before creating the immutable versioned export and then updates the mutable `current.json`
pointer. Export CSV, JSON, XLSX, and manifest files are sanitized: they never include audit rows,
approval identities, or source-revision metadata. Azure Storage's authenticated write audit trail
is the approval identity record. Its Storage Queue/Event Grid adapter is intentionally a later
deployment task.

The validator rejects missing or duplicate IDs, removed prior IDs, invalid
statuses, invalid supersession targets, malformed dates, and lifecycle changes
without a matching audit entry in the new source revision.

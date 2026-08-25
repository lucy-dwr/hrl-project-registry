from datetime import date
from pathlib import Path

import hashlib
import json

import pytest

from hrl_project_registry.azure_worker import RegistryValidationWorker
from hrl_project_registry.registry import process_ready_source, promote_candidate, validate_source_revision

REGISTRY_HEADER = "project_id,status,project_name,assigned_organization_code,assigned_at,superseded_by_project_id\n"
AUDIT_HEADER = "source_revision,actor,action,project_id,reason\n"


def write_revision(path: Path, registry_rows: str, audit_rows: str) -> None:
    path.mkdir()
    (path / "project-id-registry.csv").write_text(REGISTRY_HEADER + registry_rows)
    (path / "registry-audit.csv").write_text(AUDIT_HEADER + audit_rows)
    (path / "_READY").touch()


def test_ready_source_creates_a_candidate_and_report(tmp_path: Path):
    source = tmp_path / "2026-08-25"
    write_revision(
        source,
        "HRL-001,eligible,Example project,DWR,2026-08-25,\n",
        "2026-08-25,registry-admin,allocated,HRL-001,Initial allocation\n",
    )
    result = process_ready_source(
        source_directory=source,
        report_root=tmp_path / "reports",
        candidate_root=tmp_path / "candidates",
        generated_on=date(2026, 8, 25),
    )
    candidate = tmp_path / "candidates/project-id-registry/2026-08-25"
    assert not result.errors
    assert (candidate / "project-id-registry.json").is_file()
    assert (candidate / "candidate-manifest.json").is_file()
    assert json.loads((candidate / "status.json").read_text())["status"] == "AWAITING_APPROVAL"
    assert (tmp_path / "reports/project-id-registry/2026-08-25/validation-report.json").is_file()
    assert not process_ready_source(
        source_directory=source,
        report_root=tmp_path / "reports",
        candidate_root=tmp_path / "candidates",
        generated_on=date(2026, 8, 25),
    ).errors


def test_candidate_promotion_is_immutable_and_idempotent(tmp_path: Path):
    source = tmp_path / "2026-08-25"
    write_revision(source, "HRL-001,eligible,Example project,DWR,2026-08-25,\n", "2026-08-25,registry-admin,allocated,HRL-001,Initial allocation\n")
    process_ready_source(source_directory=source, report_root=tmp_path / "reports", candidate_root=tmp_path / "candidates", generated_on=date(2026, 8, 25))
    candidate = tmp_path / "candidates/project-id-registry/2026-08-25"
    digest = hashlib.sha256((candidate / "candidate-manifest.json").read_bytes()).hexdigest()
    (candidate / "_APPROVE").write_text(json.dumps({"export_version": "2026-08-25", "approved_by": "registry-admin", "approved_on": "2026-08-25", "candidate_manifest_sha256": digest}))
    destination = promote_candidate(candidate_directory=candidate, export_root=tmp_path / "exports")
    assert (destination / "manifest.json").is_file()
    assert promote_candidate(candidate_directory=candidate, export_root=tmp_path / "exports") == destination


def test_invalid_source_writes_an_idempotent_correction_report(tmp_path: Path):
    source = tmp_path / "2026-08-25"
    source.mkdir()
    (source / "_READY").touch()
    result = process_ready_source(
        source_directory=source,
        report_root=tmp_path / "reports",
        candidate_root=tmp_path / "candidates",
        generated_on=date(2026, 8, 25),
    )
    assert "project-id-registry.csv" in " ".join(result.errors)
    assert json.loads(
        (tmp_path / "reports/project-id-registry/2026-08-25/status.json").read_text()
    ) == {"status": "NEEDS_CORRECTION"}
    assert process_ready_source(
        source_directory=source,
        report_root=tmp_path / "reports",
        candidate_root=tmp_path / "candidates",
        generated_on=date(2026, 8, 25),
    ).errors == result.errors


def test_registry_worker_accepts_only_the_expected_ready_event():
    worker = object.__new__(RegistryValidationWorker)
    worker.source_container = "registry-admin"
    worker.source_prefix = "project-id-registry/source-revisions"
    payload = json.dumps(
        [{
            "eventType": "Microsoft.Storage.BlobCreated",
            "subject": "/blobServices/default/containers/registry-admin/blobs/"
            "project-id-registry/source-revisions/2026-08-25/_READY",
            "data": {
                "url": "https://example.blob.core.windows.net/registry-admin/"
                "project-id-registry/source-revisions/2026-08-25/_READY"
            },
        }]
    )
    assert worker._event_version(payload) == "2026-08-25"
    malformed = payload.replace("/_READY", "/project-id-registry.csv")
    with pytest.raises(ValueError):
        worker._event_version(malformed)


def test_source_rejects_a_removed_or_reused_id(tmp_path: Path):
    previous = tmp_path / "2026-08-24"
    write_revision(
        previous,
        "HRL-001,retired,Example project,DWR,2026-08-24,\n",
        "2026-08-24,registry-admin,allocated,HRL-001,Initial allocation\n",
    )
    source = tmp_path / "2026-08-25"
    write_revision(
        source,
        "HRL-002,eligible,Other project,DWR,2026-08-25,\n",
        "2026-08-25,registry-admin,allocated,HRL-002,Initial allocation\n",
    )
    result = validate_source_revision(source, previous)
    assert "may not disappear" in " ".join(result.errors)


def test_supersession_requires_current_audit_event(tmp_path: Path):
    previous = tmp_path / "2026-08-24"
    write_revision(
        previous,
        "HRL-001,eligible,Example project,DWR,2026-08-24,\nHRL-002,eligible,Replacement,DWR,2026-08-24,\n",
        "2026-08-24,registry-admin,allocated,HRL-001,Initial allocation\n2026-08-24,registry-admin,allocated,HRL-002,Initial allocation\n",
    )
    source = tmp_path / "2026-08-25"
    write_revision(
        source,
        "HRL-001,superseded,Example project,DWR,2026-08-24,HRL-002\nHRL-002,eligible,Replacement,DWR,2026-08-24,\n",
        "2026-08-24,registry-admin,allocated,HRL-001,Initial allocation\n",
    )
    result = validate_source_revision(source, previous)
    assert "supersession needs a current-revision audit event" in " ".join(result.errors)

from datetime import date
from pathlib import Path

import hashlib
import json

import pytest

from hrl_project_registry.azure_worker import RegistryPromotionWorker, RegistryValidationWorker
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
    manifest = json.loads((destination / "manifest.json").read_text())
    assert set(manifest) == {
        "source_registry",
        "registry_contract_version",
        "export_version",
        "generated_on",
        "row_count",
        "checksums",
    }
    assert "registry-admin" not in (destination / "manifest.json").read_text()
    assert "Initial allocation" not in (destination / "project-id-registry.csv").read_text()
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


def test_registry_promotion_worker_accepts_only_the_expected_approval_event():
    worker = object.__new__(RegistryPromotionWorker)
    worker.candidate_container = "registry-export-candidates"
    worker.candidate_prefix = "project-id-registry"
    payload = json.dumps(
        [{
            "eventType": "Microsoft.Storage.BlobCreated",
            "subject": "/blobServices/default/containers/registry-export-candidates/blobs/"
            "project-id-registry/2026-08-25/_APPROVE",
            "data": {
                "url": "https://example.blob.core.windows.net/registry-export-candidates/"
                "project-id-registry/2026-08-25/_APPROVE"
            },
        }]
    )
    assert worker._event_version(payload) == "2026-08-25"
    malformed = payload.replace("/_APPROVE", "/status.json")
    with pytest.raises(ValueError):
        worker._event_version(malformed)


class _Blob:
    def __init__(self, service, container: str, name: str):
        self.service = service
        self.container = container
        self.name = name

    def exists(self):
        return (self.container, self.name) in self.service.data

    def download_blob(self):
        return self

    def readall(self):
        return self.service.data[(self.container, self.name)]

    def upload_blob(self, data, **_kwargs):
        self.service.data[(self.container, self.name)] = data

    def get_blob_properties(self):
        return type("Properties", (), {"etag": "test-etag"})()


class _Container:
    def __init__(self, service, container: str):
        self.service = service
        self.container = container

    def list_blobs(self, name_starts_with: str):
        return [
            type("BlobItem", (), {"name": name})()
            for container, name in self.service.data
            if container == self.container and name.startswith(name_starts_with)
        ]

    def get_blob_client(self, name: str):
        return _Blob(self.service, self.container, name)


class _BlobService:
    def __init__(self):
        self.data = {}

    def get_blob_client(self, container: str, name: str):
        return _Blob(self, container, name)

    def get_container_client(self, container: str):
        return _Container(self, container)


class _Queue:
    def __init__(self, messages):
        self.messages = messages
        self.deleted = []

    def receive_messages(self, **_kwargs):
        return self.messages[:1]

    def delete_message(self, message_id, pop_receipt):
        self.deleted.append((message_id, pop_receipt))


def test_registry_promotion_worker_promotes_and_acknowledges_duplicate(tmp_path: Path):
    source = tmp_path / "2026-08-25"
    write_revision(
        source,
        "HRL-001,eligible,Example project,DWR,2026-08-25,\n",
        "2026-08-25,registry-admin,allocated,HRL-001,Initial allocation\n",
    )
    process_ready_source(
        source_directory=source,
        report_root=tmp_path / "reports",
        candidate_root=tmp_path / "candidates",
        generated_on=date(2026, 8, 25),
    )
    candidate = tmp_path / "candidates/project-id-registry/2026-08-25"
    digest = hashlib.sha256((candidate / "candidate-manifest.json").read_bytes()).hexdigest()
    (candidate / "_APPROVE").write_text(
        json.dumps(
            {
                "export_version": "2026-08-25",
                "approved_by": "registry-admin",
                "approved_on": "2026-08-25",
                "candidate_manifest_sha256": digest,
            }
        )
    )
    event = json.dumps(
        [{
            "eventType": "Microsoft.Storage.BlobCreated",
            "subject": "/blobServices/default/containers/registry-export-candidates/blobs/"
            "project-id-registry/2026-08-25/_APPROVE",
            "data": {
                "url": "https://example.blob.core.windows.net/registry-export-candidates/"
                "project-id-registry/2026-08-25/_APPROVE"
            },
        }]
    )
    blobs = _BlobService()
    for path in candidate.iterdir():
        blobs.data[("registry-export-candidates", f"project-id-registry/2026-08-25/{path.name}")] = path.read_bytes()
    message = type("Message", (), {"content": event, "id": "message-1", "pop_receipt": "receipt-1"})()
    queue = _Queue([message])
    worker = object.__new__(RegistryPromotionWorker)
    worker.blobs = blobs
    worker.queue = queue
    worker.candidate_container = "registry-export-candidates"
    worker.export_container = "registry-exports"
    worker.candidate_prefix = "project-id-registry"

    assert worker.process_one()
    assert queue.deleted == [("message-1", "receipt-1")]
    assert json.loads(
        blobs.data[("registry-exports", "project-id-registry/current.json")]
    ) == {"export_version": "2026-08-25", "manifest": "2026-08-25/manifest.json"}
    assert ("registry-exports", "project-id-registry/2026-08-25/manifest.json") in blobs.data

    duplicate = type("Message", (), {"content": event, "id": "message-2", "pop_receipt": "receipt-2"})()
    worker.queue = _Queue([duplicate])
    assert worker.process_one()
    assert worker.queue.deleted == [("message-2", "receipt-2")]


def test_registry_promotion_worker_leaves_failed_message_unacknowledged():
    event = json.dumps(
        [{
            "eventType": "Microsoft.Storage.BlobCreated",
            "subject": "/blobServices/default/containers/registry-export-candidates/blobs/"
            "project-id-registry/2026-08-25/_APPROVE",
            "data": {
                "url": "https://example.blob.core.windows.net/registry-export-candidates/"
                "project-id-registry/2026-08-25/_APPROVE"
            },
        }]
    )
    message = type("Message", (), {"content": event, "id": "message-1", "pop_receipt": "receipt-1"})()
    worker = object.__new__(RegistryPromotionWorker)
    worker.blobs = _BlobService()
    worker.queue = _Queue([message])
    worker.candidate_container = "registry-export-candidates"
    worker.export_container = "registry-exports"
    worker.candidate_prefix = "project-id-registry"

    with pytest.raises(ValueError, match="_APPROVE marker is no longer present"):
        worker.process_one()
    assert worker.queue.deleted == []


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


@pytest.mark.parametrize(
    ("version", "previous_version", "expected"),
    [
        ("2026-02-30", None, "real ISO date"),
        ("2026-08-25-r2", "2026-08-24", "new day"),
        ("2026-08-25-r3", "2026-08-25", "increment consecutively"),
        ("2026-08-25-r3", "2026-08-25-r2", None),
    ],
)
def test_source_revision_names_and_same_day_revisions(
    tmp_path: Path, version: str, previous_version: str | None, expected: str | None
):
    previous = None
    if previous_version:
        previous = tmp_path / previous_version
        write_revision(
            previous,
            "HRL-001,eligible,Example project,DWR,2026-08-24,\n",
            f"{previous_version},registry-admin,allocated,HRL-001,Initial allocation\n",
        )
    source = tmp_path / version
    write_revision(
        source,
        "HRL-001,eligible,Example project,DWR,2026-08-24,\n",
        f"{version},unrecognized-local-actor,allocated,HRL-001,Initial allocation\n",
    )
    result = validate_source_revision(source, previous)
    assert (expected is None) == (not result.errors)
    if expected:
        assert expected in " ".join(result.errors)


def test_validation_does_not_depend_on_authorization_lookup(tmp_path: Path):
    source = tmp_path / "2026-08-25"
    write_revision(
        source,
        "HRL-001,eligible,Example project,DWR,2026-08-25,\n",
        "2026-08-25,offline-test-actor,allocated,HRL-001,Initial allocation\n",
    )
    assert not validate_source_revision(source).errors


def test_conditional_pointer_update_preserves_a_newer_export(tmp_path: Path):
    blobs = _BlobService()
    worker = object.__new__(RegistryPromotionWorker)
    worker.blobs = blobs
    worker.export_container = "registry-exports"
    worker.candidate_prefix = "project-id-registry"
    existing = {"export_version": "2026-08-25-r3", "manifest": "2026-08-25-r3/manifest.json"}
    blobs.data[("registry-exports", "project-id-registry/current.json")] = json.dumps(existing).encode()
    pointer = tmp_path / "current.json"
    pointer.write_text(json.dumps({"export_version": "2026-08-25-r2", "manifest": "2026-08-25-r2/manifest.json"}))

    with pytest.raises(ValueError, match="older export"):
        worker._upload_current_pointer(pointer, "2026-08-25-r2")

    assert json.loads(blobs.data[("registry-exports", "project-id-registry/current.json")]) == existing

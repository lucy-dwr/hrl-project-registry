from datetime import datetime, timezone
from pathlib import Path

from hrl_project_registry.registry import process_ready_source, validate_source_revision

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
        generated_at=datetime(2026, 8, 25, 1, 20, tzinfo=timezone.utc),
    )
    candidate = tmp_path / "candidates/project-id-registry/2026-08-25"
    assert not result.errors
    assert (candidate / "project-id-registry.json").is_file()
    assert (candidate / "candidate-manifest.json").is_file()
    assert (candidate / "status.json").read_text() == '{"status":"AWAITING_APPROVAL"}\n'
    assert (tmp_path / "reports/project-id-registry/2026-08-25/validation-report.json").is_file()


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

from __future__ import annotations

from pathlib import Path

import pytest

from hrl_project_registry.registry import (
    load_registry,
    validate_change,
    validate_file,
    validate_registry,
)

HEADER = "project_id,status,project_name,assigned_organization_code,assigned_at,superseded_by_project_id\n"


def write_registry(path: Path, body: str) -> Path:
    path.write_text(HEADER + body, encoding="utf-8")
    return path


def rows(body: str, tmp_path: Path) -> list[dict[str, str]]:
    return load_registry(write_registry(tmp_path / "reg.csv", body))


# --- standalone file validation ---------------------------------------------


def test_a_clean_registry_passes(tmp_path: Path):
    result = validate_registry(
        rows(
            "HRL-001,eligible,Example One,DWR,2026-08-24,\n"
            "HRL-002,superseded,Example Two,EBMUD,2026-08-24,HRL-001\n",
            tmp_path,
        )
    )
    assert result.ok
    assert not result.warnings


def test_empty_registry_is_a_warning_not_an_error(tmp_path: Path):
    result = validate_registry(rows("", tmp_path))
    assert result.ok
    assert result.warnings


def test_wrong_columns_are_rejected(tmp_path: Path):
    path = tmp_path / "reg.csv"
    path.write_text("project_id,status\nHRL-001,eligible\n", encoding="utf-8")
    result = validate_file(path)
    assert not result.ok
    assert "columns" in result.errors[0]


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        ("HRL-001,eligible,A,DWR,2026-08-24,\nHRL-001,eligible,B,DWR,2026-08-24,\n", "duplicate"),
        ("001,eligible,A,DWR,2026-08-24,\n", "HRL-003"),
        ("HRL-001,pending,A,DWR,2026-08-24,\n", "invalid status"),
        ("HRL-001,eligible,,DWR,2026-08-24,\n", "project_name is required"),
        ("HRL-001,eligible,A,dwr,2026-08-24,\n", "uppercase abbreviation"),
        ("HRL-001,eligible,A,DWR,08/24/2026,\n", "ISO date"),
        ("HRL-001,superseded,A,DWR,2026-08-24,\n", "must appear together"),
        ("HRL-001,eligible,A,DWR,2026-08-24,HRL-001\n", "cannot supersede itself"),
        ("HRL-001,superseded,A,DWR,2026-08-24,HRL-002\n", "does not exist"),
        (
            "HRL-001,superseded,A,DWR,2026-08-24,HRL-002\nHRL-002,retired,B,DWR,2026-08-24,\n",
            "must be eligible",
        ),
    ],
)
def test_row_level_errors(tmp_path: Path, body: str, needle: str):
    result = validate_registry(rows(body, tmp_path))
    assert not result.ok
    assert needle in " ".join(result.errors)


# --- change validation against the prior approved file ----------------------


def test_change_allows_a_new_id():
    old = [{"project_id": "HRL-001", "status": "eligible", "project_name": "A",
            "assigned_organization_code": "DWR", "assigned_at": "2026-08-24",
            "superseded_by_project_id": ""}]
    new = old + [{"project_id": "HRL-002", "status": "eligible", "project_name": "B",
                  "assigned_organization_code": "DWR", "assigned_at": "2026-09-01",
                  "superseded_by_project_id": ""}]
    assert validate_change(old, new) == []


def _row(pid: str, status: str = "eligible", at: str = "2026-08-24", repl: str = "") -> dict[str, str]:
    return {
        "project_id": pid,
        "status": status,
        "project_name": pid,
        "assigned_organization_code": "DWR",
        "assigned_at": at,
        "superseded_by_project_id": repl,
    }


def test_change_rejects_a_removed_id():
    assert "may not be removed" in " ".join(validate_change([_row("HRL-001")], []))


def test_change_rejects_a_moved_assigned_at():
    errors = validate_change([_row("HRL-001", at="2026-08-24")], [_row("HRL-001", at="2026-08-25")])
    assert "assigned_at is immutable" in " ".join(errors)


def test_change_rejects_reviving_a_retired_id():
    errors = validate_change([_row("HRL-001", status="retired")], [_row("HRL-001", status="eligible")])
    assert "terminal" in " ".join(errors)


def test_change_allows_retiring_an_eligible_id():
    assert validate_change([_row("HRL-001", status="eligible")], [_row("HRL-001", status="retired")]) == []


# --- validate_file wiring --------------------------------------------------


def test_validate_file_combines_standalone_and_change_errors(tmp_path: Path):
    base = write_registry(tmp_path / "base.csv", "HRL-001,eligible,A,DWR,2026-08-24,\n")
    head = write_registry(tmp_path / "head.csv", "HRL-002,eligible,B,DWR,2026-08-24,\n")
    result = validate_file(head, base)
    assert not result.ok
    assert "may not be removed" in " ".join(result.errors)


def test_the_committed_registry_is_valid():
    repo_csv = Path(__file__).resolve().parents[1] / "project-id-registry.csv"
    result = validate_file(repo_csv)
    assert result.ok, result.errors

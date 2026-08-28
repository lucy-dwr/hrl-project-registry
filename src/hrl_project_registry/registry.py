from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import REGISTRY_CONTRACT_VERSION

__all__ = [
    "REGISTRY_CONTRACT_VERSION",
    "PUBLIC_COLUMNS",
    "VALID_STATUSES",
    "ValidationResult",
    "load_registry",
    "validate_registry",
    "validate_change",
    "validate_file",
]

PUBLIC_COLUMNS = (
    "project_id",
    "status",
    "project_name",
    "assigned_organization_code",
    "assigned_at",
    "superseded_by_project_id",
)
VALID_STATUSES = frozenset({"eligible", "retired", "superseded"})
PROJECT_ID_PATTERN = re.compile(r"^HRL-\d{3,}$")
ORG_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{1,15}$")


@dataclass(frozen=True)
class ValidationResult:
    rows: list[dict[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _is_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def load_registry(path: Path) -> list[dict[str, str]]:
    """Read `project-id-registry.csv`, enforcing the exact column contract."""
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames != list(PUBLIC_COLUMNS):
            raise ValueError(
                f"{path.name} must use exactly these columns: {', '.join(PUBLIC_COLUMNS)}"
            )
        return [{column: (row[column] or "").strip() for column in PUBLIC_COLUMNS} for row in reader]


def validate_registry(rows: list[dict[str, str]]) -> ValidationResult:
    """Validate the registry as a standalone file (no history required)."""
    errors: list[str] = []
    warnings: list[str] = []
    seen: dict[str, dict[str, str]] = {}

    for number, row in enumerate(rows, start=2):
        project_id = row["project_id"]
        if not project_id:
            errors.append(f"row {number}: project_id is required.")
            continue
        if not PROJECT_ID_PATTERN.fullmatch(project_id):
            errors.append(f"row {number}: project_id {project_id!r} must look like 'HRL-003'.")
        if project_id in seen:
            errors.append(f"row {number}: duplicate project_id {project_id!r}.")
        seen[project_id] = row

        if row["status"] not in VALID_STATUSES:
            errors.append(f"row {number}: invalid status {row['status']!r}.")
        if not row["project_name"]:
            errors.append(f"row {number}: project_name is required.")
        if not ORG_CODE_PATTERN.fullmatch(row["assigned_organization_code"]):
            errors.append(
                f"row {number}: assigned_organization_code "
                f"{row['assigned_organization_code']!r} must be an uppercase abbreviation."
            )
        if not _is_iso_date(row["assigned_at"]):
            errors.append(f"row {number}: assigned_at must be an ISO date (YYYY-MM-DD).")

        replacement = row["superseded_by_project_id"]
        if (row["status"] == "superseded") != bool(replacement):
            errors.append(
                f"row {number}: 'superseded' status and a replacement ID must appear together."
            )
        if replacement and replacement == project_id:
            errors.append(f"row {number}: a project cannot supersede itself.")

    for project_id, row in seen.items():
        replacement = row["superseded_by_project_id"]
        if replacement and replacement not in seen:
            errors.append(f"{project_id}: replacement project {replacement!r} does not exist.")
        elif replacement and seen[replacement]["status"] != "eligible":
            errors.append(f"{project_id}: replacement project {replacement!r} must be eligible.")

    if not rows:
        warnings.append("The registry contains no project IDs.")

    return ValidationResult(
        rows=sorted(rows, key=lambda row: row["project_id"]),
        errors=errors,
        warnings=warnings,
    )


def validate_change(old_rows: list[dict[str, str]], new_rows: list[dict[str, str]]) -> list[str]:
    """Enforce the guarantees that only make sense against the prior approved file.

    IDs are never removed; `assigned_at` is immutable; a retired or superseded ID
    is terminal and cannot revert or change; an eligible ID may not name a
    replacement. New IDs are allowed and checked by `validate_registry`.
    """
    errors: list[str] = []
    old = {row["project_id"]: row for row in old_rows if row["project_id"]}
    new = {row["project_id"]: row for row in new_rows if row["project_id"]}

    for project_id, previous in old.items():
        current = new.get(project_id)
        if current is None:
            errors.append(f"{project_id}: IDs may not be removed from the registry.")
            continue
        if current["assigned_at"] != previous["assigned_at"]:
            errors.append(f"{project_id}: assigned_at is immutable.")
        if previous["status"] != "eligible" and current["status"] != previous["status"]:
            errors.append(
                f"{project_id}: a {previous['status']} ID is terminal and cannot change status."
            )
        if current["status"] == "eligible" and current["superseded_by_project_id"]:
            errors.append(f"{project_id}: an eligible ID cannot name a replacement.")

    return errors


def validate_file(path: Path, base_path: Path | None = None) -> ValidationResult:
    """Validate `path`, and if `base_path` is given, also the change from it."""
    try:
        rows = load_registry(path)
    except (OSError, ValueError) as exc:
        return ValidationResult(errors=[str(exc)])

    result = validate_registry(rows)
    errors = list(result.errors)

    if base_path is not None:
        try:
            base_rows = load_registry(base_path)
        except (OSError, ValueError) as exc:
            errors.append(f"could not read the base registry for comparison: {exc}")
        else:
            errors.extend(validate_change(base_rows, rows))

    return ValidationResult(rows=result.rows, errors=errors, warnings=result.warnings)

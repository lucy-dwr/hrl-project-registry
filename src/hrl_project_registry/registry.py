from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from . import REGISTRY_CONTRACT_VERSION

PUBLIC_COLUMNS = (
    "project_id",
    "status",
    "project_name",
    "assigned_organization_code",
    "assigned_at",
    "superseded_by_project_id",
)
AUDIT_COLUMNS = ("source_revision", "actor", "action", "project_id", "reason")
VALID_STATUSES = frozenset({"eligible", "retired", "superseded"})
VALID_AUDIT_ACTIONS = frozenset({"allocated", "retired", "superseded"})
SOURCE_REVISION_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}(?:-r[2-9][0-9]*)?$")
SOURCE_FILENAMES = frozenset({"project-id-registry.csv", "registry-audit.csv", "_READY"})


@dataclass(frozen=True)
class ValidationResult:
    rows: list[dict[str, str]]
    errors: list[str]
    warnings: list[str]


def _read_csv(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames != list(columns):
            raise ValueError(f"{path.name} must use exactly these columns: {', '.join(columns)}")
        return [{column: (row[column] or "").strip() for column in columns} for row in reader]


def _is_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def validate_source_revision(source_directory: Path, previous_directory: Path | None = None) -> ValidationResult:
    """Validate a complete source revision uploaded after all files are present."""
    rows = _read_csv(source_directory / "project-id-registry.csv", PUBLIC_COLUMNS)
    audit_rows = _read_csv(source_directory / "registry-audit.csv", AUDIT_COLUMNS)
    revision = source_directory.name
    errors: list[str] = []
    warnings: list[str] = []
    actual_files = {path.name for path in source_directory.iterdir() if path.is_file()}
    unexpected = actual_files - SOURCE_FILENAMES
    if unexpected:
        errors.append(f"Source revision contains unsupported files: {', '.join(sorted(unexpected))}.")
    missing = SOURCE_FILENAMES - actual_files
    if missing:
        errors.append(f"Source revision is missing required files: {', '.join(sorted(missing))}.")
    projects: dict[str, dict[str, str]] = {}
    if not SOURCE_REVISION_PATTERN.fullmatch(revision):
        errors.append("Source revision must be YYYY-MM-DD or YYYY-MM-DD-r2 (or a later revision).")

    for number, row in enumerate(rows, start=2):
        project_id = row["project_id"]
        if not project_id:
            errors.append(f"registry row {number}: project_id is required.")
            continue
        if project_id in projects:
            errors.append(f"registry row {number}: duplicate project_id {project_id!r}.")
        projects[project_id] = row
        if row["status"] not in VALID_STATUSES:
            errors.append(f"registry row {number}: invalid status {row['status']!r}.")
        if not _is_iso_date(row["assigned_at"]):
            errors.append(f"registry row {number}: assigned_at must be an ISO date.")
        replacement = row["superseded_by_project_id"]
        if (row["status"] == "superseded") != bool(replacement):
            errors.append(f"registry row {number}: superseded status and replacement ID must appear together.")
        if replacement == project_id:
            errors.append(f"registry row {number}: a project cannot supersede itself.")

    for project_id, row in projects.items():
        replacement = row["superseded_by_project_id"]
        if replacement and replacement not in projects:
            errors.append(f"{project_id!r}: replacement project {replacement!r} does not exist.")
        elif replacement and projects[replacement]["status"] != "eligible":
            errors.append(f"{project_id!r}: replacement project {replacement!r} must be eligible.")

    revision_events: dict[tuple[str, str], int] = {}
    for number, event in enumerate(audit_rows, start=2):
        if not all(event[column] for column in AUDIT_COLUMNS):
            errors.append(f"audit row {number}: all audit columns are required.")
        if event["action"] not in VALID_AUDIT_ACTIONS:
            errors.append(f"audit row {number}: invalid action {event['action']!r}.")
        if event["source_revision"] == revision:
            revision_events[(event["project_id"], event["action"])] = number

    if previous_directory is None:
        previous: dict[str, dict[str, str]] = {}
    else:
        previous_rows = _read_csv(previous_directory / "project-id-registry.csv", PUBLIC_COLUMNS)
        previous = {row["project_id"]: row for row in previous_rows}

    for project_id, old in previous.items():
        new = projects.get(project_id)
        if new is None:
            errors.append(f"{project_id!r}: IDs may not disappear from a later source revision.")
            continue
        if new["assigned_at"] != old["assigned_at"]:
            errors.append(f"{project_id!r}: assigned_at is immutable.")
        if old["status"] != "eligible" and new["status"] != old["status"]:
            errors.append(f"{project_id!r}: retired and superseded IDs cannot change status.")
        if old["status"] == "eligible" and new["status"] == "retired":
            if (project_id, "retired") not in revision_events:
                errors.append(f"{project_id!r}: retirement needs a current-revision audit event.")
        if old["status"] == "eligible" and new["status"] == "superseded":
            if (project_id, "superseded") not in revision_events:
                errors.append(f"{project_id!r}: supersession needs a current-revision audit event.")
        if old["status"] == "eligible" and new["status"] == "eligible":
            if new["superseded_by_project_id"]:
                errors.append(f"{project_id!r}: eligible IDs cannot name a replacement ID.")

    for project_id in set(projects) - set(previous):
        if (project_id, "allocated") not in revision_events:
            errors.append(f"{project_id!r}: new IDs need a current-revision allocated audit event.")

    if not rows:
        warnings.append("The registry contains no project IDs.")
    return ValidationResult(rows=sorted(rows, key=lambda row: row["project_id"]), errors=errors, warnings=warnings)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path.name} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return payload


def _write_xlsx(path: Path, rows: list[dict[str, str]], generated_on: date) -> None:
    def cells(values: list[str], row: int) -> str:
        return "".join(
            f'<c r="{chr(65 + column)}{row}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
            for column, value in enumerate(values)
        )

    sheet_rows = [f'<row r="1">{cells(list(PUBLIC_COLUMNS), 1)}</row>']
    sheet_rows.extend(
        f'<row r="{number}">{cells([row[column] for column in PUBLIC_COLUMNS], number)}</row>'
        for number, row in enumerate(rows, start=2)
    )
    parts = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Project ID Registry" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(sheet_rows) + "</sheetData></worksheet>",
    }
    stamp = (generated_on.year, generated_on.month, generated_on.day, 0, 0, 0)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in parts.items():
            info = zipfile.ZipInfo(name, stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content.encode(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def process_ready_source(
    *, source_directory: Path, report_root: Path, candidate_root: Path, generated_on: date, previous_directory: Path | None = None
) -> ValidationResult:
    """Create a report and, only for a valid `_READY` revision, an approval candidate."""
    if not (source_directory / "_READY").is_file():
        raise ValueError("The source revision is not ready: _READY is required.")
    version = source_directory.name
    generated = generated_on.isoformat()
    report_directory = report_root / "project-id-registry" / version
    report_status = report_directory / "status.json"
    if report_status.is_file():
        report = _read_json(report_directory / "validation-report.json")
        return ValidationResult(
            rows=[],
            errors=[str(error) for error in report.get("errors", [])],
            warnings=[str(warning) for warning in report.get("warnings", [])],
        )
    try:
        result = validate_source_revision(source_directory, previous_directory)
    except (OSError, ValueError) as exc:
        result = ValidationResult(rows=[], errors=[str(exc)], warnings=[])
    report = {
        "source_registry": "hrl-project-registry",
        "registry_contract_version": REGISTRY_CONTRACT_VERSION,
        "source_revision": version,
        "generated_on": generated,
        "row_count": len(result.rows),
        "status": "FAILED" if result.errors else "AWAITING_APPROVAL",
        "errors": result.errors,
        "warnings": result.warnings,
    }
    report_directory.mkdir(parents=True, exist_ok=True)
    (report_directory / "validation-report.json").write_text(json.dumps(report, indent=2) + "\n")
    if result.errors:
        report_status.write_text('{"status":"NEEDS_CORRECTION"}\n')
        return result
    destination = candidate_root / "project-id-registry" / version
    destination.parent.mkdir(parents=True, exist_ok=True)
    if (destination / "status.json").is_file():
        return result
    if destination.exists():
        raise FileExistsError(f"Candidate already exists: {destination}")
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".candidate-") as temporary:
        staging = Path(temporary)
        json_path = staging / "project-id-registry.json"
        csv_path = staging / "project-id-registry.csv"
        xlsx_path = staging / "project-id-registry.xlsx"
        json_path.write_text(json.dumps(result.rows, indent=2) + "\n")
        with csv_path.open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=PUBLIC_COLUMNS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(result.rows)
        _write_xlsx(xlsx_path, result.rows, generated_on)
        checksums = {path.name: _sha256(path) for path in (json_path, csv_path, xlsx_path)}
        manifest = {
            "source_registry": "hrl-project-registry",
            "registry_contract_version": REGISTRY_CONTRACT_VERSION,
            "source_revision": version,
            "export_version": version,
            "generated_on": generated,
            "row_count": len(result.rows),
            "checksums": checksums,
        }
        (staging / "candidate-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        (staging / "status.json").write_text(
            json.dumps({"source_revision": version, "status": "AWAITING_APPROVAL"}, sort_keys=True) + "\n"
        )
        (staging / "SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in sorted(checksums.items())))
        if any(_sha256(staging / name) != checksum for name, checksum in checksums.items()):
            raise RuntimeError("Candidate checksum verification failed.")
        shutil.move(str(staging), str(destination))
    return result


def promote_candidate(*, candidate_directory: Path, export_root: Path) -> Path:
    """Promote one reviewed candidate into an immutable registry export."""
    status = _read_json(candidate_directory / "status.json")
    if status.get("status") != "AWAITING_APPROVAL":
        raise ValueError("candidate status must be AWAITING_APPROVAL")
    manifest_path = candidate_directory / "candidate-manifest.json"
    candidate_manifest = _read_json(manifest_path)
    approval = _read_json(candidate_directory / "_APPROVE")
    required = {"export_version", "approved_by", "approved_on", "candidate_manifest_sha256"}
    if not required <= set(approval) or not all(isinstance(approval[key], str) and approval[key] for key in required):
        raise ValueError("_APPROVE is missing required approval fields")
    if not _is_iso_date(str(approval["approved_on"])):
        raise ValueError("_APPROVE approved_on must be an ISO date")
    if approval["export_version"] != candidate_manifest.get("export_version"):
        raise ValueError("_APPROVE export_version does not match candidate manifest")
    if approval["candidate_manifest_sha256"] != _sha256(manifest_path):
        raise ValueError("_APPROVE candidate manifest checksum does not match")
    checksums = candidate_manifest.get("checksums")
    if not isinstance(checksums, dict) or set(checksums) != {
        "project-id-registry.csv", "project-id-registry.json", "project-id-registry.xlsx"
    }:
        raise ValueError("candidate manifest has an invalid artifact checksum set")
    for name, checksum in checksums.items():
        if not isinstance(checksum, str) or _sha256(candidate_directory / name) != checksum:
            raise ValueError(f"candidate artifact checksum does not match: {name}")
    version = str(candidate_manifest.get("export_version", ""))
    if not SOURCE_REVISION_PATTERN.fullmatch(version):
        raise ValueError("candidate manifest has an invalid export_version")
    if candidate_manifest.get("source_revision") != version:
        raise ValueError("candidate manifest source_revision and export_version must match")
    if status.get("source_revision") != version:
        raise ValueError("candidate status source_revision does not match candidate manifest")
    destination = export_root / "project-id-registry" / version
    if destination.exists():
        manifest = _read_json(destination / "manifest.json")
        if manifest.get("checksums") != checksums:
            raise ValueError("existing immutable export does not match candidate")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".export-") as temporary:
        staging = Path(temporary)
        for name in sorted(checksums):
            shutil.copy2(candidate_directory / name, staging / name)
        manifest = {
            **candidate_manifest,
            "approved_by": approval["approved_by"],
            "approved_on": approval["approved_on"],
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        (staging / "SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in sorted(checksums.items())))
        shutil.move(str(staging), str(destination))
    pointer = {"export_version": version, "manifest": f"{version}/manifest.json"}
    pointer_path = destination.parent / "current.json"
    temporary_pointer = destination.parent / ".current.json.tmp"
    temporary_pointer.write_text(json.dumps(pointer, indent=2, sort_keys=True) + "\n")
    temporary_pointer.replace(pointer_path)
    return destination

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import typer
from .registry import process_ready_source

app = typer.Typer(no_args_is_help=True)


@app.command("process-ready-source")
def process_ready_source_command(
    source_directory: Path = typer.Option(..., help="Downloaded source-revision directory containing _READY."),
    report_root: Path = typer.Option(...),
    candidate_root: Path = typer.Option(...),
    previous_directory: Path | None = typer.Option(None),
    generated_at: datetime | None = typer.Option(None, help="Optional UTC provenance timestamp."),
) -> None:
    """Validate an uploaded `_READY` revision and create an approval candidate when valid."""
    result = process_ready_source(
        source_directory=source_directory,
        report_root=report_root,
        candidate_root=candidate_root,
        previous_directory=previous_directory,
        generated_at=generated_at or datetime.now(timezone.utc),
    )
    if result.errors:
        typer.echo("Registry source validation failed.", err=True)
        raise typer.Exit(code=1)
    typer.echo("Registry candidate is awaiting approval.")

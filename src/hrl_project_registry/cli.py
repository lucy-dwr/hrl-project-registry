from __future__ import annotations

from datetime import date
from pathlib import Path

import typer
from .registry import process_ready_source, promote_candidate

app = typer.Typer(no_args_is_help=True)


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter("must be an ISO date (YYYY-MM-DD)") from exc


@app.command("process-ready-source")
def process_ready_source_command(
    source_directory: Path = typer.Option(..., help="Downloaded source-revision directory containing _READY."),
    report_root: Path = typer.Option(...),
    candidate_root: Path = typer.Option(...),
    previous_directory: Path | None = typer.Option(None),
    generated_on: str | None = typer.Option(None, help="Optional provenance date (YYYY-MM-DD)."),
) -> None:
    """Validate an uploaded `_READY` revision and create an approval candidate when valid."""
    result = process_ready_source(
        source_directory=source_directory,
        report_root=report_root,
        candidate_root=candidate_root,
        previous_directory=previous_directory,
        generated_on=_parse_date(generated_on) or date.today(),
    )
    if result.errors:
        typer.echo("Registry source validation failed.", err=True)
        raise typer.Exit(code=1)
    typer.echo("Registry candidate is awaiting approval.")


@app.command("promote")
def promote_command(
    candidate_directory: Path = typer.Option(..., help="Reviewed candidate directory containing _APPROVE."),
    export_root: Path = typer.Option(...),
) -> None:
    """Promote a reviewed registry candidate into its immutable export directory."""
    destination = promote_candidate(candidate_directory=candidate_directory, export_root=export_root)
    typer.echo(f"Published immutable registry export: {destination}")


@app.command("consume-validation-queue")
def consume_validation_queue_command(
    account_url: str = typer.Option(..., envvar="HRL_STORAGE_ACCOUNT_URL"),
    queue: str = typer.Option(...),
    source_container: str = typer.Option(...),
    report_container: str = typer.Option(...),
    candidate_container: str = typer.Option(...),
    export_container: str = typer.Option(...),
    source_prefix: str = typer.Option("project-id-registry/source-revisions"),
    generated_on: str | None = typer.Option(None, help="Optional provenance date (YYYY-MM-DD)."),
) -> None:
    """Receive, process, and acknowledge one registry source-revision message."""
    from .azure_worker import RegistryValidationWorker

    worker = RegistryValidationWorker(account_url, queue, source_container, report_container, candidate_container, export_container, source_prefix)
    if not worker.process_one(_parse_date(generated_on)):
        typer.echo("No queue message available.")


@app.command("consume-promotion-queue")
def consume_promotion_queue_command(
    account_url: str = typer.Option(..., envvar="HRL_STORAGE_ACCOUNT_URL"),
    queue: str = typer.Option(...),
    candidate_container: str = typer.Option(...),
    export_container: str = typer.Option(...),
    candidate_prefix: str = typer.Option("project-id-registry"),
) -> None:
    """Receive, promote, and acknowledge one approved registry candidate message."""
    from .azure_worker import RegistryPromotionWorker

    worker = RegistryPromotionWorker(
        account_url,
        queue,
        candidate_container,
        export_container,
        candidate_prefix,
    )
    if not worker.process_one():
        typer.echo("No queue message available.")

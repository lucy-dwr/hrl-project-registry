"""Managed-identity adapter for one registry-validation Storage Queue message."""
from __future__ import annotations

import json
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import unquote, urlparse

from azure.core.exceptions import ResourceExistsError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from azure.storage.queue import QueueClient

from .registry import process_ready_source, promote_candidate, source_revision_key


class RegistryValidationWorker:
    def __init__(
        self,
        account_url: str,
        queue_name: str,
        source_container: str,
        report_container: str,
        candidate_container: str,
        export_container: str,
        source_prefix: str,
    ):
        credential = DefaultAzureCredential()
        self.blobs = BlobServiceClient(account_url, credential=credential)
        self.queue = QueueClient(account_url.replace(".blob.", ".queue.").rstrip("/") + f"/{queue_name}", credential=credential)
        self.source_container = source_container
        self.report_container = report_container
        self.candidate_container = candidate_container
        self.export_container = export_container
        self.source_prefix = source_prefix.strip("/")

    def _download_directory(self, container: str, prefix: str, destination: Path) -> None:
        client = self.blobs.get_container_client(container)
        for blob in client.list_blobs(name_starts_with=f"{prefix}/"):
            relative = blob.name.removeprefix(f"{prefix}/")
            if not relative or "/" in relative:
                raise ValueError("registry source revisions may contain direct files only")
            (destination / relative).write_bytes(client.get_blob_client(blob.name).download_blob().readall())

    def _upload_directory(self, container: str, prefix: str, directory: Path) -> None:
        client = self.blobs.get_container_client(container)
        paths = sorted(directory.iterdir(), key=lambda path: (path.name == "status.json", path.name))
        for path in paths:
            if path.is_file():
                try:
                    client.get_blob_client(f"{prefix}/{path.name}").upload_blob(path.read_bytes(), overwrite=False)
                except ResourceExistsError:
                    pass

    def _event_version(self, message: str) -> str:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError as exc:
            raise ValueError("queue message is not JSON") from exc
        if isinstance(payload, list):
            if len(payload) != 1:
                raise ValueError("queue message must contain exactly one Event Grid event")
            payload = payload[0]
        if not isinstance(payload, dict) or (payload.get("eventType") or payload.get("type")) != "Microsoft.Storage.BlobCreated":
            raise ValueError("queue message is not one BlobCreated event")
        subject, data = payload.get("subject"), payload.get("data")
        if not isinstance(subject, str) or not isinstance(data, dict) or not isinstance(data.get("url"), str):
            raise ValueError("event lacks subject or data.url")
        marker = f"/blobServices/default/containers/{self.source_container}/blobs/{self.source_prefix}/"
        if not subject.startswith(marker) or not subject.endswith("/_READY"):
            raise ValueError("event is outside the registry source _READY protocol")
        version = unquote(subject[len(marker):-len("/_READY")])
        url_path = unquote(urlparse(data["url"]).path).lstrip("/")
        if url_path != f"{self.source_container}/{self.source_prefix}/{version}/_READY":
            raise ValueError("event subject and data.url disagree")
        try:
            source_revision_key(version)
        except ValueError as exc:
            raise ValueError("event has an invalid source revision") from exc
        return version

    def process_one(self, generated_on: date | None = None) -> bool:
        message = next(iter(self.queue.receive_messages(messages_per_page=1, visibility_timeout=900)), None)
        if message is None:
            return False
        version = self._event_version(message.content)
        report_prefix = f"project-id-registry/{version}"
        candidate_prefix = f"project-id-registry/{version}"
        if self.blobs.get_blob_client(self.report_container, f"{report_prefix}/status.json").exists() or self.blobs.get_blob_client(self.candidate_container, f"{candidate_prefix}/status.json").exists():
            self.queue.delete_message(message.id, message.pop_receipt)
            return True
        with tempfile.TemporaryDirectory(prefix="hrl-registry-") as temporary:
            root = Path(temporary)
            source = root / version
            source.mkdir()
            self._download_directory(self.source_container, f"{self.source_prefix}/{version}", source)
            if not (source / "_READY").is_file():
                raise ValueError("_READY marker is no longer present")
            previous = None
            pointer = self.blobs.get_blob_client(self.export_container, "project-id-registry/current.json")
            if pointer.exists():
                current = json.loads(pointer.download_blob().readall())
                manifest_client = self.blobs.get_blob_client(
                    self.export_container, current["manifest"]
                )
                manifest = json.loads(manifest_client.download_blob().readall())
                previous = root / str(manifest["source_revision"])
                previous.mkdir()
                self._download_directory(
                    self.source_container,
                    f"{self.source_prefix}/{manifest['source_revision']}",
                    previous,
                )
            reports, candidates = root / "reports", root / "candidates"
            result = process_ready_source(
                source_directory=source,
                previous_directory=previous,
                report_root=reports,
                candidate_root=candidates,
                generated_on=generated_on or date.today(),
            )
            self._upload_directory(self.report_container, report_prefix, reports / "project-id-registry" / version)
            if not result.errors:
                self._upload_directory(self.candidate_container, candidate_prefix, candidates / "project-id-registry" / version)
        self.queue.delete_message(message.id, message.pop_receipt)
        return True


class RegistryPromotionWorker:
    """Managed-identity adapter for one registry-promotion Storage Queue message."""

    def __init__(
        self,
        account_url: str,
        queue_name: str,
        candidate_container: str,
        export_container: str,
        candidate_prefix: str = "project-id-registry",
    ):
        credential = DefaultAzureCredential()
        self.blobs = BlobServiceClient(account_url, credential=credential)
        self.queue = QueueClient(
            account_url.replace(".blob.", ".queue.").rstrip("/") + f"/{queue_name}",
            credential=credential,
        )
        self.candidate_container = candidate_container
        self.export_container = export_container
        self.candidate_prefix = candidate_prefix.strip("/")

    def _download_directory(self, container: str, prefix: str, destination: Path) -> None:
        client = self.blobs.get_container_client(container)
        for blob in client.list_blobs(name_starts_with=f"{prefix}/"):
            relative = blob.name.removeprefix(f"{prefix}/")
            if not relative or "/" in relative:
                raise ValueError("registry candidates may contain direct files only")
            (destination / relative).write_bytes(client.get_blob_client(blob.name).download_blob().readall())

    def _upload_directory(self, container: str, prefix: str, directory: Path) -> None:
        client = self.blobs.get_container_client(container)
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            if path.is_file():
                try:
                    client.get_blob_client(f"{prefix}/{path.name}").upload_blob(path.read_bytes(), overwrite=False)
                except ResourceExistsError:
                    pass

    def _event_version(self, message: str) -> str:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError as exc:
            raise ValueError("queue message is not JSON") from exc
        if isinstance(payload, list):
            if len(payload) != 1:
                raise ValueError("queue message must contain exactly one Event Grid event")
            payload = payload[0]
        if not isinstance(payload, dict) or (payload.get("eventType") or payload.get("type")) != "Microsoft.Storage.BlobCreated":
            raise ValueError("queue message is not one BlobCreated event")
        subject, data = payload.get("subject"), payload.get("data")
        if not isinstance(subject, str) or not isinstance(data, dict) or not isinstance(data.get("url"), str):
            raise ValueError("event lacks subject or data.url")
        marker = f"/blobServices/default/containers/{self.candidate_container}/blobs/{self.candidate_prefix}/"
        if not subject.startswith(marker) or not subject.endswith("/_APPROVE"):
            raise ValueError("event is outside the registry candidate _APPROVE protocol")
        version = unquote(subject[len(marker):-len("/_APPROVE")])
        url_path = unquote(urlparse(data["url"]).path).lstrip("/")
        if url_path != f"{self.candidate_container}/{self.candidate_prefix}/{version}/_APPROVE":
            raise ValueError("event subject and data.url disagree")
        try:
            source_revision_key(version)
        except ValueError as exc:
            raise ValueError("event has an invalid export version") from exc
        return version

    def _upload_current_pointer(self, pointer: Path, version: str) -> None:
        client = self.blobs.get_blob_client(self.export_container, f"{self.candidate_prefix}/current.json")
        payload = pointer.read_bytes()
        if client.exists():
            current = json.loads(client.download_blob().readall())
            current_version = current.get("export_version")
            if not isinstance(current_version, str):
                raise ValueError("current.json has no valid export_version")
            if source_revision_key(current_version) > source_revision_key(version):
                raise ValueError("refusing to replace current.json with an older export")
            if current_version != version:
                client.upload_blob(payload, overwrite=True, if_match=client.get_blob_properties().etag)
        else:
            client.upload_blob(payload, overwrite=False, if_none_match="*")

    def process_one(self) -> bool:
        message = next(iter(self.queue.receive_messages(messages_per_page=1, visibility_timeout=900)), None)
        if message is None:
            return False
        version = self._event_version(message.content)
        export_prefix = f"{self.candidate_prefix}/{version}"
        destination_manifest = self.blobs.get_blob_client(self.export_container, f"{export_prefix}/manifest.json")
        current_pointer = self.blobs.get_blob_client(self.export_container, f"{self.candidate_prefix}/current.json")
        if destination_manifest.exists() and current_pointer.exists():
            self.queue.delete_message(message.id, message.pop_receipt)
            return True
        with tempfile.TemporaryDirectory(prefix="hrl-registry-promotion-") as temporary:
            root = Path(temporary)
            candidate = root / version
            candidate.mkdir()
            self._download_directory(self.candidate_container, export_prefix, candidate)
            if not (candidate / "_APPROVE").is_file():
                raise ValueError("_APPROVE marker is no longer present")
            exports = root / "exports"
            destination = promote_candidate(candidate_directory=candidate, export_root=exports)
            self._upload_directory(self.export_container, export_prefix, destination)
            self._upload_current_pointer(exports / self.candidate_prefix / "current.json", version)
        self.queue.delete_message(message.id, message.pop_receipt)
        return True

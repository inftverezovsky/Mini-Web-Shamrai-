from __future__ import annotations

import asyncio
import base64
import json
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.models.database import AsyncSessionLocal
from src.services.stats_export import (
    build_stats_export_workbook,
    load_client_export_groups,
    load_clients_export_items,
    load_shamrai_export_items,
    stats_export_period_label,
)

DriveExportScope = Literal["shamrai", "clients", "all"]
DriveExportFormat = Literal["xlsx", "google_sheet"]
DRIVE_EXPORT_ROOT_FOLDER_NAME = "Shamrai Stats Exports"


@dataclass
class DriveExportJob:
    id: str
    status: str = "pending"
    scope: str = "all"
    period: str = "all"
    formats: list[str] = field(default_factory=list)
    links: list[dict[str, str]] = field(default_factory=list)
    error: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "scope": self.scope,
            "period": self.period,
            "formats": self.formats,
            "links": self.links,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


_jobs: dict[str, DriveExportJob] = {}


def _touch(job: DriveExportJob, status_value: Optional[str] = None) -> None:
    if status_value:
        job.status = status_value
    job.updated_at = datetime.utcnow().isoformat() + "Z"


def get_drive_export_job(job_id: str) -> Optional[dict[str, Any]]:
    job = _jobs.get(job_id)
    return job.as_dict() if job else None


def _drive_enabled_or_400() -> None:
    if not settings.GOOGLE_DRIVE_STATS_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Выгрузка в Google Drive отключена на сервере",
        )
    if not settings.GOOGLE_DRIVE_STATS_FOLDER_ID.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Не настроена папка Google Drive для статистики",
        )
    if not settings.GOOGLE_SERVICE_ACCOUNT_JSON_B64.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Не настроен service account Google Drive",
        )


def start_drive_export_job(
    *,
    scope: DriveExportScope,
    period: str,
    formats: list[DriveExportFormat],
) -> dict[str, Any]:
    _drive_enabled_or_400()
    job_id = uuid.uuid4().hex
    job = DriveExportJob(id=job_id, scope=scope, period=period, formats=list(formats))
    _jobs[job_id] = job
    asyncio.create_task(_run_drive_export_job(job))
    return job.as_dict()


async def _run_drive_export_job(job: DriveExportJob) -> None:
    _touch(job, "running")
    try:
        async with AsyncSessionLocal() as db:
            artifacts = await _build_artifacts(db, scope=job.scope, period=job.period)
        links = await asyncio.to_thread(_upload_artifacts, artifacts, job.formats)
        job.links = links
        _touch(job, "completed")
    except Exception as exc:
        job.error = str(exc)
        _touch(job, "failed")


async def _build_artifacts(db: AsyncSession, *, scope: str, period: str) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    period_label = stats_export_period_label(period)

    if scope in {"shamrai", "all"}:
        shamrai_items = await load_shamrai_export_items(db, period)
        artifacts.append({
            "folder": "Шамрай",
            "title": f"Шамрай - статистика - {period_label}",
            "xlsx": build_stats_export_workbook(
                shamrai_items,
                title="СТАТИСТИКА SHAMRAI",
                period_label=period_label,
                include_client=False,
            ),
        })

    if scope in {"clients", "all"}:
        client_items = await load_clients_export_items(db, period)
        artifacts.append({
            "folder": "Клиенты",
            "title": f"Клиенты - свод - {period_label}",
            "xlsx": build_stats_export_workbook(
                client_items,
                title="СТАТИСТИКА КЛИЕНТОВ SHAMRAI",
                period_label=period_label,
                include_client=True,
            ),
        })
        for group in await load_client_export_groups(db, period):
            safe_name = _safe_filename(group.client_name)
            artifacts.append({
                "folder": "Клиенты",
                "title": f"{safe_name} - статистика - {period_label}",
                "xlsx": build_stats_export_workbook(
                    group.items,
                    title=f"СТАТИСТИКА КЛИЕНТА: {group.client_name}",
                    period_label=period_label,
                    include_client=True,
                ),
            })

    return artifacts


def _safe_filename(value: str) -> str:
    clean = "".join(ch for ch in value if ch not in r'<>:"/\|?*').strip()
    return clean[:80] or "Клиент"


def _credentials_info() -> dict[str, Any]:
    raw = base64.b64decode(settings.GOOGLE_SERVICE_ACCOUNT_JSON_B64.encode("utf-8")).decode("utf-8")
    return json.loads(raw)


def _build_drive_service():
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError("Не установлены google-api-python-client/google-auth для Google Drive выгрузки") from exc

    credentials = service_account.Credentials.from_service_account_info(
        _credentials_info(),
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _create_folder(service, *, name: str, parent_id: str) -> dict[str, Any]:
    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    return service.files().create(body=metadata, fields="id,name,webViewLink", supportsAllDrives=True).execute()


def _escape_drive_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _get_or_create_folder(service, *, name: str, parent_id: str) -> dict[str, Any]:
    escaped_name = _escape_drive_query_value(name)
    escaped_parent = _escape_drive_query_value(parent_id)
    query = (
        "mimeType='application/vnd.google-apps.folder' "
        f"and name='{escaped_name}' "
        f"and '{escaped_parent}' in parents "
        "and trashed=false"
    )
    result = service.files().list(
        q=query,
        fields="files(id,name,webViewLink)",
        pageSize=1,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = result.get("files") or []
    if files:
        return files[0]
    return _create_folder(service, name=name, parent_id=parent_id)


def _upload_file(service, *, path: Path, title: str, parent_id: str, as_google_sheet: bool) -> dict[str, str]:
    from googleapiclient.http import MediaFileUpload

    metadata = {"name": title, "parents": [parent_id]}
    if as_google_sheet:
        metadata["mimeType"] = "application/vnd.google-apps.spreadsheet"
    media = MediaFileUpload(
        str(path),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        resumable=False,
    )
    created = service.files().create(
        body=metadata,
        media_body=media,
        fields="id,name,webViewLink,mimeType",
        supportsAllDrives=True,
    ).execute()
    return {
        "title": created.get("name", title),
        "url": created.get("webViewLink", ""),
        "id": created.get("id", ""),
        "format": "google_sheet" if as_google_sheet else "xlsx",
    }


def _upload_artifacts(artifacts: list[dict[str, Any]], formats: list[str]) -> list[dict[str, str]]:
    service = _build_drive_service()
    root_id = settings.GOOGLE_DRIVE_STATS_FOLDER_ID.strip()
    exports_root = _get_or_create_folder(
        service,
        name=DRIVE_EXPORT_ROOT_FOLDER_NAME,
        parent_id=root_id,
    )
    run_folder = _create_folder(
        service,
        name=datetime.now().strftime("%Y-%m-%d %H-%M"),
        parent_id=exports_root["id"],
    )
    folder_cache: dict[str, str] = {}
    links: list[dict[str, str]] = [
        {
            "title": exports_root["name"],
            "url": exports_root.get("webViewLink", ""),
            "id": exports_root["id"],
            "format": "folder",
        },
        {
            "title": run_folder["name"],
            "url": run_folder.get("webViewLink", ""),
            "id": run_folder["id"],
            "format": "folder",
        },
    ]

    with tempfile.TemporaryDirectory(prefix="shamrai-stats-") as tmp_dir:
        tmp_path = Path(tmp_dir)
        for artifact in artifacts:
            folder_name = artifact["folder"]
            if folder_name not in folder_cache:
                folder = _create_folder(service, name=folder_name, parent_id=run_folder["id"])
                folder_cache[folder_name] = folder["id"]
                links.append({
                    "title": folder["name"],
                    "url": folder.get("webViewLink", ""),
                    "id": folder["id"],
                    "format": "folder",
                })
            xlsx_path = tmp_path / f"{_safe_filename(artifact['title'])}.xlsx"
            xlsx_path.write_bytes(artifact["xlsx"])
            if "xlsx" in formats:
                links.append(_upload_file(
                    service,
                    path=xlsx_path,
                    title=f"{artifact['title']}.xlsx",
                    parent_id=folder_cache[folder_name],
                    as_google_sheet=False,
                ))
            if "google_sheet" in formats:
                links.append(_upload_file(
                    service,
                    path=xlsx_path,
                    title=artifact["title"],
                    parent_id=folder_cache[folder_name],
                    as_google_sheet=True,
                ))
    return links

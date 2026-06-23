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
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from openpyxl.utils import get_column_letter
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.models.database import AsyncSessionLocal
from src.services.statistics import MONTH_LABELS
from src.services.stats_export import (
    FLAT_FORMAT,
    GoogleSheetIconCell,
    StatsWorkbookArtifact,
    build_client_info_export_workbook_artifact,
    build_stats_export_workbook_artifact,
    filter_client_info_export_rows,
    filter_client_recent_export_rows,
    load_client_info_export_rows,
    load_client_recent_bet_export_rows,
    load_client_export_groups,
    load_clients_export_items,
    load_shamrai_export_items,
    stats_export_period_label,
)

DriveExportScope = Literal["shamrai", "clients", "all", "crm"]
DriveExportFormat = Literal["xlsx", "google_sheet"]
DRIVE_EXPORT_ROOT_FOLDER_NAME = "Shamrai Stats Exports"
CRM_DRIVE_EXPORT_ROOT_FOLDER_NAME = "Shamrai Exports"
CRM_DRIVE_EXPORT_FOLDER_NAME = "CRM"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
GOOGLE_DRIVE_AUTH_MODES = {"auto", "oauth", "service_account"}
DRIVE_EXPORT_TZ = ZoneInfo("Europe/Moscow")
DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"
DRIVE_GOOGLE_SHEET_MIME = "application/vnd.google-apps.spreadsheet"
DRIVE_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass
class DriveExportJob:
    id: str
    status: str = "pending"
    scope: str = "all"
    period: str = "all"
    formats: list[str] = field(default_factory=list)
    filters: dict[str, Any] = field(default_factory=dict)
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
            "filters": self.filters,
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

    auth_mode = _drive_auth_mode()
    if auth_mode == "oauth" and not _has_oauth_credentials():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Не настроен Google OAuth для выгрузки статистики",
        )
    if auth_mode == "service_account" and not settings.GOOGLE_SERVICE_ACCOUNT_JSON_B64.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Не настроен service account Google Drive",
        )
    if auth_mode == "auto" and not (_has_oauth_credentials() or settings.GOOGLE_SERVICE_ACCOUNT_JSON_B64.strip()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Не настроены Google OAuth или service account для выгрузки статистики",
        )


def _format_drive_export_error(exc: Exception) -> str:
    status_code = getattr(getattr(exc, "resp", None), "status", None)
    reason = ""
    message = ""
    content = getattr(exc, "content", None)
    if content:
        try:
            payload = json.loads(content.decode("utf-8") if isinstance(content, bytes) else str(content))
            error = payload.get("error") or {}
            message = str(error.get("message") or "")
            reasons = [
                str(item.get("reason") or "")
                for item in (error.get("errors") or [])
                if isinstance(item, dict)
            ]
            reason = ",".join(item for item in reasons if item)
        except Exception:
            message = ""

    reason_lower = reason.lower()
    message_lower = message.lower()
    if status_code == 403:
        if "quota" in reason_lower or "quota" in message_lower or "storagequotaexceeded" in reason_lower:
            return (
                "Google Drive отказал в создании файла: у текущего Google-аккаунта для выгрузки нет доступной "
                "квоты Drive. Подключите OAuth пользователя с квотой Drive или используйте папку на общем диске."
            )
        if "permission" in reason_lower or "permission" in message_lower or "forbidden" in reason_lower:
            return (
                "Google Drive отказал в доступе к папке выгрузки. Проверьте, что папка статистики расшарена "
                "на выбранный Google-аккаунт с правами редактора."
            )
        return (
            "Google Drive вернул 403 при выгрузке статистики. Проверьте права service account, доступность папки "
            "и квоту Drive."
        )

    if status_code:
        return f"Google Drive вернул ошибку {status_code} при выгрузке статистики."
    return str(exc) or "Не удалось выгрузить статистику в Google Drive"


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


def start_crm_drive_export_job(
    *,
    q: Optional[str],
    activity: str,
    group: Optional[str],
    tag: Optional[str],
    formats: list[DriveExportFormat],
) -> dict[str, Any]:
    _drive_enabled_or_400()
    job_id = uuid.uuid4().hex
    filters = {
        "q": (q or "").strip(),
        "activity": activity,
        "group": (group or "").strip(),
        "tag": (tag or "").strip(),
    }
    job = DriveExportJob(
        id=job_id,
        scope="crm",
        period="all",
        formats=list(formats),
        filters=filters,
    )
    _jobs[job_id] = job
    asyncio.create_task(_run_drive_export_job(job))
    return job.as_dict()


async def _run_drive_export_job(job: DriveExportJob) -> None:
    _touch(job, "running")
    try:
        async with AsyncSessionLocal() as db:
            if job.scope == "crm":
                artifacts = await _build_crm_artifacts(db, filters=job.filters, formats=job.formats)
            else:
                artifacts = await _build_artifacts(db, scope=job.scope, period=job.period, formats=job.formats)
        if job.scope == "crm":
            links = await asyncio.to_thread(
                _upload_artifacts,
                artifacts,
                job.formats,
                root_folder_name=CRM_DRIVE_EXPORT_ROOT_FOLDER_NAME,
                base_folder_segments=[CRM_DRIVE_EXPORT_FOLDER_NAME],
                temp_prefix="shamrai-crm-",
            )
        else:
            links = await asyncio.to_thread(_upload_artifacts, artifacts, job.formats)
        job.links = links
        _touch(job, "completed")
    except Exception as exc:
        job.error = _format_drive_export_error(exc)
        _touch(job, "failed")


def _normalized_formats(formats: Optional[list[str]]) -> set[str]:
    return set(formats or ["google_sheet"])


def _artifact_with_google_sheet_variant(
    *,
    folder: str,
    title: str,
    formats: Optional[list[str]],
    workbook_builder,
) -> dict[str, Any]:
    format_set = _normalized_formats(formats)
    xlsx_artifact: Optional[StatsWorkbookArtifact] = None
    google_sheet_artifact: Optional[StatsWorkbookArtifact] = None
    if "xlsx" in format_set:
        xlsx_artifact = workbook_builder("floating")
    if "google_sheet" in format_set:
        google_sheet_artifact = workbook_builder("google_cell")

    primary_artifact = xlsx_artifact or google_sheet_artifact or workbook_builder("floating")
    artifact: dict[str, Any] = {
        "folder": folder,
        "title": title,
        "xlsx": primary_artifact.xlsx,
        "google_sheet_icon_cells": [],
    }
    if google_sheet_artifact:
        artifact["google_sheet_xlsx"] = google_sheet_artifact.xlsx
        artifact["google_sheet_icon_cells"] = google_sheet_artifact.icon_cells
    return artifact


async def _build_artifacts(
    db: AsyncSession,
    *,
    scope: str,
    period: str,
    formats: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    period_label = stats_export_period_label(period)

    if scope in {"shamrai", "all"}:
        shamrai_items = await load_shamrai_export_items(db, period)
        artifacts.append(_artifact_with_google_sheet_variant(
            folder="Шамрай",
            title=f"Шамрай - статистика - {period_label}",
            formats=formats,
            workbook_builder=lambda logo_mode: build_stats_export_workbook_artifact(
                shamrai_items,
                title="СТАТИСТИКА SHAMRAI",
                period_label=period_label,
                include_client=False,
                logo_mode=logo_mode,
            ),
        ))

    if scope in {"clients", "all"}:
        client_items = await load_clients_export_items(db, period)
        client_info_rows = await load_client_info_export_rows(db, period)
        client_recent_rows = await load_client_recent_bet_export_rows(db, period)
        artifacts.append(_artifact_with_google_sheet_variant(
            folder="Клиенты",
            title=f"Клиенты - свод - {period_label}",
            formats=formats,
            workbook_builder=lambda logo_mode: build_stats_export_workbook_artifact(
                client_items,
                title="СТАТИСТИКА КЛИЕНТОВ SHAMRAI",
                period_label=period_label,
                include_client=True,
                value_format=FLAT_FORMAT,
                value_label="флеты",
                logo_mode=logo_mode,
            ),
        ))
        artifacts.append(_artifact_with_google_sheet_variant(
            folder="Клиенты/Инфа",
            title=f"Клиенты - инфа - {period_label}",
            formats=formats,
            workbook_builder=lambda logo_mode: build_client_info_export_workbook_artifact(
                client_info_rows,
                recent_rows=client_recent_rows,
                period_label=period_label,
                logo_mode=logo_mode,
            ),
        ))
        for group in await load_client_export_groups(db, period):
            safe_name = _safe_filename(group.client_name)
            artifacts.append(_artifact_with_google_sheet_variant(
                folder="Клиенты",
                title=f"{safe_name} - статистика - {period_label}",
                formats=formats,
                workbook_builder=lambda logo_mode, group=group: build_stats_export_workbook_artifact(
                    group.items,
                    title=f"СТАТИСТИКА КЛИЕНТА: {group.client_name}",
                    period_label=period_label,
                    include_client=True,
                    value_format=FLAT_FORMAT,
                    value_label="флеты",
                    logo_mode=logo_mode,
                ),
            ))

    return artifacts


def _crm_filter_label(filters: dict[str, Any]) -> str:
    parts: list[str] = []
    q = str(filters.get("q") or "").strip()
    activity = str(filters.get("activity") or "all").strip()
    group = str(filters.get("group") or "").strip()
    tag = str(filters.get("tag") or "").strip()
    if q:
        parts.append(f"поиск: {q}")
    activity_labels = {
        "active": "активные",
        "empty": "без матчей",
        "guarantee": "гарантия",
    }
    if activity in activity_labels:
        parts.append(activity_labels[activity])
    if group and group != "all":
        parts.append(f"группа: {group}")
    if tag and tag != "all":
        parts.append(f"метка: {tag}")
    return "CRM: клиенты" + (f" ({', '.join(parts)})" if parts else "")


async def _build_crm_artifacts(
    db: AsyncSession,
    *,
    filters: dict[str, Any],
    formats: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    client_info_rows = await load_client_info_export_rows(db, "all")
    filtered_info_rows = filter_client_info_export_rows(
        client_info_rows,
        q=str(filters.get("q") or ""),
        activity=str(filters.get("activity") or "all"),
        group=str(filters.get("group") or ""),
        tag=str(filters.get("tag") or ""),
    )
    filtered_user_ids = {row.user_id for row in filtered_info_rows}
    recent_rows = await load_client_recent_bet_export_rows(db, "all", limit_per_client=None)
    filtered_recent_rows = filter_client_recent_export_rows(recent_rows, filtered_user_ids)
    period_label = _crm_filter_label(filters)
    return [_artifact_with_google_sheet_variant(
        folder="Клиенты",
        title="CRM по клиентам",
        formats=formats,
        workbook_builder=lambda logo_mode: build_client_info_export_workbook_artifact(
            filtered_info_rows,
            recent_rows=filtered_recent_rows,
            period_label=period_label,
            logo_mode=logo_mode,
        ),
    )]


def _safe_filename(value: str) -> str:
    clean = "".join(ch for ch in value if ch not in r'<>:"/\|?*').strip()
    return clean[:80] or "Клиент"


def _folder_path_segments(value: str) -> list[str]:
    return [
        _safe_filename(segment)
        for segment in str(value or "").replace("\\", "/").split("/")
        if _safe_filename(segment)
    ] or ["Экспорт"]


def _export_date_folder_segments(now: Optional[datetime] = None) -> list[str]:
    current = now or datetime.now(DRIVE_EXPORT_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=DRIVE_EXPORT_TZ)
    current = current.astimezone(DRIVE_EXPORT_TZ)
    return [
        current.strftime("%Y"),
        f"{current.strftime('%m')} - {MONTH_LABELS.get(current.month, current.strftime('%m'))}",
        current.strftime("%d.%m.%Y"),
    ]


def _credentials_info() -> dict[str, Any]:
    raw = base64.b64decode(settings.GOOGLE_SERVICE_ACCOUNT_JSON_B64.encode("utf-8")).decode("utf-8")
    return json.loads(raw)


def _drive_auth_mode() -> str:
    mode = settings.GOOGLE_DRIVE_AUTH_MODE.strip().lower() or "auto"
    return mode if mode in GOOGLE_DRIVE_AUTH_MODES else "auto"


def _has_oauth_credentials() -> bool:
    return bool(
        settings.GOOGLE_OAUTH_CLIENT_ID.strip()
        and settings.GOOGLE_OAUTH_CLIENT_SECRET.strip()
        and settings.GOOGLE_OAUTH_REFRESH_TOKEN.strip()
    )


def _build_google_credentials():
    try:
        from google.oauth2.credentials import Credentials
        from google.oauth2 import service_account
    except ImportError as exc:
        raise RuntimeError("Не установлены google-api-python-client/google-auth для Google Drive выгрузки") from exc

    auth_mode = _drive_auth_mode()
    if auth_mode in {"auto", "oauth"} and _has_oauth_credentials():
        return Credentials(
            token=None,
            refresh_token=settings.GOOGLE_OAUTH_REFRESH_TOKEN.strip(),
            token_uri=settings.GOOGLE_OAUTH_TOKEN_URI.strip() or "https://oauth2.googleapis.com/token",
            client_id=settings.GOOGLE_OAUTH_CLIENT_ID.strip(),
            client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET.strip(),
            scopes=[DRIVE_SCOPE],
        )
    if auth_mode in {"auto", "service_account"} and settings.GOOGLE_SERVICE_ACCOUNT_JSON_B64.strip():
        return service_account.Credentials.from_service_account_info(
            _credentials_info(),
            scopes=[DRIVE_SCOPE],
        )
    raise RuntimeError("Не настроены Google OAuth или service account для Google Drive выгрузки")


def _build_drive_service():
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError("Не установлены google-api-python-client/google-auth для Google Drive выгрузки") from exc

    return build("drive", "v3", credentials=_build_google_credentials(), cache_discovery=False)


def _build_sheets_service():
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError("Не установлены google-api-python-client/google-auth для Google Sheets выгрузки") from exc

    return build("sheets", "v4", credentials=_build_google_credentials(), cache_discovery=False)


def _create_folder(service, *, name: str, parent_id: str) -> dict[str, Any]:
    metadata = {
        "name": name,
        "mimeType": DRIVE_FOLDER_MIME,
        "parents": [parent_id],
    }
    return service.files().create(body=metadata, fields="id,name,webViewLink", supportsAllDrives=True).execute()


def _escape_drive_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _get_or_create_folder(service, *, name: str, parent_id: str) -> dict[str, Any]:
    escaped_name = _escape_drive_query_value(name)
    escaped_parent = _escape_drive_query_value(parent_id)
    query = (
        f"mimeType='{DRIVE_FOLDER_MIME}' "
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


def _find_drive_files(service, *, name: str, parent_id: str, mime_type: str) -> list[dict[str, Any]]:
    escaped_name = _escape_drive_query_value(name)
    escaped_parent = _escape_drive_query_value(parent_id)
    escaped_mime_type = _escape_drive_query_value(mime_type)
    query = (
        f"name='{escaped_name}' "
        f"and '{escaped_parent}' in parents "
        f"and mimeType='{escaped_mime_type}' "
        "and trashed=false"
    )
    result = service.files().list(
        q=query,
        fields="files(id,name,webViewLink,mimeType)",
        pageSize=10,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    return list(result.get("files") or [])


def _delete_drive_file(service, *, file_id: str) -> None:
    service.files().delete(fileId=file_id, supportsAllDrives=True).execute()


def _upload_file(service, *, path: Path, title: str, parent_id: str, as_google_sheet: bool) -> dict[str, str]:
    from googleapiclient.http import MediaFileUpload

    target_mime_type = DRIVE_GOOGLE_SHEET_MIME if as_google_sheet else DRIVE_XLSX_MIME
    metadata = {"name": title}
    if as_google_sheet:
        metadata["mimeType"] = DRIVE_GOOGLE_SHEET_MIME
    media = MediaFileUpload(
        str(path),
        mimetype=DRIVE_XLSX_MIME,
        resumable=False,
    )
    existing_files = _find_drive_files(service, name=title, parent_id=parent_id, mime_type=target_mime_type)
    if existing_files:
        created = service.files().update(
            fileId=existing_files[0]["id"],
            body=metadata,
            media_body=media,
            fields="id,name,webViewLink,mimeType",
            supportsAllDrives=True,
        ).execute()
        for duplicate in existing_files[1:]:
            duplicate_id = str(duplicate.get("id") or "")
            if duplicate_id:
                _delete_drive_file(service, file_id=duplicate_id)
    else:
        created = service.files().create(
            body={**metadata, "parents": [parent_id]},
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


def _escape_sheet_name(value: str) -> str:
    return str(value or "").replace("'", "''")


def _google_sheet_icon_range(icon_cell: GoogleSheetIconCell) -> str:
    return f"'{_escape_sheet_name(icon_cell.sheet)}'!{get_column_letter(icon_cell.column)}{icon_cell.row}"


def _google_sheet_icon_formula(icon_cell: GoogleSheetIconCell) -> str:
    code_query = quote(",".join(icon_cell.codes), safe=",")
    logo_url = f"{settings.API_BASE_URL.rstrip('/')}/api/stats/export/bookmaker-logo.png?codes={code_query}"
    height = max(1, int(icon_cell.height))
    width = max(1, int(icon_cell.width))
    return f'=IMAGE("{logo_url}", 4, {height}, {width})'


def _postprocess_google_sheet_icon_cells(
    sheets_service,
    *,
    spreadsheet_id: str,
    icon_cells: Iterable[GoogleSheetIconCell],
) -> None:
    data = [
        {
            "range": _google_sheet_icon_range(icon_cell),
            "values": [[_google_sheet_icon_formula(icon_cell)]],
        }
        for icon_cell in icon_cells
        if icon_cell.codes
    ]
    for start in range(0, len(data), 500):
        chunk = data[start:start + 500]
        if not chunk:
            continue
        sheets_service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "valueInputOption": "USER_ENTERED",
                "data": chunk,
            },
        ).execute()


def _upload_artifacts(
    artifacts: list[dict[str, Any]],
    formats: list[str],
    *,
    root_folder_name: str = DRIVE_EXPORT_ROOT_FOLDER_NAME,
    base_folder_segments: Optional[list[str]] = None,
    temp_prefix: str = "shamrai-stats-",
) -> list[dict[str, str]]:
    service = _build_drive_service()
    sheets_service = None
    root_id = settings.GOOGLE_DRIVE_STATS_FOLDER_ID.strip()
    exports_root = _get_or_create_folder(
        service,
        name=root_folder_name,
        parent_id=root_id,
    )
    parent_id = exports_root["id"]
    folder_cache: dict[tuple[str, ...], str] = {}
    links: list[dict[str, str]] = [
        {
            "title": exports_root["name"],
            "url": exports_root.get("webViewLink", ""),
            "id": exports_root["id"],
            "format": "folder",
        },
    ]

    base_path: list[str] = []
    for folder_name in base_folder_segments or []:
        for segment in _folder_path_segments(folder_name):
            base_path.append(segment)
            cache_key = tuple(base_path)
            folder = _get_or_create_folder(service, name=segment, parent_id=parent_id)
            folder_cache[cache_key] = folder["id"]
            links.append({
                "title": "/".join(base_path),
                "url": folder.get("webViewLink", ""),
                "id": folder["id"],
                "format": "folder",
            })
            parent_id = folder["id"]

    date_parent_id = parent_id
    date_path: list[str] = []
    for folder_name in _export_date_folder_segments():
        date_path.append(folder_name)
        full_path = [*base_path, *date_path]
        cache_key = tuple(full_path)
        folder = _get_or_create_folder(service, name=folder_name, parent_id=date_parent_id)
        folder_cache[cache_key] = folder["id"]
        links.append({
            "title": "/".join(full_path),
            "url": folder.get("webViewLink", ""),
            "id": folder["id"],
            "format": "folder",
        })
        date_parent_id = folder["id"]

    with tempfile.TemporaryDirectory(prefix=temp_prefix) as tmp_dir:
        tmp_path = Path(tmp_dir)
        for artifact in artifacts:
            parent_id = date_parent_id
            path_key = [*base_path, *date_path]
            for folder_name in _folder_path_segments(artifact["folder"]):
                path_key.append(folder_name)
                cache_key = tuple(path_key)
                if cache_key not in folder_cache:
                    folder = _get_or_create_folder(service, name=folder_name, parent_id=parent_id)
                    folder_cache[cache_key] = folder["id"]
                    links.append({
                        "title": "/".join(path_key),
                        "url": folder.get("webViewLink", ""),
                        "id": folder["id"],
                        "format": "folder",
                    })
                parent_id = folder_cache[cache_key]
            safe_title = _safe_filename(artifact["title"])
            xlsx_path = tmp_path / f"{safe_title}.xlsx"
            xlsx_path.write_bytes(artifact["xlsx"])
            if "xlsx" in formats:
                links.append(_upload_file(
                    service,
                    path=xlsx_path,
                    title=f"{artifact['title']}.xlsx",
                    parent_id=parent_id,
                    as_google_sheet=False,
                ))
            if "google_sheet" in formats:
                google_sheet_path = tmp_path / f"{safe_title}.google-sheet.xlsx"
                google_sheet_path.write_bytes(artifact.get("google_sheet_xlsx") or artifact["xlsx"])
                uploaded_sheet = _upload_file(
                    service,
                    path=google_sheet_path,
                    title=artifact["title"],
                    parent_id=parent_id,
                    as_google_sheet=True,
                )
                links.append(uploaded_sheet)
                icon_cells = list(artifact.get("google_sheet_icon_cells") or [])
                if icon_cells and uploaded_sheet.get("id"):
                    if sheets_service is None:
                        sheets_service = _build_sheets_service()
                    _postprocess_google_sheet_icon_cells(
                        sheets_service,
                        spreadsheet_id=uploaded_sheet["id"],
                        icon_cells=icon_cells,
                    )
    return links

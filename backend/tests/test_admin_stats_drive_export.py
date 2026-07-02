import unittest
import sys
import types
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.api.admin import (
    AdminCrmDriveExportRequest,
    AdminStatsDriveExportRequest,
    create_admin_stats_drive_export,
    create_admin_users_drive_export,
    export_admin_stats,
    export_admin_users,
)
from src.services import google_drive_export
from src.services.stats_export import ClientInfoExportRow, ClientRecentBetExportRow, GoogleSheetIconCell


class _FakeDriveRequest:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class _FakeFilesResource:
    def __init__(self):
        self.created = []
        self.uploaded = []
        self.uploaded_media_bytes = []
        self.updated = []
        self.updated_media_bytes = []
        self.deleted = []
        self.list_queries = []
        self.records = []

    def _query_value(self, query, prefix):
        marker = f"{prefix}='"
        if marker not in query:
            return None
        return query.split(marker, 1)[1].split("'", 1)[0]

    def _query_parent(self, query):
        marker = "'"
        suffix = "' in parents"
        if suffix not in query:
            return None
        before = query.split(suffix, 1)[0]
        return before.rsplit(marker, 1)[-1]

    def list(self, **kwargs):
        self.list_queries.append(kwargs)
        query = kwargs.get("q", "")
        name = self._query_value(query, "name")
        mime_type = self._query_value(query, "mimeType")
        parent = self._query_parent(query)
        files = [
            {
                "id": record["id"],
                "name": record["name"],
                "webViewLink": record["webViewLink"],
                "mimeType": record["mimeType"],
            }
            for record in self.records
            if (not name or record["name"] == name)
            and (not mime_type or record["mimeType"] == mime_type)
            and (not parent or parent in record["parents"])
            and not record.get("trashed")
        ]
        return _FakeDriveRequest({"files": files[: kwargs.get("pageSize", 10)]})

    def create(self, *, body, fields, supportsAllDrives, media_body=None):
        saved_body = dict(body)
        file_id = f"id-{len(self.records) + 1}"
        saved_body["id"] = file_id
        self.created.append(saved_body)
        is_upload = media_body is not None
        if is_upload:
            self.uploaded.append(saved_body)
            self.uploaded_media_bytes.append(Path(media_body).read_bytes())
        record = {
            "id": file_id,
            "name": body["name"],
            "parents": list(body.get("parents") or []),
            "webViewLink": f"https://drive.google.com/{file_id}",
            "mimeType": body.get("mimeType", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "trashed": False,
        }
        self.records.append(record)
        return _FakeDriveRequest({
            "id": file_id,
            "name": body["name"],
            "webViewLink": f"https://drive.google.com/{file_id}",
            "mimeType": body.get("mimeType", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        })

    def update(self, *, fileId, body, fields, supportsAllDrives, media_body=None):
        record = next(record for record in self.records if record["id"] == fileId)
        record["name"] = body.get("name", record["name"])
        if body.get("mimeType"):
            record["mimeType"] = body["mimeType"]
        saved_body = dict(body)
        saved_body["id"] = fileId
        self.updated.append(saved_body)
        if media_body is not None:
            self.updated_media_bytes.append(Path(media_body).read_bytes())
        return _FakeDriveRequest({
            "id": fileId,
            "name": record["name"],
            "webViewLink": record["webViewLink"],
            "mimeType": record["mimeType"],
        })

    def delete(self, *, fileId, supportsAllDrives):
        record = next(record for record in self.records if record["id"] == fileId)
        record["trashed"] = True
        self.deleted.append(fileId)
        return _FakeDriveRequest({})


class _FakeDriveService:
    def __init__(self):
        self.files_resource = _FakeFilesResource()

    def files(self):
        return self.files_resource


class _FakeSheetsValuesResource:
    def __init__(self):
        self.batch_updates = []

    def batchUpdate(self, *, spreadsheetId, body):
        self.batch_updates.append({"spreadsheetId": spreadsheetId, "body": body})
        return _FakeDriveRequest({"updatedCells": sum(len(item["values"]) for item in body.get("data", []))})


class _FakeSheetsSpreadsheetsResource:
    def __init__(self):
        self.values_resource = _FakeSheetsValuesResource()

    def values(self):
        return self.values_resource


class _FakeSheetsService:
    def __init__(self):
        self.spreadsheets_resource = _FakeSheetsSpreadsheetsResource()

    def spreadsheets(self):
        return self.spreadsheets_resource


class _FakeGoogleHttpError(Exception):
    def __init__(self, *, status_code=403, reason="storageQuotaExceeded", message="Storage quota exceeded"):
        self.resp = types.SimpleNamespace(status=status_code)
        self.content = (
            '{"error":{"message":"%s","errors":[{"reason":"%s"}]}}'
            % (message, reason)
        ).encode("utf-8")

    def __str__(self):
        return "HttpError 403 when requesting https://www.googleapis.com/drive/v3/files"


def _client_info_row(**overrides):
    data = {
        "user_id": 1,
        "client_name": "Test Client",
        "username": "test_client",
        "phone": "",
        "vk_user_id": "",
        "is_web_only": False,
        "bookmaker_names": "Fonbet",
        "client_group": "",
        "client_tag": "",
        "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
        "matches_remaining": 0,
        "guarantee_active": False,
        "total_taken_bets": 0,
        "settled_bets": 0,
        "pending_bets": 0,
        "refund_bets": 0,
        "bets": 0,
        "wins": 0,
        "losses": 0,
        "winrate": 0.0,
        "roi": 0.0,
        "profit_units": 0.0,
        "average_coefficient": 0.0,
        "current_streak": 0,
        "current_streak_type": None,
        "max_win_streak": 0,
        "max_loss_streak": 0,
        "recent_results": [],
        "situation_code": "empty",
        "situation_label": "Без ставок",
        "situation_tone": "neutral",
        "situation_description": "Нет данных по ставкам.",
        "bookmaker_logo_codes": [],
    }
    data.update(overrides)
    return ClientInfoExportRow(**data)


def _recent_client_row(**overrides):
    data = {
        "user_id": 1,
        "client_name": "Test Client",
        "username": "test_client",
        "phone": "",
        "vk_user_id": "",
        "is_web_only": False,
        "client_group": "",
        "client_tag": "",
        "matches_remaining": 0,
        "guarantee_active": False,
        "taken_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
        "event_name": "Test match",
        "sport_type": "football",
        "bookmaker_names": "Fonbet",
        "coefficient": Decimal("1.90"),
        "outcome": "П1",
        "status": "pending",
        "result_label": "Ожидает",
        "resolved_at": None,
        "source_type": "feed",
        "access_type": "paid",
        "match_charged": True,
        "bet_id": "bet-1",
        "bookmaker_logo_codes": [],
    }
    data.update(overrides)
    return ClientRecentBetExportRow(**data)


async def _streaming_response_text(response) -> str:
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.encode("utf-8") if isinstance(chunk, str) else chunk)
    return b"".join(chunks).decode("utf-8-sig")


class AdminStatsDriveExportTests(unittest.IsolatedAsyncioTestCase):
    async def test_drive_export_rejects_bad_scope(self):
        with self.assertRaises(Exception) as exc:
            await create_admin_stats_drive_export(
                AdminStatsDriveExportRequest(scope="bad", period="all", formats=["xlsx"]),
                admin=object(),
            )

        self.assertEqual(getattr(exc.exception, "status_code", None), 400)

    async def test_drive_export_starts_job_with_normalized_formats(self):
        with patch("src.api.admin.start_drive_export_job") as start_job:
            start_job.return_value = {
                "id": "job-1",
                "status": "pending",
                "scope": "all",
                "period": "all",
                "formats": ["xlsx", "google_sheet"],
                "links": [],
                "error": None,
            }

            result = await create_admin_stats_drive_export(
                AdminStatsDriveExportRequest(
                    scope="all",
                    period="bad-period",
                    formats=["xlsx", "xlsx", "google_sheet"],
                ),
                admin=object(),
            )

        self.assertEqual(result["id"], "job-1")
        start_job.assert_called_once_with(
            scope="all",
            period="all",
            formats=["xlsx", "google_sheet"],
        )

    async def test_clients_drive_export_can_start_google_sheet_only_job(self):
        with patch("src.api.admin.start_drive_export_job") as start_job:
            start_job.return_value = {
                "id": "job-clients",
                "status": "pending",
                "scope": "clients",
                "period": "all",
                "formats": ["google_sheet"],
                "links": [],
                "error": None,
            }

            result = await create_admin_stats_drive_export(
                AdminStatsDriveExportRequest(scope="clients", period="all", formats=["google_sheet"]),
                admin=object(),
            )

        self.assertEqual(result["id"], "job-clients")
        start_job.assert_called_once_with(
            scope="clients",
            period="all",
            formats=["google_sheet"],
        )

    async def test_drive_export_defaults_to_google_sheet_only(self):
        with patch("src.api.admin.start_drive_export_job") as start_job:
            start_job.return_value = {
                "id": "job-default",
                "status": "pending",
                "scope": "all",
                "period": "all",
                "formats": ["google_sheet"],
                "links": [],
                "error": None,
            }

            result = await create_admin_stats_drive_export(
                AdminStatsDriveExportRequest(scope="all", period="all", formats=[]),
                admin=object(),
            )

        self.assertEqual(result["id"], "job-default")
        start_job.assert_called_once_with(
            scope="all",
            period="all",
            formats=["google_sheet"],
        )

    async def test_direct_xlsx_export_supports_shamrai_scope(self):
        db = object()
        with (
            patch("src.api.admin.load_shamrai_export_items", new=AsyncMock(return_value=[])) as load_items,
            patch("src.api.admin.build_stats_export_workbook", return_value=b"fake-xlsx") as build_workbook,
        ):
            response = await export_admin_stats(
                scope="shamrai",
                format="xlsx",
                period="all",
                source="all",
                admin=object(),
                db=db,
            )

        load_items.assert_awaited_once_with(db, "all")
        build_workbook.assert_called_once()
        self.assertIn("shamrai_stats_shamrai_all.xlsx", response.headers["content-disposition"])

    async def test_direct_xlsx_export_clients_scope_downloads_client_info_workbook(self):
        db = object()
        with (
            patch("src.api.admin.load_client_info_export_rows", new=AsyncMock(return_value=[])) as load_info_rows,
            patch("src.api.admin.load_client_recent_bet_export_rows", new=AsyncMock(return_value=[])) as load_recent_rows,
            patch("src.api.admin.build_client_info_export_workbook", return_value=b"client-info-xlsx") as build_workbook,
            patch("src.api.admin.load_clients_export_items", new=AsyncMock()) as load_client_items,
            patch("src.api.admin.build_stats_export_workbook") as build_stats_workbook,
        ):
            response = await export_admin_stats(
                scope="clients",
                format="xlsx",
                period="all",
                source="all",
                admin=object(),
                db=db,
            )

        load_info_rows.assert_awaited_once_with(db, "all")
        load_recent_rows.assert_awaited_once_with(db, "all", limit_per_client=None)
        build_workbook.assert_called_once_with([], recent_rows=[], period_label="Весь период")
        load_client_items.assert_not_called()
        build_stats_workbook.assert_not_called()
        self.assertIn("shamrai_clients_info_all.xlsx", response.headers["content-disposition"])

    async def test_crm_xlsx_export_uses_filtered_client_info_workbook(self):
        db = object()
        rows = [
            _client_info_row(user_id=1, client_name="Active Client", client_group="VIP", client_tag="hot", matches_remaining=3, bookmaker_ids=[1]),
            _client_info_row(user_id=2, client_name="Active Bookmaker Client", client_group="VIP", client_tag="hot", matches_remaining=3, bookmaker_ids=[2]),
        ]
        recent_rows = [
            _recent_client_row(user_id=1, event_name="Active match"),
            _recent_client_row(user_id=2, event_name="Empty match"),
        ]
        with (
            patch("src.api.admin.load_client_info_export_rows", new=AsyncMock(return_value=rows)) as load_info_rows,
            patch("src.api.admin.load_client_recent_bet_export_rows", new=AsyncMock(return_value=recent_rows)) as load_recent_rows,
            patch("src.api.admin.build_client_info_export_workbook", return_value=b"crm-xlsx") as build_workbook,
        ):
            response = await export_admin_users(
                format="xlsx",
                q="active",
                activity="active",
                group="VIP",
                tag="hot",
                bookmaker_id=2,
                admin=object(),
                db=db,
            )

        load_info_rows.assert_awaited_once_with(db, "all")
        load_recent_rows.assert_awaited_once_with(db, "all", limit_per_client=None)
        build_workbook.assert_called_once_with([rows[1]], recent_rows=[recent_rows[1]], period_label="CRM: клиенты")
        self.assertIn("shamrai_clients_crm.xlsx", response.headers["content-disposition"])

    async def test_crm_csv_export_contains_client_situation_and_remaining_matches(self):
        db = object()
        rows = [
            _client_info_row(
                user_id=7,
                client_name="Ivan Client",
                username="ivan",
                bookmaker_names="Fonbet, Лига Ставок",
                client_group="VIP",
                client_tag="risk",
                matches_remaining=-1,
                guarantee_active=True,
                situation_label="Нужен контроль",
                situation_description="Серия минусов и долг по матчам.",
            )
        ]
        with (
            patch("src.api.admin.load_client_info_export_rows", new=AsyncMock(return_value=rows)),
            patch("src.api.admin.load_client_recent_bet_export_rows", new=AsyncMock(return_value=[])),
        ):
            response = await export_admin_users(
                format="csv",
                q=None,
                activity="all",
                group=None,
                tag=None,
                admin=object(),
                db=db,
            )

        body = await _streaming_response_text(response)
        self.assertIn("Матчей осталось", body)
        self.assertIn("Ситуация", body)
        self.assertIn("Ivan Client", body)
        self.assertIn("-1", body)
        self.assertIn("Нужен контроль", body)

    async def test_crm_drive_export_starts_google_drive_job_with_filters(self):
        with patch("src.api.admin.start_crm_drive_export_job") as start_job:
            start_job.return_value = {
                "id": "crm-job",
                "status": "pending",
                "scope": "crm",
                "period": "all",
                "formats": ["xlsx", "google_sheet"],
                "links": [],
                "error": None,
            }

            result = await create_admin_users_drive_export(
                AdminCrmDriveExportRequest(
                    q="vip",
                    activity="active",
                    group="VIP",
                    tag="hot",
                    bookmaker_id=2,
                    formats=["xlsx", "google_sheet", "xlsx"],
                ),
                admin=object(),
            )

        self.assertEqual(result["id"], "crm-job")
        start_job.assert_called_once_with(
            q="vip",
            activity="active",
            group="VIP",
            tag="hot",
            bookmaker_id=2,
            formats=["xlsx", "google_sheet"],
        )


class GoogleDriveUploadTests(unittest.TestCase):
    def test_format_drive_error_hides_raw_google_url_and_explains_quota(self):
        message = google_drive_export._format_drive_export_error(_FakeGoogleHttpError())

        self.assertIn("Google Drive отказал в создании файла", message)
        self.assertIn("квот", message.lower())
        self.assertIn("OAuth", message)
        self.assertNotIn("googleapis.com", message)

    def test_format_drive_error_explains_missing_folder_permission(self):
        message = google_drive_export._format_drive_export_error(
            _FakeGoogleHttpError(reason="insufficientFilePermissions", message="The user does not have sufficient permissions")
        )

        self.assertIn("Google Drive отказал в доступе", message)
        self.assertIn("Google-аккаунт", message)
        self.assertIn("редактора", message)

    def test_drive_enabled_accepts_oauth_without_service_account(self):
        with (
            patch.object(google_drive_export.settings, "GOOGLE_DRIVE_STATS_ENABLED", True),
            patch.object(google_drive_export.settings, "GOOGLE_DRIVE_STATS_FOLDER_ID", "folder-id"),
            patch.object(google_drive_export.settings, "GOOGLE_DRIVE_AUTH_MODE", "oauth"),
            patch.object(google_drive_export.settings, "GOOGLE_OAUTH_CLIENT_ID", "client-id"),
            patch.object(google_drive_export.settings, "GOOGLE_OAUTH_CLIENT_SECRET", "client-secret"),
            patch.object(google_drive_export.settings, "GOOGLE_OAUTH_REFRESH_TOKEN", "refresh-token"),
            patch.object(google_drive_export.settings, "GOOGLE_SERVICE_ACCOUNT_JSON_B64", ""),
        ):
            google_drive_export._drive_enabled_or_400()

    def test_drive_enabled_rejects_missing_credentials_in_auto_mode(self):
        with (
            patch.object(google_drive_export.settings, "GOOGLE_DRIVE_STATS_ENABLED", True),
            patch.object(google_drive_export.settings, "GOOGLE_DRIVE_STATS_FOLDER_ID", "folder-id"),
            patch.object(google_drive_export.settings, "GOOGLE_DRIVE_AUTH_MODE", "auto"),
            patch.object(google_drive_export.settings, "GOOGLE_OAUTH_CLIENT_ID", ""),
            patch.object(google_drive_export.settings, "GOOGLE_OAUTH_CLIENT_SECRET", ""),
            patch.object(google_drive_export.settings, "GOOGLE_OAUTH_REFRESH_TOKEN", ""),
            patch.object(google_drive_export.settings, "GOOGLE_SERVICE_ACCOUNT_JSON_B64", ""),
        ):
            with self.assertRaises(Exception) as exc:
                google_drive_export._drive_enabled_or_400()

        self.assertEqual(getattr(exc.exception, "status_code", None), 400)
        self.assertIn("Google OAuth", exc.exception.detail)

    def test_build_drive_service_prefers_oauth_credentials(self):
        captured = {}

        class _FakeCredentials:
            def __init__(self, **kwargs):
                captured["oauth"] = kwargs

        fake_googleapiclient = types.ModuleType("googleapiclient")
        fake_discovery = types.ModuleType("googleapiclient.discovery")
        fake_discovery.build = lambda api, version, **kwargs: {"api": api, "version": version, **kwargs}
        fake_googleapiclient.discovery = fake_discovery
        fake_google = types.ModuleType("google")
        fake_oauth2 = types.ModuleType("google.oauth2")
        fake_oauth_credentials = types.ModuleType("google.oauth2.credentials")
        fake_oauth_credentials.Credentials = _FakeCredentials
        fake_service_account = types.ModuleType("google.oauth2.service_account")
        fake_service_account.Credentials = types.SimpleNamespace(from_service_account_info=lambda *_args, **_kwargs: object())
        fake_oauth2.credentials = fake_oauth_credentials
        fake_oauth2.service_account = fake_service_account
        fake_google.oauth2 = fake_oauth2

        with (
            patch.object(google_drive_export.settings, "GOOGLE_DRIVE_AUTH_MODE", "auto"),
            patch.object(google_drive_export.settings, "GOOGLE_OAUTH_CLIENT_ID", "client-id"),
            patch.object(google_drive_export.settings, "GOOGLE_OAUTH_CLIENT_SECRET", "client-secret"),
            patch.object(google_drive_export.settings, "GOOGLE_OAUTH_REFRESH_TOKEN", "refresh-token"),
            patch.object(google_drive_export.settings, "GOOGLE_OAUTH_TOKEN_URI", "https://oauth2.googleapis.com/token"),
            patch.object(google_drive_export.settings, "GOOGLE_SERVICE_ACCOUNT_JSON_B64", "ignored-service-account"),
            patch.dict(sys.modules, {
                "google": fake_google,
                "google.oauth2": fake_oauth2,
                "google.oauth2.credentials": fake_oauth_credentials,
                "google.oauth2.service_account": fake_service_account,
                "googleapiclient": fake_googleapiclient,
                "googleapiclient.discovery": fake_discovery,
            }),
        ):
            service = google_drive_export._build_drive_service()

        self.assertEqual(service["api"], "drive")
        self.assertEqual(captured["oauth"]["refresh_token"], "refresh-token")
        self.assertEqual(captured["oauth"]["client_id"], "client-id")
        self.assertEqual(captured["oauth"]["scopes"], [google_drive_export.DRIVE_SCOPE])

    def test_upload_artifacts_creates_date_scope_folders_and_formats(self):
        service = _FakeDriveService()
        artifacts = [
            {"folder": "Шамрай", "title": "Шамрай - статистика", "xlsx": b"shamrai"},
            {"folder": "Клиенты", "title": "Клиенты - свод", "xlsx": b"clients"},
            {"folder": "Клиенты/Инфа", "title": "Клиенты - инфа", "xlsx": b"client-info"},
        ]
        fake_googleapiclient = types.ModuleType("googleapiclient")
        fake_http = types.ModuleType("googleapiclient.http")
        fake_http.MediaFileUpload = lambda path, **_: Path(path)
        fake_googleapiclient.http = fake_http

        with (
            patch.object(google_drive_export, "_build_drive_service", return_value=service),
            patch.object(google_drive_export, "_export_date_folder_segments", return_value=["2026", "06 - Июнь", "22.06.2026"]),
            patch.object(google_drive_export.settings, "GOOGLE_DRIVE_STATS_FOLDER_ID", "parent-folder"),
            patch.dict(sys.modules, {
                "googleapiclient": fake_googleapiclient,
                "googleapiclient.http": fake_http,
            }),
        ):
            links = google_drive_export._upload_artifacts(artifacts, ["xlsx", "google_sheet"])

        folder_names = [
            body["name"]
            for body in service.files_resource.created
            if body.get("mimeType") == "application/vnd.google-apps.folder"
        ]
        self.assertEqual(folder_names[:4], ["Shamrai Stats Exports", "2026", "06 - Июнь", "22.06.2026"])
        self.assertIn("Шамрай", folder_names)
        self.assertIn("Клиенты", folder_names)
        self.assertIn("Инфа", folder_names)
        clients_folder = next(body for body in service.files_resource.created if body["name"] == "Клиенты")
        info_folder = next(body for body in service.files_resource.created if body["name"] == "Инфа")
        self.assertEqual(info_folder["parents"], [clients_folder["id"]])
        client_info_upload = next(body for body in service.files_resource.uploaded if body["name"] == "Клиенты - инфа.xlsx")
        self.assertEqual(client_info_upload["parents"], [info_folder["id"]])
        self.assertEqual(len(service.files_resource.uploaded), 6)
        self.assertEqual({link["format"] for link in links}, {"folder", "xlsx", "google_sheet"})
        self.assertIn("name='Shamrai Stats Exports'", service.files_resource.list_queries[0]["q"])
        self.assertIn("2026/06 - Июнь/22.06.2026", {link["title"] for link in links})

    def test_upload_crm_artifacts_uses_shamrai_exports_crm_folder(self):
        service = _FakeDriveService()
        artifacts = [
            {"folder": "Клиенты", "title": "CRM по клиентам", "xlsx": b"crm"},
        ]
        fake_googleapiclient = types.ModuleType("googleapiclient")
        fake_http = types.ModuleType("googleapiclient.http")
        fake_http.MediaFileUpload = lambda path, **_: Path(path)
        fake_googleapiclient.http = fake_http

        with (
            patch.object(google_drive_export, "_build_drive_service", return_value=service),
            patch.object(google_drive_export, "_export_date_folder_segments", return_value=["2026", "06 - Июнь", "22.06.2026"]),
            patch.object(google_drive_export.settings, "GOOGLE_DRIVE_STATS_FOLDER_ID", "parent-folder"),
            patch.dict(sys.modules, {
                "googleapiclient": fake_googleapiclient,
                "googleapiclient.http": fake_http,
            }),
        ):
            links = google_drive_export._upload_artifacts(
                artifacts,
                ["google_sheet"],
                root_folder_name=google_drive_export.CRM_DRIVE_EXPORT_ROOT_FOLDER_NAME,
                base_folder_segments=[google_drive_export.CRM_DRIVE_EXPORT_FOLDER_NAME],
                temp_prefix="shamrai-crm-",
            )

        folder_names = [
            body["name"]
            for body in service.files_resource.created
            if body.get("mimeType") == "application/vnd.google-apps.folder"
        ]
        self.assertEqual(folder_names[:2], ["Shamrai Exports", "CRM"])
        self.assertIn("Клиенты", folder_names)
        self.assertIn("CRM/2026/06 - Июнь/22.06.2026", {link["title"] for link in links})
        self.assertIn("name='Shamrai Exports'", service.files_resource.list_queries[0]["q"])
        self.assertEqual(service.files_resource.uploaded[0]["name"], "CRM по клиентам")

    def test_upload_artifacts_updates_same_day_files_instead_of_creating_duplicates(self):
        service = _FakeDriveService()
        artifacts = [
            {"folder": "Клиенты/Инфа", "title": "Клиенты - инфа", "xlsx": b"first"},
        ]
        fake_googleapiclient = types.ModuleType("googleapiclient")
        fake_http = types.ModuleType("googleapiclient.http")
        fake_http.MediaFileUpload = lambda path, **_: Path(path)
        fake_googleapiclient.http = fake_http

        with (
            patch.object(google_drive_export, "_build_drive_service", return_value=service),
            patch.object(google_drive_export, "_export_date_folder_segments", return_value=["2026", "06 - Июнь", "22.06.2026"]),
            patch.object(google_drive_export.settings, "GOOGLE_DRIVE_STATS_FOLDER_ID", "parent-folder"),
            patch.dict(sys.modules, {
                "googleapiclient": fake_googleapiclient,
                "googleapiclient.http": fake_http,
            }),
        ):
            google_drive_export._upload_artifacts(artifacts, ["xlsx", "google_sheet"])
            google_drive_export._upload_artifacts(artifacts, ["xlsx", "google_sheet"])

        uploaded_names = [body["name"] for body in service.files_resource.uploaded]
        self.assertEqual(uploaded_names.count("Клиенты - инфа.xlsx"), 1)
        self.assertEqual(uploaded_names.count("Клиенты - инфа"), 1)
        self.assertEqual(
            [body["name"] for body in service.files_resource.updated],
            ["Клиенты - инфа.xlsx", "Клиенты - инфа"],
        )

    def test_upload_artifacts_can_create_google_sheet_without_xlsx_duplicate(self):
        service = _FakeDriveService()
        artifacts = [
            {"folder": "Клиенты/Инфа", "title": "Клиенты - инфа", "xlsx": b"client-info"},
        ]
        fake_googleapiclient = types.ModuleType("googleapiclient")
        fake_http = types.ModuleType("googleapiclient.http")
        fake_http.MediaFileUpload = lambda path, **_: Path(path)
        fake_googleapiclient.http = fake_http

        with (
            patch.object(google_drive_export, "_build_drive_service", return_value=service),
            patch.object(google_drive_export.settings, "GOOGLE_DRIVE_STATS_FOLDER_ID", "parent-folder"),
            patch.dict(sys.modules, {
                "googleapiclient": fake_googleapiclient,
                "googleapiclient.http": fake_http,
            }),
        ):
            links = google_drive_export._upload_artifacts(artifacts, ["google_sheet"])

        uploaded_names = [body["name"] for body in service.files_resource.uploaded]
        self.assertEqual(uploaded_names, ["Клиенты - инфа"])
        self.assertEqual(service.files_resource.uploaded[0]["mimeType"], "application/vnd.google-apps.spreadsheet")
        self.assertNotIn("Клиенты - инфа.xlsx", uploaded_names)
        self.assertEqual({link["format"] for link in links}, {"folder", "google_sheet"})

    def test_upload_artifacts_postprocesses_google_sheet_icon_cells(self):
        service = _FakeDriveService()
        sheets_service = _FakeSheetsService()
        artifacts = [
            {
                "folder": "Шамрай",
                "title": "Шамрай - статистика",
                "xlsx": b"floating-xlsx",
                "google_sheet_xlsx": b"cell-xlsx",
                "google_sheet_icon_cells": [
                    GoogleSheetIconCell(
                        sheet="Детально",
                        row=5,
                        column=7,
                        codes=("fonbet",),
                        width=34,
                        height=18,
                    )
                ],
            },
        ]
        fake_googleapiclient = types.ModuleType("googleapiclient")
        fake_http = types.ModuleType("googleapiclient.http")
        fake_http.MediaFileUpload = lambda path, **_: Path(path)
        fake_googleapiclient.http = fake_http

        with (
            patch.object(google_drive_export, "_build_drive_service", return_value=service),
            patch.object(google_drive_export, "_build_sheets_service", return_value=sheets_service) as build_sheets,
            patch.object(google_drive_export.settings, "GOOGLE_DRIVE_STATS_FOLDER_ID", "parent-folder"),
            patch.object(google_drive_export.settings, "API_BASE_URL", "https://shamra1.pro"),
            patch.dict(sys.modules, {
                "googleapiclient": fake_googleapiclient,
                "googleapiclient.http": fake_http,
            }),
        ):
            google_drive_export._upload_artifacts(artifacts, ["xlsx", "google_sheet"])

        self.assertEqual(service.files_resource.uploaded_media_bytes[-2:], [b"floating-xlsx", b"cell-xlsx"])
        build_sheets.assert_called_once()
        batch_updates = sheets_service.spreadsheets_resource.values_resource.batch_updates
        self.assertEqual(len(batch_updates), 1)
        update = batch_updates[0]
        google_sheet_upload = next(body for body in service.files_resource.uploaded if body["name"] == "Шамрай - статистика")
        self.assertEqual(update["spreadsheetId"], google_sheet_upload["id"])
        self.assertEqual(update["body"]["valueInputOption"], "USER_ENTERED")
        self.assertEqual(update["body"]["data"][0]["range"], "'Детально'!G5")
        formula = update["body"]["data"][0]["values"][0][0]
        self.assertEqual(
            formula,
            '=IMAGE("https://shamra1.pro/api/stats/export/bookmaker-logo.png?codes=fonbet", 4, 18, 34)',
        )

    def test_upload_xlsx_does_not_postprocess_google_sheet_icons(self):
        service = _FakeDriveService()
        artifacts = [
            {
                "folder": "Шамрай",
                "title": "Шамрай - статистика",
                "xlsx": b"floating-xlsx",
                "google_sheet_icon_cells": [
                    GoogleSheetIconCell(sheet="Детально", row=5, column=7, codes=("fonbet",), width=34, height=18),
                ],
            },
        ]
        fake_googleapiclient = types.ModuleType("googleapiclient")
        fake_http = types.ModuleType("googleapiclient.http")
        fake_http.MediaFileUpload = lambda path, **_: Path(path)
        fake_googleapiclient.http = fake_http

        with (
            patch.object(google_drive_export, "_build_drive_service", return_value=service),
            patch.object(google_drive_export, "_build_sheets_service") as build_sheets,
            patch.object(google_drive_export.settings, "GOOGLE_DRIVE_STATS_FOLDER_ID", "parent-folder"),
            patch.dict(sys.modules, {
                "googleapiclient": fake_googleapiclient,
                "googleapiclient.http": fake_http,
            }),
        ):
            google_drive_export._upload_artifacts(artifacts, ["xlsx"])

        build_sheets.assert_not_called()


if __name__ == "__main__":
    unittest.main()

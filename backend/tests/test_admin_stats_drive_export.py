import unittest
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.api.admin import AdminStatsDriveExportRequest, create_admin_stats_drive_export, export_admin_stats
from src.services import google_drive_export


class _FakeDriveRequest:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class _FakeFilesResource:
    def __init__(self):
        self.created = []
        self.uploaded = []
        self.list_queries = []

    def list(self, **kwargs):
        self.list_queries.append(kwargs)
        return _FakeDriveRequest({"files": []})

    def create(self, *, body, fields, supportsAllDrives, media_body=None):
        self.created.append(body)
        is_upload = media_body is not None
        if is_upload:
            self.uploaded.append(body)
        file_id = f"id-{len(self.created)}"
        return _FakeDriveRequest({
            "id": file_id,
            "name": body["name"],
            "webViewLink": f"https://drive.google.com/{file_id}",
            "mimeType": body.get("mimeType", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        })


class _FakeDriveService:
    def __init__(self):
        self.files_resource = _FakeFilesResource()

    def files(self):
        return self.files_resource


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


class GoogleDriveUploadTests(unittest.TestCase):
    def test_upload_artifacts_creates_root_timestamp_scope_folders_and_formats(self):
        service = _FakeDriveService()
        artifacts = [
            {"folder": "Шамрай", "title": "Шамрай - статистика", "xlsx": b"shamrai"},
            {"folder": "Клиенты", "title": "Клиенты - свод", "xlsx": b"clients"},
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
            links = google_drive_export._upload_artifacts(artifacts, ["xlsx", "google_sheet"])

        folder_names = [
            body["name"]
            for body in service.files_resource.created
            if body.get("mimeType") == "application/vnd.google-apps.folder"
        ]
        self.assertEqual(folder_names[:2], ["Shamrai Stats Exports", links[1]["title"]])
        self.assertIn("Шамрай", folder_names)
        self.assertIn("Клиенты", folder_names)
        self.assertEqual(len(service.files_resource.uploaded), 4)
        self.assertEqual({link["format"] for link in links}, {"folder", "xlsx", "google_sheet"})
        self.assertIn("name='Shamrai Stats Exports'", service.files_resource.list_queries[0]["q"])


if __name__ == "__main__":
    unittest.main()

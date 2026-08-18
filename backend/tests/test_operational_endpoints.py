import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from src import main


class OperationalEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_liveness_does_not_probe_dependencies(self):
        with (
            patch.object(main, "database_readiness_probe", new=AsyncMock()) as database_probe,
            patch.object(main, "redis_readiness_probe", new=AsyncMock()) as redis_probe,
        ):
            payload = await main.health_check()

        self.assertEqual(payload, {"status": "ok"})
        database_probe.assert_not_awaited()
        redis_probe.assert_not_awaited()

    async def test_readiness_is_ok_only_when_database_redis_and_schema_are_ready(self):
        with (
            patch.object(
                main,
                "database_readiness_probe",
                new=AsyncMock(
                    return_value={
                        "database": "ok",
                        "schema": "ok",
                        "expected_revisions": ["20260802_0041"],
                        "current_revisions": ["20260802_0041"],
                    }
                ),
            ),
            patch.object(main, "redis_readiness_probe", new=AsyncMock(return_value={"redis": "ok"})),
        ):
            payload = await main.readiness_check()

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["checks"], {"database": "ok", "redis": "ok", "schema": "ok"})

    async def test_readiness_returns_503_without_leaking_dependency_errors(self):
        with (
            patch.object(
                main,
                "database_readiness_probe",
                new=AsyncMock(
                    return_value={
                        "database": "error",
                        "schema": "unknown",
                        "expected_revisions": ["20260802_0041"],
                        "current_revisions": [],
                    }
                ),
            ),
            patch.object(main, "redis_readiness_probe", new=AsyncMock(return_value={"redis": "error"})),
        ):
            response = await main.readiness_check()

        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 503)
        body = json.loads(response.body)
        self.assertEqual(body["status"], "not_ready")
        self.assertNotIn("detail", body)
        self.assertNotIn("exception", json.dumps(body).lower())

    def test_version_endpoint_returns_only_sanitized_release_metadata(self):
        with patch.dict(
            os.environ,
            {
                "SHAMRAI_GIT_SHA": "a" * 40,
                "SHAMRAI_BUILD_TIME": "2026-08-02T12:00:00Z",
            },
            clear=False,
        ):
            payload = main.version_check()

        self.assertEqual(payload["git_sha"], "a" * 40)
        self.assertEqual(payload["build_time"], "2026-08-02T12:00:00Z")
        self.assertEqual(set(payload), {"app_version", "git_sha", "build_time"})

    def test_operational_routes_are_public_and_maintenance_exempt(self):
        routes = {
            route.path: route
            for route in main.app.routes
            if isinstance(route, APIRoute) and route.path in {"/api/health", "/api/ready", "/api/version"}
        }

        self.assertEqual(set(routes), {"/api/health", "/api/ready", "/api/version"})
        self.assertTrue(all(not route.dependant.dependencies for route in routes.values()))
        self.assertTrue(main.is_maintenance_exempt_path("/api/ready"))
        self.assertTrue(main.is_maintenance_exempt_path("/api/version"))


if __name__ == "__main__":
    unittest.main()

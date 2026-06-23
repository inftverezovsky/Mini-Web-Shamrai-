from __future__ import annotations

import base64
import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException, status

# Pure parsing and normalization helpers called by admin_broadcast.py route handlers.


def _encode_forecast_request_cursor(forecast_request: Any) -> str:
    payload = {
        "created_at": forecast_request.created_at.isoformat() if forecast_request.created_at else "",
        "id": str(forecast_request.id),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_forecast_request_cursor(cursor: Optional[str]) -> tuple[datetime, UUID] | None:
    if not cursor:
        return None
    try:
        padded = cursor + ("=" * ((4 - len(cursor) % 4) % 4))
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        return datetime.fromisoformat(str(payload["created_at"])), UUID(str(payload["id"]))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Некорректный cursor заявок",
        )


def _parse_optional_fair_coefficient(value: Optional[object]) -> Optional[Decimal]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = Decimal(text)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Верный коэффициент должен быть числом",
        )
    if parsed < Decimal("1.0") or parsed > Decimal("999.99"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Верный коэффициент должен быть от 1.0 до 999.99",
        )
    return parsed


def _parse_bookmaker_id_values(values: Optional[list[str]]) -> list[int]:
    bookmaker_ids: list[int] = []
    for raw_value in values or []:
        if raw_value is None:
            continue
        for part in str(raw_value).split(","):
            clean = part.strip()
            if not clean:
                continue
            try:
                bookmaker_id = int(clean)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Некорректный ID букмекера",
                )
            if bookmaker_id not in bookmaker_ids:
                bookmaker_ids.append(bookmaker_id)
    return bookmaker_ids


def _merge_bookmaker_ids(
    bookmaker_id: Optional[int],
    bookmaker_ids: Optional[list[int]],
) -> list[int]:
    selected_ids: list[int] = []
    if bookmaker_id:
        selected_ids.append(bookmaker_id)
    for selected_id in bookmaker_ids or []:
        if selected_id and selected_id not in selected_ids:
            selected_ids.append(selected_id)
    return selected_ids


def _web_push_report_values(report: Optional[dict]) -> dict:
    report = report or {}
    return {
        "web_push_audience": int(report.get("web_push_audience") or 0),
        "web_push_sent": int(report.get("web_push_sent") or 0),
        "web_push_failed": int(report.get("web_push_failed") or 0),
        "web_push_missing_permission": int(report.get("web_push_missing_permission") or 0),
        "web_push_retry_queued": int(report.get("web_push_retry_queued") or 0),
        "web_push_errors": list(report.get("web_push_errors") or []),
    }

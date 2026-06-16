from __future__ import annotations

import logging
import ipaddress
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from threading import RLock
from typing import Deque, Iterable, Mapping, Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.core.config import settings
from src.core.security import verify_access_token

logger = logging.getLogger("uvicorn")

RATE_LIMIT_GROUPS = {
    "public_read",
    "auth",
    "payment",
    "webhook",
    "admin",
    "upload",
    "default",
}

DEFAULT_RATE_LIMIT_RULES = {
    "public_read": (120, 40),
    "auth": (20, 10),
    "payment": (30, 10),
    "webhook": (120, 60),
    "admin": (60, 20),
    "upload": (20, 5),
    "default": (180, 60),
}

JSON_WEBHOOK_PATHS = {
    "/api/telegram/webhook",
    "/api/vk/callback",
    "/api/payments/yookassa/webhook",
    "/api/payments/telegram-webhook",
}

FORM_WEBHOOK_PATHS = {
    "/api/payments/tegro/webhook",
}

UPLOAD_PATH_PREFIXES = (
    "/api/bets/with-coupon",
    "/api/admin/announcements",
    "/api/admin/forecast-broadcast",
    "/api/admin/forecast-requests/bulk-send",
)


@dataclass(frozen=True)
class RateLimitRule:
    limit: int
    burst: int
    window_seconds: int

    @property
    def threshold(self) -> int:
        return max(1, self.limit + self.burst)


@dataclass(frozen=True)
class RateLimitDecision:
    group: str
    limit: int
    remaining: int
    retry_after: int
    exceeded: bool
    blocked: bool
    subjects: tuple[str, ...]


def _positive_int(value: object, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def parse_rate_limit_rules(raw_rules: str, window_seconds: int) -> dict[str, RateLimitRule]:
    rules = {
        group: RateLimitRule(limit, burst, window_seconds)
        for group, (limit, burst) in DEFAULT_RATE_LIMIT_RULES.items()
    }
    for chunk in (raw_rules or "").replace(";", ",").split(","):
        part = chunk.strip()
        if not part or "=" not in part:
            continue
        group, raw_value = [item.strip() for item in part.split("=", 1)]
        if group not in RATE_LIMIT_GROUPS:
            continue
        value_parts = raw_value.split(":", 1)
        limit = _positive_int(value_parts[0], rules[group].limit)
        burst = _positive_int(value_parts[1], rules[group].burst) if len(value_parts) > 1 else rules[group].burst
        rules[group] = RateLimitRule(limit, burst, window_seconds)
    return rules


def classify_rate_limit_group(path: str, method: str = "GET") -> str:
    clean_path = path.split("?", 1)[0]
    method = method.upper()

    if not clean_path.startswith("/api/"):
        return "default"

    if any(clean_path.startswith(prefix) for prefix in UPLOAD_PATH_PREFIXES):
        return "upload"

    if clean_path in JSON_WEBHOOK_PATHS or clean_path in FORM_WEBHOOK_PATHS:
        return "webhook"

    if clean_path.startswith("/api/auth/"):
        return "auth"

    if clean_path.startswith("/api/payments/"):
        payment_paths = (
            "/api/payments/invoice",
            "/api/payments/yookassa/create",
            "/api/payments/tegro/create",
            "/api/payments/promo/validate",
        )
        if any(clean_path.startswith(prefix) for prefix in payment_paths):
            return "payment"

    if clean_path.startswith("/api/admin/"):
        return "admin"

    if method == "GET" and (
        clean_path in {"/api/bookmakers", "/api/stats/global", "/api/health"}
        or clean_path.startswith("/api/bets/feed")
        or clean_path.startswith("/api/plans")
    ):
        return "public_read"

    return "default"


def _trusted_proxy_networks() -> tuple[ipaddress._BaseNetwork, ...]:
    networks = []
    for raw_item in (settings.SECURITY_TRUSTED_PROXY_CIDRS or "").split(","):
        item = raw_item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            logger.warning("[Security] ignoring invalid trusted proxy CIDR: %s", item)
    return tuple(networks)


TRUSTED_PROXY_NETWORKS = _trusted_proxy_networks()


def _is_trusted_proxy(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in network for network in TRUSTED_PROXY_NETWORKS)


def _client_ip(request: Request) -> str:
    direct_host = request.client.host if request.client and request.client.host else ""
    if direct_host and _is_trusted_proxy(direct_host):
        x_real_ip = (request.headers.get("x-real-ip") or "").split(",", 1)[0].strip()
        if x_real_ip:
            return x_real_ip
        forwarded = (request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
        if forwarded:
            return forwarded
    return direct_host or "unknown"


def _user_subject_from_authorization(request: Request) -> Optional[str]:
    authorization = request.headers.get("authorization") or ""
    token = authorization[7:] if authorization.startswith("Bearer ") else authorization
    if not token:
        return None
    try:
        payload = verify_access_token(token)
    except Exception:
        return None
    if not payload or "sub" not in payload:
        return None
    return f"user:{payload['sub']}"


def request_subjects(request: Request) -> tuple[str, ...]:
    subjects = [f"ip:{_client_ip(request)}"]
    user_subject = _user_subject_from_authorization(request)
    if user_subject:
        subjects.append(user_subject)
    return tuple(subjects)


class SecurityRateLimiter:
    def __init__(
        self,
        *,
        mode: str,
        rules: Mapping[str, RateLimitRule],
        max_tracked_keys: int,
        cleanup_interval_seconds: int,
    ) -> None:
        normalized_mode = (mode or "monitor").strip().lower()
        self.mode = normalized_mode if normalized_mode in {"off", "monitor", "enforce"} else "monitor"
        self.rules = dict(rules)
        self.max_tracked_keys = max(100, int(max_tracked_keys or 10000))
        self.cleanup_interval_seconds = max(10, int(cleanup_interval_seconds or 60))
        self.started_at = time.time()
        self._events: dict[str, Deque[float]] = {}
        self._lock = RLock()
        self._last_cleanup = self.started_at
        self._group_totals: Counter[str] = Counter()
        self._group_exceeded: Counter[str] = Counter()
        self._group_blocked: Counter[str] = Counter()
        self._offender_counts: Counter[str] = Counter()

    @classmethod
    def from_settings(cls) -> "SecurityRateLimiter":
        window_seconds = _positive_int(settings.SECURITY_RATE_LIMIT_WINDOW_SECONDS, 60)
        return cls(
            mode=settings.SECURITY_RATE_LIMIT_MODE,
            rules=parse_rate_limit_rules(settings.SECURITY_RATE_LIMIT_GROUP_RULES, window_seconds),
            max_tracked_keys=settings.SECURITY_RATE_LIMIT_MAX_TRACKED_KEYS,
            cleanup_interval_seconds=settings.SECURITY_RATE_LIMIT_CLEANUP_INTERVAL_SECONDS,
        )

    def _cleanup(self, now: float, force: bool = False) -> None:
        if not force and now - self._last_cleanup < self.cleanup_interval_seconds:
            return
        oldest_window = max((rule.window_seconds for rule in self.rules.values()), default=60)
        cutoff = now - oldest_window
        empty_keys = []
        for key, events in self._events.items():
            while events and events[0] <= cutoff:
                events.popleft()
            if not events:
                empty_keys.append(key)
        for key in empty_keys:
            self._events.pop(key, None)
        self._last_cleanup = now

    def _ensure_capacity(self, key: str, now: float) -> None:
        if key in self._events or len(self._events) < self.max_tracked_keys:
            return
        self._cleanup(now, force=True)
        if len(self._events) < self.max_tracked_keys:
            return
        try:
            self._events.pop(next(iter(self._events)))
        except StopIteration:
            pass

    def check(self, *, group: str, subjects: Iterable[str], now: Optional[float] = None) -> RateLimitDecision:
        group = group if group in self.rules else "default"
        rule = self.rules[group]
        now = now if now is not None else time.time()
        subject_tuple = tuple(subjects) or ("ip:unknown",)

        if self.mode == "off":
            return RateLimitDecision(
                group=group,
                limit=rule.threshold,
                remaining=rule.threshold,
                retry_after=0,
                exceeded=False,
                blocked=False,
                subjects=subject_tuple,
            )

        cutoff = now - rule.window_seconds
        exceeded = False
        blocked = False
        min_remaining = rule.threshold
        retry_after = 0

        with self._lock:
            self._cleanup(now)
            self._group_totals[group] += 1
            for subject in subject_tuple:
                key = f"{group}:{subject}"
                self._ensure_capacity(key, now)
                events = self._events.setdefault(key, deque())
                while events and events[0] <= cutoff:
                    events.popleft()
                events.append(now)
                count = len(events)
                remaining = max(0, rule.threshold - count)
                min_remaining = min(min_remaining, remaining)
                if count > rule.threshold:
                    exceeded = True
                    oldest = events[0] if events else now
                    retry_after = max(retry_after, int(max(1, rule.window_seconds - (now - oldest))))
                    self._offender_counts[subject] += 1

            if exceeded:
                self._group_exceeded[group] += 1
                blocked = self.mode == "enforce"
                if blocked:
                    self._group_blocked[group] += 1

        return RateLimitDecision(
            group=group,
            limit=rule.threshold,
            remaining=min_remaining,
            retry_after=retry_after,
            exceeded=exceeded,
            blocked=blocked,
            subjects=subject_tuple,
        )

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "mode": self.mode,
                "uptime_seconds": int(time.time() - self.started_at),
                "tracked_keys": len(self._events),
                "max_tracked_keys": self.max_tracked_keys,
                "rules": {
                    group: {
                        "limit": rule.limit,
                        "burst": rule.burst,
                        "window_seconds": rule.window_seconds,
                        "threshold": rule.threshold,
                    }
                    for group, rule in sorted(self.rules.items())
                },
                "groups": {
                    group: {
                        "total": int(self._group_totals.get(group, 0)),
                        "exceeded": int(self._group_exceeded.get(group, 0)),
                        "blocked": int(self._group_blocked.get(group, 0)),
                    }
                    for group in sorted(self.rules)
                },
                "top_offenders": [
                    {"subject": subject, "exceeded": int(count)}
                    for subject, count in self._offender_counts.most_common(10)
                ],
            }


def _content_length(request: Request) -> Optional[int]:
    raw_value = request.headers.get("content-length")
    if raw_value is None:
        return None
    try:
        value = int(raw_value)
    except ValueError:
        return -1
    return value


def _is_json_content_type(value: str) -> bool:
    return value.split(";", 1)[0].strip().lower() == "application/json"


def _rate_limit_headers(decision: RateLimitDecision) -> dict[str, str]:
    headers = {
        "X-RateLimit-Group": decision.group,
        "X-RateLimit-Limit": str(decision.limit),
        "X-RateLimit-Remaining": str(decision.remaining),
    }
    if decision.retry_after:
        headers["Retry-After"] = str(decision.retry_after)
    return headers


def _response_headers_for_decision(decision: RateLimitDecision) -> dict[str, str]:
    headers = _rate_limit_headers(decision)
    if decision.exceeded:
        headers["X-RateLimit-Monitor"] = "exceeded"
    return headers


class SecurityRateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limiter: SecurityRateLimiter) -> None:
        super().__init__(app)
        self.limiter = limiter

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method.upper()
        if not path.startswith("/api/") or method == "OPTIONS":
            return await call_next(request)

        group = classify_rate_limit_group(path, method)
        length = _content_length(request)
        if length == -1:
            return JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)
        if method in {"POST", "PUT", "PATCH"} and length is None:
            return JSONResponse({"detail": "Content-Length required"}, status_code=411)

        body_limit = (
            settings.SECURITY_UPLOAD_BODY_MAX_BYTES
            if group == "upload"
            else settings.SECURITY_JSON_BODY_MAX_BYTES
        )
        if length is not None and length > body_limit:
            return JSONResponse({"detail": "Request body too large"}, status_code=413)

        if method in {"POST", "PUT", "PATCH"} and path in JSON_WEBHOOK_PATHS:
            content_type = request.headers.get("content-type") or ""
            if not _is_json_content_type(content_type):
                return JSONResponse({"detail": "Unsupported Media Type"}, status_code=415)

        subjects = request_subjects(request)
        decision = self.limiter.check(group=group, subjects=subjects)

        if decision.exceeded:
            logger.warning(
                "[Security] rate_limit_exceeded group=%s mode=%s path=%s method=%s subjects=%s blocked=%s",
                decision.group,
                self.limiter.mode,
                path,
                method,
                ",".join(decision.subjects),
                decision.blocked,
            )

        if decision.blocked:
            return JSONResponse(
                {"detail": "Too many requests"},
                status_code=429,
                headers=_response_headers_for_decision(decision),
            )

        response = await call_next(request)
        for name, value in _response_headers_for_decision(decision).items():
            response.headers.setdefault(name, value)
        return response


security_rate_limiter = SecurityRateLimiter.from_settings()


def get_security_rate_limit_metrics() -> dict:
    return security_rate_limiter.snapshot()

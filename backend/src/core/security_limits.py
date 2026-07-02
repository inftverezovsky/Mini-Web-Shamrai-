from __future__ import annotations

import logging
import hashlib
import ipaddress
import re
import time
import uuid
from collections import Counter, deque
from dataclasses import dataclass
from threading import RLock
from typing import Any, Deque, Iterable, Mapping, Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.core.config import settings
from src.core.redis_cache import get_redis_client
from src.core.security import verify_access_token

logger = logging.getLogger("uvicorn")
AUTH_COOKIE_NAME = "shamrai_access_token"
IDENTITY_DEVICE_HEADER = "x-shamrai-device-id"
IDENTITY_DEVICE_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")

RATE_LIMIT_GROUPS = {
    "public_read",
    "auth",
    "auth_poll",
    "payment",
    "webhook",
    "admin",
    "upload",
    "default",
}

DEFAULT_RATE_LIMIT_RULES = {
    "public_read": (120, 40),
    "auth": (40, 20),
    "auth_poll": (90, 30),
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
    "/api/chat/conversations/support/attachments",
)

REDIS_RATE_LIMIT_SCRIPT = """
local key = KEYS[1]
local cutoff = tonumber(ARGV[1])
local now = tonumber(ARGV[2])
local member = ARGV[3]
local ttl = tonumber(ARGV[5])

redis.call("ZREMRANGEBYSCORE", key, "-inf", cutoff)
redis.call("ZADD", key, now, member)
local count = redis.call("ZCARD", key)
local oldest = now
local oldest_items = redis.call("ZRANGE", key, 0, 0, "WITHSCORES")
if oldest_items[2] ~= nil then
    oldest = tonumber(oldest_items[2])
end
redis.call("EXPIRE", key, ttl)
return {count, tostring(oldest)}
"""


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

    if clean_path.startswith("/api/chat/admin/conversations/") and clean_path.endswith("/attachments"):
        return "upload"

    if clean_path in JSON_WEBHOOK_PATHS or clean_path in FORM_WEBHOOK_PATHS:
        return "webhook"

    if clean_path.startswith("/api/auth/"):
        if method == "GET" and clean_path.startswith("/api/auth/telegram/bot-session/"):
            return "auth_poll"
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
        token = (getattr(request, "cookies", {}) or {}).get(AUTH_COOKIE_NAME) or ""
    if not token:
        return None
    try:
        payload = verify_access_token(token)
    except Exception:
        return None
    if not payload or "sub" not in payload:
        return None
    return f"user:{payload['sub']}"


def _header_value(request: Request, name: str) -> str:
    headers = getattr(request, "headers", {}) or {}
    value = headers.get(name) or headers.get(name.lower()) or headers.get(name.upper())
    return str(value or "").strip()


def _identity_device_subject(request: Request) -> Optional[str]:
    raw_value = _header_value(request, IDENTITY_DEVICE_HEADER)
    if not raw_value or not IDENTITY_DEVICE_RE.match(raw_value):
        return None
    digest = hashlib.sha256(raw_value.encode("utf-8")).hexdigest()[:24]
    return f"device:{digest}"


def request_subjects(
    request: Request,
    *,
    prefer_authenticated_user: bool = False,
    prefer_identity_device: bool = False,
) -> tuple[str, ...]:
    ip_subject = f"ip:{_client_ip(request)}"
    user_subject = _user_subject_from_authorization(request)
    if prefer_authenticated_user and user_subject:
        return (user_subject,)
    if prefer_identity_device:
        identity_subject = _identity_device_subject(request)
        if identity_subject:
            return (identity_subject,)

    subjects = [ip_subject]
    if user_subject:
        subjects.append(user_subject)
    return tuple(subjects)


def _prefer_authenticated_rate_limit_subject(group: str) -> bool:
    return group in {"default", "payment", "admin", "upload"}


def _prefer_identity_device_rate_limit_subject(group: str) -> bool:
    return group in {"auth", "auth_poll"}


class SecurityRateLimiter:
    _REDIS_FAILURE_BACKOFF_SECONDS = 5.0

    def __init__(
        self,
        *,
        mode: str,
        rules: Mapping[str, RateLimitRule],
        max_tracked_keys: int,
        cleanup_interval_seconds: int,
        storage: str = "memory",
        redis_prefix: str = "security:rate-limit:v1",
        redis_client: Any | None = None,
        redis_client_factory: Any | None = None,
    ) -> None:
        normalized_mode = (mode or "monitor").strip().lower()
        self.mode = normalized_mode if normalized_mode in {"off", "monitor", "enforce"} else "monitor"
        normalized_storage = (storage or "memory").strip().lower()
        self.storage = normalized_storage if normalized_storage in {"auto", "memory", "redis"} else "auto"
        self.redis_prefix = self._normalize_redis_prefix(redis_prefix)
        self.rules = dict(rules)
        self.max_tracked_keys = max(100, int(max_tracked_keys or 10000))
        self.cleanup_interval_seconds = max(10, int(cleanup_interval_seconds or 60))
        self.started_at = time.time()
        self._redis_client = redis_client
        self._redis_client_factory = redis_client_factory
        self._redis_suspended_until = 0.0
        self._last_redis_error_log = 0.0
        self._redis_failures = 0
        self._redis_successes = 0
        self._fallback_checks = 0
        self._last_storage = "memory"
        self._events: dict[str, Deque[float]] = {}
        self._lock = RLock()
        self._last_cleanup = self.started_at
        self._group_totals: Counter[str] = Counter()
        self._group_exceeded: Counter[str] = Counter()
        self._group_blocked: Counter[str] = Counter()
        self._offender_counts: Counter[str] = Counter()

    @staticmethod
    def _normalize_redis_prefix(raw_prefix: str) -> str:
        prefix = (raw_prefix or "security:rate-limit:v1").strip().strip(":")
        return prefix or "security:rate-limit:v1"

    @classmethod
    def from_settings(cls) -> "SecurityRateLimiter":
        window_seconds = _positive_int(settings.SECURITY_RATE_LIMIT_WINDOW_SECONDS, 60)
        return cls(
            mode=settings.SECURITY_RATE_LIMIT_MODE,
            rules=parse_rate_limit_rules(settings.SECURITY_RATE_LIMIT_GROUP_RULES, window_seconds),
            max_tracked_keys=settings.SECURITY_RATE_LIMIT_MAX_TRACKED_KEYS,
            cleanup_interval_seconds=settings.SECURITY_RATE_LIMIT_CLEANUP_INTERVAL_SECONDS,
            storage=settings.SECURITY_RATE_LIMIT_STORAGE,
            redis_prefix=settings.SECURITY_RATE_LIMIT_REDIS_PREFIX,
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

    def _record_metrics_locked(self, *, group: str, exceeded_subjects: Iterable[str], blocked: bool) -> None:
        exceeded_subject_tuple = tuple(exceeded_subjects)
        self._group_totals[group] += 1
        if exceeded_subject_tuple:
            self._group_exceeded[group] += 1
            if blocked:
                self._group_blocked[group] += 1
            for subject in exceeded_subject_tuple:
                self._offender_counts[subject] += 1

    def _check_memory(
        self,
        *,
        group: str,
        rule: RateLimitRule,
        subjects: tuple[str, ...],
        now: float,
    ) -> RateLimitDecision:
        cutoff = now - rule.window_seconds
        exceeded_subjects: list[str] = []
        min_remaining = rule.threshold
        retry_after = 0

        with self._lock:
            self._cleanup(now)
            for subject in subjects:
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
                    exceeded_subjects.append(subject)
                    oldest = events[0] if events else now
                    retry_after = max(retry_after, int(max(1, rule.window_seconds - (now - oldest))))

            blocked = bool(exceeded_subjects) and self.mode == "enforce"
            self._record_metrics_locked(group=group, exceeded_subjects=exceeded_subjects, blocked=blocked)
            self._last_storage = "memory"

        return RateLimitDecision(
            group=group,
            limit=rule.threshold,
            remaining=min_remaining,
            retry_after=retry_after,
            exceeded=bool(exceeded_subjects),
            blocked=blocked,
            subjects=subjects,
        )

    def _redis_configured(self) -> bool:
        if self.storage == "memory":
            return False
        if self._redis_client is not None or self._redis_client_factory is not None:
            return True
        return bool(settings.REDIS_CACHE_ENABLED and settings.REDIS_URL.strip())

    def _get_redis_client(self) -> Any | None:
        if self._redis_client is not None:
            return self._redis_client
        if self._redis_client_factory is not None:
            return self._redis_client_factory()
        return get_redis_client()

    def _redis_client_for_check(self) -> Any | None:
        if self.storage == "memory" or time.monotonic() < self._redis_suspended_until:
            return None
        return self._get_redis_client()

    def _redis_key(self, *, group: str, subject: str) -> str:
        return f"{self.redis_prefix}:{group}:{subject}"

    async def _redis_hit(self, *, client: Any, key: str, rule: RateLimitRule, now: float) -> tuple[int, float]:
        cutoff = now - rule.window_seconds
        member = f"{now:.6f}:{uuid.uuid4().hex}"
        ttl = max(1, rule.window_seconds + self.cleanup_interval_seconds)
        result = await client.eval(
            REDIS_RATE_LIMIT_SCRIPT,
            1,
            key,
            cutoff,
            now,
            member,
            rule.threshold,
            ttl,
        )
        count = int(result[0])
        oldest = result[1]
        if isinstance(oldest, bytes):
            oldest = oldest.decode("utf-8")
        try:
            oldest_score = float(oldest)
        except (TypeError, ValueError):
            oldest_score = now
        return count, oldest_score

    async def _check_redis(
        self,
        *,
        client: Any,
        group: str,
        rule: RateLimitRule,
        subjects: tuple[str, ...],
        now: float,
    ) -> RateLimitDecision:
        exceeded_subjects: list[str] = []
        min_remaining = rule.threshold
        retry_after = 0

        for subject in subjects:
            count, oldest = await self._redis_hit(
                client=client,
                key=self._redis_key(group=group, subject=subject),
                rule=rule,
                now=now,
            )
            remaining = max(0, rule.threshold - count)
            min_remaining = min(min_remaining, remaining)
            if count > rule.threshold:
                exceeded_subjects.append(subject)
                retry_after = max(retry_after, int(max(1, rule.window_seconds - (now - oldest))))

        blocked = bool(exceeded_subjects) and self.mode == "enforce"
        with self._lock:
            self._record_metrics_locked(group=group, exceeded_subjects=exceeded_subjects, blocked=blocked)
            self._redis_successes += 1
            self._last_storage = "redis"

        return RateLimitDecision(
            group=group,
            limit=rule.threshold,
            remaining=min_remaining,
            retry_after=retry_after,
            exceeded=bool(exceeded_subjects),
            blocked=blocked,
            subjects=subjects,
        )

    def _suspend_redis_temporarily(self, exc: Exception) -> None:
        now = time.monotonic()
        with self._lock:
            self._redis_failures += 1
            self._redis_suspended_until = now + self._REDIS_FAILURE_BACKOFF_SECONDS
            should_log = now - self._last_redis_error_log >= self._REDIS_FAILURE_BACKOFF_SECONDS
            if should_log:
                self._last_redis_error_log = now
        if should_log:
            logger.warning(
                "[Security] Redis rate limiter unavailable; using local fallback for %.1fs: %s",
                self._REDIS_FAILURE_BACKOFF_SECONDS,
                type(exc).__name__,
            )

    async def check(self, *, group: str, subjects: Iterable[str], now: Optional[float] = None) -> RateLimitDecision:
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

        client = self._redis_client_for_check()
        if client is not None:
            try:
                return await self._check_redis(
                    client=client,
                    group=group,
                    rule=rule,
                    subjects=subject_tuple,
                    now=now,
                )
            except Exception as exc:
                self._suspend_redis_temporarily(exc)

        if self._redis_configured():
            with self._lock:
                self._fallback_checks += 1

        return self._check_memory(group=group, rule=rule, subjects=subject_tuple, now=now)

    def _redis_fallback_active(self) -> bool:
        return self._redis_configured() and time.monotonic() < self._redis_suspended_until

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "mode": self.mode,
                "storage": self._last_storage,
                "configured_storage": self.storage,
                "redis_prefix": self.redis_prefix,
                "redis_configured": self._redis_configured(),
                "redis_available": self._last_storage == "redis" and not self._redis_fallback_active(),
                "redis_fallback_active": self._redis_fallback_active(),
                "redis_failures": int(self._redis_failures),
                "redis_successes": int(self._redis_successes),
                "fallback_checks": int(self._fallback_checks),
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


def _security_json_response(detail: str, status_code: int, headers: Optional[dict[str, str]] = None) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status_code, headers=headers)


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
            return _security_json_response("Некорректный размер запроса.", 400)
        if method in {"POST", "PUT", "PATCH"} and length is None:
            return _security_json_response("Не удалось проверить размер запроса. Повторите действие.", 411)

        body_limit = (
            settings.SECURITY_UPLOAD_BODY_MAX_BYTES
            if group == "upload"
            else settings.SECURITY_JSON_BODY_MAX_BYTES
        )
        if length is not None and length > body_limit:
            return _security_json_response("Запрос слишком большой. Уменьшите файл или данные и попробуйте еще раз.", 413)

        if method in {"POST", "PUT", "PATCH"} and path in JSON_WEBHOOK_PATHS:
            content_type = request.headers.get("content-type") or ""
            if not _is_json_content_type(content_type):
                return _security_json_response("Неподдерживаемый формат запроса.", 415)

        subjects = request_subjects(
            request,
            prefer_authenticated_user=_prefer_authenticated_rate_limit_subject(group),
            prefer_identity_device=_prefer_identity_device_rate_limit_subject(group),
        )
        decision = await self.limiter.check(group=group, subjects=subjects)

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
            return _security_json_response(
                "Слишком много действий подряд. Подождите немного и повторите попытку.",
                429,
                headers=_response_headers_for_decision(decision),
            )

        response = await call_next(request)
        for name, value in _response_headers_for_decision(decision).items():
            response.headers.setdefault(name, value)
        return response


security_rate_limiter = SecurityRateLimiter.from_settings()


def get_security_rate_limit_metrics() -> dict:
    return security_rate_limiter.snapshot()

import ipaddress
from urllib.parse import urlparse


WEB_PUSH_ALLOWED_HOSTS = {
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "web.push.apple.com",
}
WEB_PUSH_ALLOWED_SUFFIXES = (
    ".push.services.mozilla.com",
    ".push.apple.com",
    ".notify.windows.com",
)
WEB_PUSH_RESERVED_SUFFIXES = (
    ".localhost",
    ".local",
    ".invalid",
    ".test",
    ".example",
)


def _is_disallowed_ip_literal(hostname: str) -> bool:
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False

    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def _is_allowed_push_service_hostname(hostname: str) -> bool:
    return hostname in WEB_PUSH_ALLOWED_HOSTS or any(
        hostname.endswith(suffix) for suffix in WEB_PUSH_ALLOWED_SUFFIXES
    )


def validate_public_web_push_endpoint(value: str) -> str:
    endpoint = value.strip()
    parsed = urlparse(endpoint)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("Web Push endpoint must be a public HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError("Web Push endpoint credentials are not allowed")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("Web Push endpoint port is invalid") from exc

    hostname = parsed.hostname.strip("[]").rstrip(".").lower()
    if (
        hostname in {"localhost", "local"}
        or hostname.endswith(WEB_PUSH_RESERVED_SUFFIXES)
        or _is_disallowed_ip_literal(hostname)
    ):
        raise ValueError("Web Push endpoint host must be public")

    if not _is_allowed_push_service_hostname(hostname):
        raise ValueError("Web Push endpoint host is not an allowed push service")

    return endpoint

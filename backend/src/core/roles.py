VALID_ROLES = ("user", "moderator", "admin", "owner")
STAFF_ROLES = ("moderator", "admin", "owner")
ADMIN_ROLES = ("admin", "owner")
ROLE_ORDER = {
    "user": 0,
    "moderator": 1,
    "admin": 2,
    "owner": 3,
}
ROLE_LABELS = {
    "user": "Пользователь",
    "moderator": "Модератор",
    "admin": "Администратор",
    "owner": "Владелец",
}


def normalize_role(role: str | None) -> str:
    return (role or "user").strip().lower()


def is_valid_role(role: str | None) -> bool:
    return normalize_role(role) in VALID_ROLES


def is_staff_role(role: str | None) -> bool:
    return normalize_role(role) in STAFF_ROLES


def is_admin_role(role: str | None) -> bool:
    return normalize_role(role) in ADMIN_ROLES


def is_owner_role(role: str | None) -> bool:
    return normalize_role(role) == "owner"


def role_level(role: str | None) -> int:
    return ROLE_ORDER.get(normalize_role(role), 0)

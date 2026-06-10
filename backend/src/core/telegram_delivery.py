from typing import Any


def is_personal_telegram_user_id(value: Any) -> bool:
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def user_can_receive_personal_telegram(user: Any) -> bool:
    return (
        is_personal_telegram_user_id(getattr(user, "telegram_id", None))
        and not bool(getattr(user, "is_web_only", False))
    )

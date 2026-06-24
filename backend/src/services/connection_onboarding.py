from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.models.models import PersonalSignal, User
from src.services.signals import deliver_personal_signal

CONNECTION_SETUP_GUIDE_SIGNAL_TYPE = "connection_setup_guide"
CONNECTION_SETUP_COMPLETE_SIGNAL_TYPE = "connection_setup_complete"
CONNECTION_SETUP_SIGNAL_TYPES = (
    CONNECTION_SETUP_GUIDE_SIGNAL_TYPE,
    CONNECTION_SETUP_COMPLETE_SIGNAL_TYPE,
)


def has_telegram_identity(user: User) -> bool:
    return bool(getattr(user, "telegram_id", 0) and int(user.telegram_id) > 0)


def has_vk_identity(user: User) -> bool:
    return bool(getattr(user, "vk_user_id", None))


def has_vk_message_delivery(user: User) -> bool:
    return bool(has_vk_identity(user) and getattr(user, "vk_messages_allowed", False))


def has_web_push_delivery(user: User) -> bool:
    subscription = getattr(user, "web_push_subscription", None)
    return isinstance(subscription, dict) and bool(subscription.get("endpoint"))


def connection_checklist(user: User) -> dict[str, bool]:
    checklist = {
        "telegram_linked": has_telegram_identity(user),
        "vk_linked": has_vk_identity(user),
        "vk_messages_allowed": has_vk_message_delivery(user),
        "web_push_enabled": has_web_push_delivery(user),
    }
    checklist["complete"] = all(checklist.values())
    return checklist


def _frontend_setup_url(setup: str, fragment: str) -> str:
    base_url = settings.FRONTEND_BASE_URL.strip().rstrip("/") or "/app"
    separator = "&" if "?" in base_url else "?"
    query = urlencode({"open": "profile", "setup": setup})
    return f"{base_url}{separator}{query}#{fragment}"


def setup_actions_for_user(user: User) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    if not has_telegram_identity(user):
        actions.append({
            "id": "connect-telegram",
            "label": "Подключить Telegram",
            "url": _frontend_setup_url("telegram", "connect-telegram"),
        })
    if not has_vk_identity(user):
        actions.append({
            "id": "connect-vk",
            "label": "Подключить VK ID",
            "url": _frontend_setup_url("vk", "connect-vk"),
        })
    if has_vk_identity(user) and not has_vk_message_delivery(user):
        actions.append({
            "id": "allow-vk-messages",
            "label": "Разрешить сообщения VK",
            "url": _frontend_setup_url("vk-messages", "connect-vk"),
        })
    if not has_web_push_delivery(user):
        actions.append({
            "id": "enable-web-push",
            "label": "Включить Web Push",
            "url": _frontend_setup_url("notifications", "web-push"),
        })
    if not actions:
        actions.append({
            "id": "open-profile",
            "label": "Открыть профиль",
            "url": _frontend_setup_url("identity", "connect-identity"),
        })
    return actions


def build_connection_setup_guide_text(user: User) -> str:
    checklist = connection_checklist(user)
    missing_lines: list[str] = []
    if not checklist["telegram_linked"]:
        missing_lines.append("Подключите Telegram: он нужен для входа через бота и восстановления доступа.")
    if not checklist["vk_linked"]:
        missing_lines.append("Подключите VK ID: это резервный вход и второй канал связи.")
    elif not checklist["vk_messages_allowed"]:
        missing_lines.append("Разрешите сообщения VK от сообщества Shamrai, иначе VK не сможет доставлять личные уведомления.")
    if not checklist["web_push_enabled"]:
        missing_lines.append("Включите Web Push: на телефоне добавьте Shamrai на главный экран, откройте иконку и разрешите уведомления.")

    if not missing_lines:
        missing_lines.append("Проверьте профиль: основные каналы уже выглядят подключенными.")

    numbered = "\n".join(f"{index}. {line}" for index, line in enumerate(missing_lines, start=1))
    return (
        "Привет! Я личный бот Shamrai. Давайте настроим связь так, чтобы сигналы и доступ не терялись.\n\n"
        f"{numbered}\n\n"
        "Ниже оставил быстрые кнопки. Нажимайте по очереди, а после настройки вернитесь в чат."
    )


def build_connection_setup_complete_text() -> str:
    return (
        "Все подключено. Telegram, VK сообщения и Web Push активны.\n\n"
        "Молодец - теперь мы с тобой всегда на связи: сигналы, восстановление доступа и важные уведомления дойдут до нужного канала."
    )


async def _existing_connection_signal_types(db: AsyncSession, user: User) -> set[str]:
    result = await db.execute(
        select(PersonalSignal.type).filter(
            PersonalSignal.user_id == user.telegram_id,
            PersonalSignal.type.in_(CONNECTION_SETUP_SIGNAL_TYPES),
        )
    )
    return {str(signal_type) for signal_type in result.scalars().all()}


async def sync_connection_onboarding(db: AsyncSession, user: User) -> dict[str, Any]:
    checklist = connection_checklist(user)
    existing_types = await _existing_connection_signal_types(db, user)
    created_signal_types: list[str] = []

    if checklist["complete"]:
        if CONNECTION_SETUP_COMPLETE_SIGNAL_TYPE not in existing_types:
            await deliver_personal_signal(
                db,
                user=user,
                text=build_connection_setup_complete_text(),
                signal_type=CONNECTION_SETUP_COMPLETE_SIGNAL_TYPE,
                data={
                    "kind": "connection_setup",
                    "checklist": checklist,
                    "setup_actions": setup_actions_for_user(user),
                },
                send_telegram=False,
                send_web_push=True,
            )
            created_signal_types.append(CONNECTION_SETUP_COMPLETE_SIGNAL_TYPE)
        return {
            "status": "ok",
            "checklist": checklist,
            "created_signal_types": created_signal_types,
        }

    if CONNECTION_SETUP_GUIDE_SIGNAL_TYPE not in existing_types:
        await deliver_personal_signal(
            db,
            user=user,
            text=build_connection_setup_guide_text(user),
            signal_type=CONNECTION_SETUP_GUIDE_SIGNAL_TYPE,
            data={
                "kind": "connection_setup",
                "checklist": checklist,
                "setup_actions": setup_actions_for_user(user),
            },
            send_telegram=False,
            send_web_push=False,
        )
        created_signal_types.append(CONNECTION_SETUP_GUIDE_SIGNAL_TYPE)

    return {
        "status": "ok",
        "checklist": checklist,
        "created_signal_types": created_signal_types,
    }

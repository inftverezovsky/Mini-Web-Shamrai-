import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from src.models.models import MessageTemplate


TEMPLATE_TELEGRAM_WELCOME = "telegram_welcome"
TEMPLATE_ANNOUNCEMENT = "announcement"
TEMPLATE_PAID_SET_TEASER = "paid_set_teaser"
TEMPLATE_FORECAST_TEASER = "forecast_teaser"
TEMPLATE_FORECAST_FULL = "forecast_full"
TEMPLATE_ODDS_DROP = "odds_drop"
TEMPLATE_BET_WIN = "bet_win"
TEMPLATE_BET_LOSS = "bet_loss"
TEMPLATE_BET_LOSS_SUPERCOMPENSATION = "bet_loss_supercompensation"
TEMPLATE_BET_REFUND = "bet_refund"
TEMPLATE_LIVE_SIGNAL = "live_signal"

PLACEHOLDER_RE = re.compile(r"{{\s*([a-zA-Z0-9_]+)\s*}}")


@dataclass(frozen=True)
class TemplateVariable:
    key: str
    label: str
    example: str = ""


@dataclass(frozen=True)
class TemplateDefinition:
    key: str
    title: str
    description: str
    body: str
    variables: tuple[TemplateVariable, ...]


COMMON_CONTACT_VARIABLE = TemplateVariable("contact_footer", "Контактный блок", "Если есть вопросы...")
COMMON_EVENT_VARIABLES = (
    TemplateVariable("event_name", "Матч", "Зенит - Спартак"),
    TemplateVariable("outcome", "Исход", "П1"),
    TemplateVariable("coefficient", "Коэффициент", "1.92"),
)


MESSAGE_TEMPLATE_DEFINITIONS: tuple[TemplateDefinition, ...] = (
    TemplateDefinition(
        key=TEMPLATE_TELEGRAM_WELCOME,
        title="Приветствие Telegram",
        description="Что клиент получает в Telegram после команды /start.",
        body=(
            "👋 <b>Привет, {{first_name}}!</b>\n\n"
            "Добро пожаловать в <b>ШАМРАЙ | ОШИБКИ БК</b>.\n\n"
            "📊 Прогнозы, ошибки БК и умные уведомления уже внутри приложения.\n\n"
            "{{contact_footer}}\n\n"
            "👇 Нажмите кнопку ниже, чтобы открыть Shamrai."
        ),
        variables=(
            TemplateVariable("first_name", "Имя клиента", "Алексей"),
            COMMON_CONTACT_VARIABLE,
        ),
    ),
    TemplateDefinition(
        key=TEMPLATE_ANNOUNCEMENT,
        title="Массовый анонс",
        description="Текст рассылки из раздела анонсов. Один шаблон уходит в Telegram, VK и чат сайта.",
        body=(
            "{{emoji}} <b>{{title}}</b>\n\n"
            "{{body}}\n\n"
            "{{bookmaker_line}}\n"
            "{{match_link_line}}\n"
            "{{coefficient_line}}\n\n"
            "{{contact_footer}}"
        ),
        variables=(
            TemplateVariable("emoji", "Иконка типа анонса", "📢"),
            TemplateVariable("title", "Заголовок", "Новый матч"),
            TemplateVariable("body", "Текст анонса", "Есть новый анонс."),
            TemplateVariable("bookmaker_line", "Строка БК", "🏦 БК: Фонбет"),
            TemplateVariable("match_link_line", "Строка ссылки на матч", "🔗 Перейти к матчу"),
            TemplateVariable("coefficient_line", "Строка коэффициента", "📊 Коэффициент: 1.90"),
            COMMON_CONTACT_VARIABLE,
        ),
    ),
    TemplateDefinition(
        key=TEMPLATE_PAID_SET_TEASER,
        title="Анонс платного набора",
        description="Сообщение клиенту: платный набор, коэффициент, стоимость и кнопка заявки.",
        body=(
            "💰 <b>{{title}}</b> | КФ <b>{{coefficient}}</b>\n\n"
            "{{bookmaker_line}}\n\n"
            "{{body}}\n\n"
            "Стоимость: <b>{{price_text}}</b>\n\n"
            "{{contact_footer}}"
        ),
        variables=(
            TemplateVariable("title", "Название набора", "Платный набор"),
            TemplateVariable("coefficient", "Коэффициент набора", "3.90"),
            TemplateVariable("bookmaker_line", "Строка БК", "Фонбет | Пари | Bettery"),
            TemplateVariable("body", "Описание набора", "Реальный КФ не выше 1.9!"),
            TemplateVariable("price_text", "Стоимость", "1 500 ₽"),
            COMMON_CONTACT_VARIABLE,
        ),
    ),
    TemplateDefinition(
        key=TEMPLATE_FORECAST_TEASER,
        title="Закрытый анонс прогноза",
        description="Первое сообщение клиенту: взять или не взять закрытый прогноз.",
        body=(
            "<b>Закрытый анонс прогноза</b>\n\n"
            "БК: {{bookmaker_labels}}\n\n"
            "Коэффициент: <b>{{coefficient}}</b>\n\n"
            "{{fair_coefficient_line}}\n\n"
            "{{teaser_text}}\n\n"
            "{{contact_footer}}"
        ),
        variables=(
            TemplateVariable("bookmaker_labels", "Букмекеры", "Фонбет, БетБум"),
            TemplateVariable("coefficient", "Коэффициент", "1.92"),
            TemplateVariable("fair_coefficient_line", "Строка верного коэффициента", "Верный: 1.74"),
            TemplateVariable("teaser_text", "Короткий текст из формы", "Есть закрытый прогноз под вашу БК."),
            COMMON_CONTACT_VARIABLE,
        ),
    ),
    TemplateDefinition(
        key=TEMPLATE_FORECAST_FULL,
        title="Полный прогноз",
        description="Сообщение с матчем, исходом, коэффициентом, описанием и купоном.",
        body=(
            "Матч: <b>{{event_name}}</b>\n\n"
            "Исход: <b>{{outcome}}</b>\n\n"
            "Коэффициент: <b>{{coefficient}}</b>\n\n"
            "{{bookmaker_line}}\n\n"
            "{{description}}\n\n"
            "{{bookmaker_links_block}}\n\n"
            "{{contact_footer}}"
        ),
        variables=(
            *COMMON_EVENT_VARIABLES,
            TemplateVariable("bookmaker_line", "Строка БК", "БК: Фонбет"),
            TemplateVariable("description", "Описание прогноза", "Короткая аналитика."),
            TemplateVariable("bookmaker_links_block", "Блок ссылок БК", "Фонбет: нажмите кнопку ниже"),
            COMMON_CONTACT_VARIABLE,
        ),
    ),
    TemplateDefinition(
        key=TEMPLATE_ODDS_DROP,
        title="Падение коэффициента",
        description="Уведомление клиентам, которые уже взяли прогноз, что линия упала.",
        body=(
            "🔥 <b>Посмотри, как выгодно взяли наш исход.</b>\n\n"
            "Матч: <b>{{event_name}}</b>\n"
            "Исход: <b>{{outcome}}</b>\n\n"
            "Мы давали кф. <b>{{coefficient}}</b>, а сейчас линия уже упала до <b>{{odds_dropped_to}}</b>.\n\n"
            "Поздравляю с выгодной ставкой, ждём заход 🤝"
        ),
        variables=(
            *COMMON_EVENT_VARIABLES,
            TemplateVariable("odds_dropped_to", "Новый коэффициент", "1.64"),
        ),
    ),
    TemplateDefinition(
        key=TEMPLATE_BET_WIN,
        title="Победа прогноза",
        description="Сообщение клиенту, когда взятый прогноз рассчитан плюсом.",
        body=(
            "🔥 Прогноз Shamrai рассчитан в плюс!\n\n"
            "Матч «{{event_name}}» успешно закрыт победой. 🧠 "
            "Списание купона произведено честно, ваш банк увеличен. Работаем дальше.🤝"
        ),
        variables=(TemplateVariable("event_name", "Матч", "Зенит - Спартак"),),
    ),
    TemplateDefinition(
        key=TEMPLATE_BET_LOSS,
        title="Неудача прогноза",
        description="Обычное сообщение о минусе, если компенсация клиенту не начисляется.",
        body=(
            "Прогноз «{{event_name}}» закрыт минусом.\n\n"
            "Держим дистанцию и работаем дальше."
        ),
        variables=(TemplateVariable("event_name", "Матч", "Зенит - Спартак"),),
    ),
    TemplateDefinition(
        key=TEMPLATE_BET_LOSS_SUPERCOMPENSATION,
        title="Неудача с компенсацией",
        description="Сообщение о минусе, когда клиенту возвращается ставка и добавляется бонус.",
        body=(
            "⚡ Сверхкомпенсация Shamrai активирована.\n\n"
            "Прогноз «{{event_name}}» закрыт минусом, поэтому мы вернули списанную ставку "
            "и начислили +1 бонусную ставку сверху. Баланс абонемента увеличен на 2."
        ),
        variables=(TemplateVariable("event_name", "Матч", "Зенит - Спартак"),),
    ),
    TemplateDefinition(
        key=TEMPLATE_BET_REFUND,
        title="Возврат прогноза",
        description="Сообщение клиенту, когда прогноз рассчитан возвратом.",
        body=(
            "↩️ Прогноз «{{event_name}}» рассчитан возвратом.\n\n"
            "Ставка возвращается по правилам БК."
        ),
        variables=(TemplateVariable("event_name", "Матч", "Зенит - Спартак"),),
    ),
    TemplateDefinition(
        key=TEMPLATE_LIVE_SIGNAL,
        title="Live-сигнал",
        description="Срочное уведомление по live-прогнозу.",
        body=(
            "⚡⚡⚡ SHAMRAI LIVE SIGNAL ALARM ⚡⚡⚡\n\n"
            "Новый срочный Live-прогноз от Shamrai:\n"
            "🏆 {{event_name}}\n"
            "📈 Коэффициент: {{coefficient}}\n"
            "{{brain_score_line}}\n\n"
            "Быстрее заходите в приложение Shamrai Analytics Hub!"
        ),
        variables=(
            TemplateVariable("event_name", "Матч", "Зенит - Спартак"),
            TemplateVariable("coefficient", "Коэффициент", "1.92"),
            TemplateVariable("brain_score_line", "Строка Brain Score", "🧠 Brain Score: 8/10"),
        ),
    ),
)

MESSAGE_TEMPLATE_DEFINITIONS_BY_KEY = {
    definition.key: definition
    for definition in MESSAGE_TEMPLATE_DEFINITIONS
}


def template_variables_payload(variables: Iterable[TemplateVariable]) -> list[dict[str, str]]:
    return [
        {
            "key": variable.key,
            "label": variable.label,
            "example": variable.example,
        }
        for variable in variables
    ]


def message_template_definition_or_404(template_key: str) -> TemplateDefinition:
    definition = MESSAGE_TEMPLATE_DEFINITIONS_BY_KEY.get(template_key)
    if not definition:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Шаблон сообщения не найден",
        )
    return definition


def default_message_template_body(template_key: str) -> str:
    return message_template_definition_or_404(template_key).body


def _clean_rendered_text(value: str) -> str:
    lines = [line.rstrip() for line in value.splitlines()]
    text = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def render_message_template_body(
    body: str,
    variables: dict[str, Any],
    *,
    safe_keys: Optional[set[str]] = None,
) -> str:
    safe_keys = safe_keys or set()

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        value = variables.get(key, "")
        if value is None:
            return ""
        text = str(value)
        if key in safe_keys:
            return text
        return html.escape(text, quote=True)

    return _clean_rendered_text(PLACEHOLDER_RE.sub(replace, str(body or "")))


async def load_message_template_body(db: AsyncSession, template_key: str) -> str:
    definition = message_template_definition_or_404(template_key)
    result = await db.execute(select(MessageTemplate).filter(MessageTemplate.key == template_key))
    template = result.scalars().first()
    if template and str(template.body or "").strip():
        return template.body
    return definition.body


async def render_message_template(
    db: AsyncSession,
    template_key: str,
    variables: dict[str, Any],
    *,
    safe_keys: Optional[set[str]] = None,
) -> str:
    body = await load_message_template_body(db, template_key)
    return render_message_template_body(body, variables, safe_keys=safe_keys)


def message_template_to_response(template: Optional[MessageTemplate], definition: TemplateDefinition) -> dict[str, Any]:
    body = template.body if template else definition.body
    updated_at = template.updated_at if template else None
    created_at = template.created_at if template else None
    return {
        "key": definition.key,
        "title": template.title if template and template.title else definition.title,
        "description": template.description if template and template.description else definition.description,
        "body": body,
        "default_body": definition.body,
        "variables": template_variables_payload(definition.variables),
        "is_custom": bool(template and template.body != definition.body),
        "updated_by": template.updated_by if template else None,
        "created_at": created_at,
        "updated_at": updated_at,
    }


async def list_message_templates(db: AsyncSession) -> list[dict[str, Any]]:
    result = await db.execute(select(MessageTemplate))
    templates_by_key = {
        template.key: template
        for template in result.scalars().all()
    }
    return [
        message_template_to_response(templates_by_key.get(definition.key), definition)
        for definition in MESSAGE_TEMPLATE_DEFINITIONS
    ]


async def upsert_message_template(
    db: AsyncSession,
    *,
    template_key: str,
    body: str,
    actor_id: int,
) -> dict[str, Any]:
    definition = message_template_definition_or_404(template_key)
    clean_body = str(body or "").strip()
    if not clean_body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Текст шаблона не может быть пустым",
        )
    if len(clean_body) > 4000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Шаблон не должен быть длиннее 4000 символов",
        )

    result = await db.execute(select(MessageTemplate).filter(MessageTemplate.key == template_key))
    template = result.scalars().first()
    if not template:
        template = MessageTemplate(
            key=template_key,
            title=definition.title,
            description=definition.description,
            variables=template_variables_payload(definition.variables),
            body=clean_body,
        )
        db.add(template)
    else:
        template.title = definition.title
        template.description = definition.description
        template.variables = template_variables_payload(definition.variables)
        template.body = clean_body
    template.updated_by = actor_id
    template.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return message_template_to_response(template, definition)


async def reset_message_template(
    db: AsyncSession,
    *,
    template_key: str,
    actor_id: int,
) -> dict[str, Any]:
    definition = message_template_definition_or_404(template_key)
    return await upsert_message_template(
        db,
        template_key=template_key,
        body=definition.body,
        actor_id=actor_id,
    )

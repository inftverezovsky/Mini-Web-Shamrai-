from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.schemas.schemas import SubscriptionPlanCreate, SubscriptionPlanUpdate


def _create_payload(**overrides):
    return {
        "name": "Тестовый флетовый тариф",
        "entitlement_type": "flat",
        "target_flats": Decimal("3.00"),
        "price": Decimal("1490.00"),
        "is_active": True,
        **overrides,
    }


def test_active_plan_create_strips_name() -> None:
    plan = SubscriptionPlanCreate(**_create_payload(name="  Тестовый тариф  "))

    assert plan.name == "Тестовый тариф"


@pytest.mark.parametrize("name", ["", "   ", "\t\r\n"])
def test_active_plan_create_rejects_blank_name(name: str) -> None:
    with pytest.raises(ValidationError):
        SubscriptionPlanCreate(**_create_payload(name=name))


def test_active_plan_update_strips_name() -> None:
    update = SubscriptionPlanUpdate(name="  Новое имя  ", is_active=True)

    assert update.name == "Новое имя"


@pytest.mark.parametrize("name", [None, "", "   ", "\t\r\n"])
def test_active_plan_update_rejects_blank_name(name: str | None) -> None:
    with pytest.raises(ValidationError):
        SubscriptionPlanUpdate(name=name, is_active=True)

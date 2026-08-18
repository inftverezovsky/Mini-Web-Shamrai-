from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


def _normalized_items(values: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
    normalized = {
        " ".join(str(key).strip().lower().split()): str(value).strip()
        for key, value in (values or {}).items()
        if str(key).strip() and str(value).strip()
    }
    return tuple(sorted(normalized.items()))


@dataclass(frozen=True)
class EmojiCatalogSnapshot:
    shamrai_id: str = ""
    write_id: str = ""
    bookmaker_items: tuple[tuple[str, str], ...] = ()
    sport_items: tuple[tuple[str, str], ...] = ()
    decor_items: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_values(
        cls,
        *,
        shamrai_id: str = "",
        write_id: str = "",
        bookmakers: Mapping[str, str] | None = None,
        sports: Mapping[str, str] | None = None,
        decor: Mapping[str, str] | None = None,
    ) -> EmojiCatalogSnapshot:
        return cls(
            shamrai_id=str(shamrai_id or "").strip(),
            write_id=str(write_id or "").strip(),
            bookmaker_items=_normalized_items(bookmakers),
            sport_items=_normalized_items(sports),
            decor_items=_normalized_items(decor),
        )

    def mapping(self, scope: str) -> dict[str, str]:
        items = {
            "bookmaker": self.bookmaker_items,
            "sport": self.sport_items,
            "decor": self.decor_items,
        }.get(str(scope or "").strip().lower(), ())
        return dict(items)

    def custom_emoji_id(self, scope: str, key: str | None = None) -> str:
        clean_scope = str(scope or "").strip().lower()
        if clean_scope == "shamrai":
            return self.shamrai_id
        if clean_scope == "write":
            return self.write_id
        clean_key = " ".join(str(key or "").strip().lower().split())
        if not clean_key:
            return ""
        return self.mapping(clean_scope).get(clean_key, "")


_runtime_catalog: EmojiCatalogSnapshot | None = None


def current_emoji_catalog() -> EmojiCatalogSnapshot:
    return _runtime_catalog or EmojiCatalogSnapshot()


def emoji_catalog_is_loaded() -> bool:
    return _runtime_catalog is not None


def replace_emoji_catalog(snapshot: EmojiCatalogSnapshot) -> None:
    global _runtime_catalog
    _runtime_catalog = snapshot


def clear_emoji_catalog() -> None:
    global _runtime_catalog
    _runtime_catalog = None

"""Adapter registry. Adding a source is one decorator away."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.sources.base import BaseDataSource
from app.sources.http import Fetcher

_REGISTRY: dict[str, type[BaseDataSource]] = {}


def register(key: str) -> Callable[[type[BaseDataSource]], type[BaseDataSource]]:
    def decorator(cls: type[BaseDataSource]) -> type[BaseDataSource]:
        if key in _REGISTRY:
            raise ValueError(f"Adapter key {key!r} is already registered by {_REGISTRY[key]!r}.")
        cls.adapter_key = key
        _REGISTRY[key] = cls
        return cls

    return decorator


def get_adapter_class(key: str) -> type[BaseDataSource]:
    try:
        return _REGISTRY[key]
    except KeyError:
        raise KeyError(
            f"Unknown adapter {key!r}. Registered adapters: {sorted(_REGISTRY)}. "
            "Add a module under app/sources/adapters and decorate it with @register."
        ) from None


def build_adapter(
    key: str,
    config: dict[str, Any] | None = None,
    credentials: dict[str, str] | None = None,
    fetcher: Fetcher | None = None,
) -> BaseDataSource:
    return get_adapter_class(key)(config=config, credentials=credentials, fetcher=fetcher)


def available_adapters() -> list[str]:
    return sorted(_REGISTRY)


def adapter_catalogue() -> list[dict[str, Any]]:
    """What the UI shows on the 'add a source' screen."""
    out = []
    for key in sorted(_REGISTRY):
        cls = _REGISTRY[key]
        out.append(
            {
                "adapter_key": key,
                "requires_network": cls.requires_network,
                "requires_credentials": list(cls.requires_credentials),
                "documented_rate_limit": cls.documented_rate_limit,
                "default_rate_limit_per_minute": cls.default_rate_limit_per_minute,
                "default_source_class": cls.default_source_class,
                "doc": (cls.__doc__ or "").strip().split("\n")[0],
            }
        )
    return out


def _load_builtin_adapters() -> None:
    from app.sources.adapters import (  # noqa: F401
        comtrade,
        csv_import,
        demo_mock,
        fred,
        github,
        hackernews,
        rss,
        scenario,
        sec_edgar,
        wikipedia,
    )


_load_builtin_adapters()

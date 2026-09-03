"""Provider-independent LLM abstraction.

The application never imports a vendor SDK outside this module. Adapters that are
not configured raise a clear error instead of silently degrading, except for
`EchoProvider`, which is a deliberate offline stand-in used in tests and in the
default configuration so the system runs with no API keys at all.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.core.config import settings


@dataclass(slots=True)
class LLMResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    provider: str = ""
    latency_ms: int = 0
    cost_usd: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "The model did not return valid JSON. The call is discarded rather than "
                "repaired, because repairing invites fabrication."
            ) from exc


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    async def complete(
        self, *, system: str, user: str, model: str, max_tokens: int = 2000, json_mode: bool = True
    ) -> LLMResponse: ...


class EchoProvider:
    """Deterministic offline provider.

    Returns a minimal, valid JSON envelope. It never invents facts: every field it
    emits is either empty or copied from the input.
    """

    name = "echo"

    async def complete(
        self,
        *,
        system: str,
        user: str,
        model: str = "echo",
        max_tokens: int = 2000,
        json_mode: bool = True,
    ) -> LLMResponse:
        start = time.monotonic()
        payload = {
            "status": "insufficient_evidence",
            "summary": "Offline provider: no narrative generated.",
            "claims": [],
            "counterarguments": [],
            "missing_evidence": ["No language model is configured (AI_PROVIDER=echo)."],
        }
        return LLMResponse(
            text=json.dumps(payload),
            input_tokens=len(user) // 4,
            output_tokens=len(payload) // 4,
            model=model,
            provider=self.name,
            latency_ms=int((time.monotonic() - start) * 1000),
            cost_usd=0.0,
        )


class _UnconfiguredProvider:
    """Base for vendor adapters that need a key we do not have."""

    name = "unconfigured"
    env_var = ""

    async def complete(self, **_: Any) -> LLMResponse:
        raise RuntimeError(
            f"The {self.name} provider is selected but {self.env_var} is not set. "
            f"Set {self.env_var} in .env, or set AI_PROVIDER=echo to run without a model."
        )


class OpenAIProvider(_UnconfiguredProvider):
    """NOT IMPLEMENTED beyond the guard: wire the SDK here.

    Kept intentionally thin so nobody mistakes an untested integration for a
    working one. See docs/changelog.md.
    """

    name = "openai"
    env_var = "OPENAI_API_KEY"


class AnthropicProvider(_UnconfiguredProvider):
    name = "anthropic"
    env_var = "ANTHROPIC_API_KEY"


class GeminiProvider(_UnconfiguredProvider):
    name = "gemini"
    env_var = "GEMINI_API_KEY"


class LocalProvider(_UnconfiguredProvider):
    name = "local"
    env_var = "LOCAL_MODEL_URL"


_PROVIDERS: dict[str, type] = {
    "echo": EchoProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "local": LocalProvider,
}


def get_provider(name: str | None = None) -> LLMProvider:
    key = name or settings.AI_PROVIDER
    try:
        return _PROVIDERS[key]()  # type: ignore[return-value]
    except KeyError:
        raise ValueError(f"Unknown AI provider {key!r}. Choose one of: {sorted(_PROVIDERS)}.") from None

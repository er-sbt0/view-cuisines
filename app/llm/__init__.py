"""Pluggable LLM providers. Add a provider: one module + one `PROVIDERS` entry."""

from collections.abc import Callable

from app.config import Settings, get_settings
from app.llm.base import ExtractionError, ExtractionRefused, LLMProvider, NotARecipeError


def _claude(settings: Settings) -> LLMProvider:
    from app.llm.claude import ClaudeProvider

    return ClaudeProvider(model=settings.llm_model, effort=settings.llm_effort)


def _fake(settings: Settings) -> LLMProvider:
    from app.llm.fake import FakeProvider

    return FakeProvider()


PROVIDERS: dict[str, Callable[[Settings], LLMProvider]] = {
    "claude": _claude,
    "fake": _fake,
}


def get_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    try:
        factory = PROVIDERS[settings.llm_provider]
    except KeyError:
        raise ValueError(
            f"Unknown llm_provider {settings.llm_provider!r}; expected one of {sorted(PROVIDERS)}"
        ) from None
    return factory(settings)


__all__ = [
    "ExtractionError",
    "ExtractionRefused",
    "LLMProvider",
    "NotARecipeError",
    "PROVIDERS",
    "get_provider",
]

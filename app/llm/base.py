"""Provider-agnostic interface and errors for LLM recipe extraction."""

from typing import Protocol

from app.models import RecipeDraft


class ExtractionError(Exception):
    """Extraction failed. The message is in Hebrew and safe to show to the user."""

    default_message = "חילוץ המתכון נכשל. נסו שוב מאוחר יותר."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)


class NotARecipeError(ExtractionError):
    """The input text does not contain a recipe."""

    default_message = "לא נמצא מתכון בטקסט שהודבק."


class ExtractionRefused(ExtractionError):
    """The model declined to process the input."""

    default_message = "המודל סירב לעבד את הטקסט הזה."


class LLMProvider(Protocol):
    def extract(self, raw_text: str) -> RecipeDraft:
        """Turn free-form recipe text into a structured Hebrew `RecipeDraft`.

        Raises `ExtractionError` (or a subclass) on failure.
        """
        ...

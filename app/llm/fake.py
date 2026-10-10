"""Offline provider for tests and development without an API key."""

from app.models import RecipeDraft


class FakeProvider:
    def __init__(self, draft: RecipeDraft | None = None, error: Exception | None = None) -> None:
        self.draft = draft
        self.error = error
        self.calls: list[str] = []

    def extract(self, raw_text: str) -> RecipeDraft:
        self.calls.append(raw_text)
        if self.error is not None:
            raise self.error
        if self.draft is not None:
            return self.draft
        first_line = raw_text.strip().splitlines()[0] if raw_text.strip() else "מתכון"
        return RecipeDraft(title=first_line[:80], steps=[raw_text.strip()] if raw_text.strip() else [])

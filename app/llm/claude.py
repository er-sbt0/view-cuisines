"""Claude provider: structured-output extraction via the Anthropic SDK."""

import anthropic

from app.llm.base import ExtractionError, ExtractionRefused, NotARecipeError
from app.llm.prompt import build_system_prompt, wrap_recipe_text
from app.models import RecipeDraft
from app.taxonomy import load_taxonomy

MAX_TOKENS = 16000
BETAS = ["server-side-fallback-2026-07-01"]


class ClaudeProvider:
    def __init__(self, model: str, effort: str, client: anthropic.Anthropic | None = None) -> None:
        self.model = model
        self.effort = effort
        self.client = client if client is not None else anthropic.Anthropic()

    def extract(self, raw_text: str) -> RecipeDraft:
        taxonomy = load_taxonomy()
        system = [
            {
                "type": "text",
                "text": build_system_prompt(taxonomy),
                "cache_control": {"type": "ephemeral"},
            }
        ]
        try:
            response = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": wrap_recipe_text(raw_text)}],
                output_format=RecipeDraft,
                output_config={"effort": self.effort},
                betas=BETAS,
                fallbacks="default",
            )
        except anthropic.APIConnectionError as e:
            raise ExtractionError("לא ניתן להתחבר לשירות Claude. בדקו את החיבור לאינטרנט ונסו שוב.") from e
        except anthropic.RateLimitError as e:
            raise ExtractionError("חרגתם ממכסת הבקשות ל-Claude. המתינו מעט ונסו שוב.") from e
        except anthropic.AuthenticationError as e:
            raise ExtractionError("מפתח ה-API של Anthropic אינו תקין. בדקו את ANTHROPIC_API_KEY.") from e
        except anthropic.APIStatusError as e:
            raise ExtractionError(f"שירות Claude החזיר שגיאה ({e.status_code}). נסו שוב מאוחר יותר.") from e
        except TypeError as e:
            # Raised by the SDK before sending when no credentials are configured.
            if "authentication" not in str(e):
                raise
            raise ExtractionError("לא הוגדר מפתח API של Anthropic. הגדירו ANTHROPIC_API_KEY בקובץ .env.") from e

        if response.stop_reason == "refusal":
            raise ExtractionRefused()
        if response.stop_reason == "max_tokens":
            raise ExtractionError("המתכון ארוך מדי לעיבוד. נסו לקצר את הטקסט.")

        draft = response.parsed_output
        if draft is None:
            raise ExtractionError("לא התקבלה תשובה תקינה מהמודל. נסו שוב.")
        if not draft.is_recipe:
            raise NotARecipeError()
        return draft.normalize_categories(taxonomy)

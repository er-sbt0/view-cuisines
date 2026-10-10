"""Parse the review/edit form back into a `RecipeDraft`, and format numbers for display."""

import re
from collections.abc import Mapping, Sequence
from fractions import Fraction

from app.models import Ingredient, RecipeDraft
from app.taxonomy import load_taxonomy

# Field names of the parallel per-row ingredient inputs.
INGREDIENT_FIELDS = ("quantity", "unit", "item", "note")

_UNICODE_FRACTIONS = {
    "½": Fraction(1, 2), "⅓": Fraction(1, 3), "⅔": Fraction(2, 3), "¼": Fraction(1, 4),
    "¾": Fraction(3, 4), "⅛": Fraction(1, 8), "⅜": Fraction(3, 8), "⅝": Fraction(5, 8),
    "⅞": Fraction(7, 8),
}
_DISPLAY_FRACTIONS = {v: k for k, v in _UNICODE_FRACTIONS.items()}
_TAG_SEPARATORS = re.compile(r"[,،;]")


def _text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def parse_quantity(value: object) -> float | None:
    """'2', '0.5', '1,5', '1/2', '½', '1 1/2', '1½' -> float. Anything else -> None."""
    text = _text(value)
    if text is None:
        return None
    total = Fraction(0)
    for ch, frac in _UNICODE_FRACTIONS.items():
        if ch in text:
            total += frac
            text = text.replace(ch, " ")
    try:
        for part in text.split():
            if "/" in part:
                num, den = part.split("/", 1)
                total += Fraction(int(num), int(den))
            else:
                total += Fraction(part.replace(",", "."))
    except (ValueError, ZeroDivisionError):
        return None
    return float(total) if total > 0 else None


def parse_int(value: object) -> int | None:
    """Non-negative integer, or None for blank/invalid input."""
    text = _text(value)
    if text is None:
        return None
    try:
        number = int(float(text.replace(",", ".")))
    except ValueError:
        return None
    return number if number >= 0 else None


def parse_lines(value: object) -> list[str]:
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]


def parse_tags(value: object) -> list[str]:
    """Split on Latin/Arabic commas and semicolons; drop blanks and duplicates."""
    tags: list[str] = []
    for tag in _TAG_SEPARATORS.split(str(value or "")):
        tag = tag.strip().lstrip("#").strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def parse_ingredients(rows: Mapping[str, Sequence[str]]) -> list[Ingredient]:
    """Zip the parallel `ing_<field>` lists into ingredients, dropping rows without an item."""
    columns = [list(rows.get(f"ing_{f}", [])) for f in INGREDIENT_FIELDS]
    count = max((len(c) for c in columns), default=0)
    for column in columns:
        column.extend([""] * (count - len(column)))
    ingredients = []
    for quantity, unit, item, note in zip(*columns):
        item = _text(item)
        if item is None:
            continue
        ingredients.append(
            Ingredient(quantity=parse_quantity(quantity), unit=_text(unit), item=item, note=_text(note))
        )
    return ingredients


def parse_recipe_form(form, require_title: bool = True) -> RecipeDraft:
    """Build a draft from a Starlette `FormData` (or any object with `get`/`getlist`).

    Raises ValueError (Hebrew message) if the title is missing and `require_title`.
    """
    title = _text(form.get("title"))
    if title is None and require_title:
        raise ValueError("חובה למלא שם למתכון.")
    draft = RecipeDraft(
        title=title or "",
        description=_text(form.get("description")),
        cuisine=_text(form.get("cuisine")) or "",
        meal_type=_text(form.get("meal_type")) or "",
        tags=parse_tags(form.get("tags")),
        servings=parse_int(form.get("servings")),
        prep_minutes=parse_int(form.get("prep_minutes")),
        cook_minutes=parse_int(form.get("cook_minutes")),
        ingredients=parse_ingredients({f"ing_{f}": form.getlist(f"ing_{f}") for f in INGREDIENT_FIELDS}),
        steps=parse_lines(form.get("steps")),
        tips=parse_lines(form.get("tips")),
    )
    return draft.normalize_categories(load_taxonomy())


# --- Display ----------------------------------------------------------------


def format_number(value: float | None) -> str:
    """Plain number for form inputs: 2.0 -> '2', 0.5 -> '0.5'."""
    if value is None:
        return ""
    return f"{value:.3f}".rstrip("0").rstrip(".")


def format_quantity(value: float | None) -> str:
    """Friendly quantity: 2.0 -> '2', 0.5 -> '½', 1.5 -> '1½', 0.3 -> '0.3'."""
    if value is None:
        return ""
    whole = int(value)
    frac = Fraction(value - whole).limit_denominator(8)
    if abs(float(frac) - (value - whole)) < 0.01:
        if frac == 0:
            return str(whole)
        if frac in _DISPLAY_FRACTIONS:
            return f"{whole or ''}{_DISPLAY_FRACTIONS[frac]}"
    return format_number(value)


def format_minutes(minutes: int | None) -> str:
    if not minutes:
        return ""
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours} ש׳ {mins} דק׳"
    if hours:
        return "שעה" if hours == 1 else f"{hours} שעות"
    return f"{mins} דק׳"


def total_minutes(draft: RecipeDraft) -> int | None:
    if draft.prep_minutes is None and draft.cook_minutes is None:
        return None
    return (draft.prep_minutes or 0) + (draft.cook_minutes or 0)

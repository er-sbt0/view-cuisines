from collections.abc import Iterator

import pytest

from app.db import Repository
from app.models import Ingredient, RecipeDraft


@pytest.fixture
def repo() -> Iterator[Repository]:
    r = Repository(":memory:")
    yield r
    r.close()


def make_draft(**overrides) -> RecipeDraft:
    base = dict(
        title="סלט חומוס",
        description="סלט קטניות רענן",
        cuisine="ישראלי",
        meal_type="סלט",
        tags=["טבעוני", "מהיר"],
        servings=4,
        prep_minutes=10,
        cook_minutes=0,
        ingredients=[
            Ingredient(quantity=400, unit="גרם", item="חומוס מבושל"),
            Ingredient(quantity=1, unit=None, item="לימון", note="סחוט"),
        ],
        steps=["מערבבים הכול.", "מגישים."],
        tips=["טעים יותר למחרת."],
    )
    base.update(overrides)
    return RecipeDraft(**base)

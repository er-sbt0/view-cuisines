"""Pydantic models shared by the LLM layer, the repository and the web routes."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.taxonomy import OTHER, Taxonomy


class Ingredient(BaseModel):
    quantity: float | None = None
    unit: str | None = None
    item: str
    note: str | None = None


class RecipeDraft(BaseModel):
    """A structured recipe as produced by the LLM (Hebrew, metric units)."""

    is_recipe: bool = True
    title: str
    description: str | None = None
    cuisine: str = OTHER
    meal_type: str = OTHER
    tags: list[str] = Field(default_factory=list)
    servings: int | None = None
    prep_minutes: int | None = None
    cook_minutes: int | None = None
    ingredients: list[Ingredient] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    tips: list[str] = Field(default_factory=list)

    def normalize_categories(self, taxonomy: Taxonomy) -> "RecipeDraft":
        """Return a copy whose cuisine/meal_type are valid taxonomy values (else "אחר")."""
        cuisine = self.cuisine.strip()
        meal_type = self.meal_type.strip()
        return self.model_copy(
            update={
                "cuisine": cuisine if cuisine in taxonomy.cuisines else OTHER,
                "meal_type": meal_type if meal_type in taxonomy.meal_types else OTHER,
            }
        )


class Recipe(RecipeDraft):
    """A persisted recipe."""

    id: int | None = None
    source_text: str
    created_at: datetime
    updated_at: datetime

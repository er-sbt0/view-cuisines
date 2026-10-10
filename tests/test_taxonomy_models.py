from app.config import Settings
from app.taxonomy import OTHER, load_taxonomy
from tests.conftest import make_draft


def test_taxonomy_loads():
    tax = load_taxonomy()
    assert "ישראלי" in tax.cuisines
    assert "קינוח" in tax.meal_types
    assert OTHER in tax.cuisines and OTHER in tax.meal_types


def test_normalize_maps_unknown_to_other():
    tax = load_taxonomy()
    draft = make_draft(cuisine="מאדים", meal_type="brunch")
    norm = draft.normalize_categories(tax)
    assert (norm.cuisine, norm.meal_type) == (OTHER, OTHER)
    assert draft.cuisine == "מאדים"  # original untouched


def test_normalize_keeps_known_values():
    norm = make_draft(cuisine=" איטלקי ", meal_type="עיקרית").normalize_categories(load_taxonomy())
    assert (norm.cuisine, norm.meal_type) == ("איטלקי", "עיקרית")


def test_settings_defaults():
    s = Settings(_env_file=None)
    assert s.llm_provider == "claude"
    assert s.database_path == "data/recipes.db"

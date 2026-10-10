from collections.abc import Iterator
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import FormData

from app.db import Repository
from app.forms import format_quantity, parse_int, parse_quantity, parse_recipe_form, parse_tags
from app.llm.base import ExtractionError, ExtractionRefused, NotARecipeError
from app.llm.fake import FakeProvider
from app.main import create_app
from app.models import Ingredient
from app.taxonomy import OTHER
from tests.conftest import make_draft

HX = {"HX-Request": "true"}


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider(draft=make_draft(title="שקשוקה", tags=["חריף", "צמחוני"]))


@pytest.fixture
def client(repo: Repository, provider: FakeProvider) -> Iterator[TestClient]:
    with TestClient(create_app(repo=repo, provider=provider)) as c:
        yield c


def form_data(**overrides) -> list[tuple[str, str]]:
    """A review-form submission as a browser would send it."""
    fields = {
        "source_text": "טקסט מקורי",
        "title": "סלט חומוס",
        "description": "",
        "cuisine": "ישראלי",
        "meal_type": "סלט",
        "tags": "טבעוני, מהיר",
        "servings": "4",
        "prep_minutes": "10",
        "cook_minutes": "",
        "steps": "מערבבים.\n\nמגישים.\n",
        "tips": "",
    }
    fields.update(overrides)
    rows = [("400", "גרם", "חומוס", ""), ("", "", "", ""), ("½", "", "לימון", "סחוט")]
    data = list(fields.items())
    for quantity, unit, item, note in rows:
        data += [("ing_quantity", quantity), ("ing_unit", unit), ("ing_item", item), ("ing_note", note)]
    return data


def post_form(client: TestClient, url: str, pairs: list[tuple[str, str]], **kwargs):
    """POST repeated form fields (TestClient's `data=` only takes a dict)."""
    headers = {"Content-Type": "application/x-www-form-urlencoded", **kwargs.pop("headers", {})}
    return client.post(url, content=urlencode(pairs), headers=headers, **kwargs)


# --- Catalog -------------------------------------------------------------


def test_catalog_empty_state(client: TestClient):
    r = client.get("/")
    assert r.status_code == 200
    assert '<html lang="he" dir="rtl">' in r.text
    assert "עדיין אין כאן מתכונים" in r.text
    assert "/static/htmx.min.js" in r.text


def test_catalog_lists_recipes_with_facets(client: TestClient, repo: Repository):
    repo.create(make_draft(title="פסטה ברוטב", cuisine="איטלקי", meal_type="עיקרית",
                           tags=["פסטה"], prep_minutes=10, cook_minutes=20), "")
    repo.create(make_draft(title="סלט ירוק"), "")
    r = client.get("/")
    assert "פסטה ברוטב" in r.text and "סלט ירוק" in r.text
    assert "איטלקי · 1" in r.text
    assert "ישראלי · 1" in r.text
    assert "30 דק׳" in r.text
    assert 'href="/?tag=%D7%A4%D7%A1%D7%98%D7%94"' in r.text  # clickable tag


def test_catalog_filters_and_search_partial_vs_full(client: TestClient, repo: Repository):
    repo.create(make_draft(title="פסטה", cuisine="איטלקי", meal_type="עיקרית", tags=["פסטה"],
                           ingredients=[Ingredient(item="ספגטי")]), "")
    repo.create(make_draft(title="מגישים עם החומוס"), "")

    full = client.get("/", params={"cuisine": "איטלקי"})
    assert "<html" in full.text and 'id="q"' in full.text
    assert "פסטה" in full.text and "מגישים עם החומוס" not in full.text

    partial = client.get("/", params={"cuisine": "איטלקי"}, headers=HX)
    assert "<html" not in partial.text and 'id="q"' not in partial.text
    assert "פסטה" in partial.text and "מגישים עם החומוס" not in partial.text
    assert 'name="cuisine" value="איטלקי" data-catalog-state' in partial.text

    found = client.get("/", params={"q": "חומוס"}, headers=HX)
    assert "מגישים עם החומוס" in found.text and "ספגטי" not in found.text
    assert "לא נמצאו מתכונים" in client.get("/", params={"q": "אין-כזה"}, headers=HX).text
    assert "פסטה" in client.get("/", params={"tag": "פסטה"}).text
    assert client.get("/", params={"meal_type": "עיקרית", "q": '"*('}).status_code == 200


# --- Paste and extract ------------------------------------------------------


def test_new_page(client: TestClient):
    r = client.get("/new")
    assert r.status_code == 200
    assert "עבד מתכון" in r.text
    assert 'hx-post="/extract"' in r.text and "hx-indicator" in r.text


def test_extract_returns_prefilled_review_form(client: TestClient, provider: FakeProvider):
    r = client.post("/extract", data={"raw_text": "שקשוקה\r\nביצים ועגבניות"}, headers=HX)
    assert r.status_code == 200
    assert provider.calls == ["שקשוקה\nביצים ועגבניות"]
    assert "<html" not in r.text
    assert 'action="/recipes"' in r.text
    assert 'value="שקשוקה"' in r.text
    assert 'value="חריף, צמחוני"' in r.text
    assert "<option selected>ישראלי</option>" in r.text
    assert 'name="ing_item" value="חומוס מבושל"' in r.text
    assert 'name="ing_quantity" value="400"' in r.text
    assert 'type="hidden" name="source_text" value="שקשוקה\nביצים ועגבניות"' in r.text
    assert "הטקסט המקורי" in r.text


def test_extract_without_htmx_renders_full_page(client: TestClient):
    r = client.post("/extract", data={"raw_text": "שקשוקה"})
    assert "<html" in r.text and 'action="/recipes"' in r.text


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (NotARecipeError(), "לא נמצא מתכון"),
        (ExtractionRefused(), "המודל סירב"),
        (ExtractionError("שגיאת רשת"), "שגיאת רשת"),
    ],
)
def test_extract_error_shows_hebrew_message_and_keeps_text(
    repo: Repository, error: Exception, message: str
):
    client = TestClient(create_app(repo=repo, provider=FakeProvider(error=error)))
    r = client.post("/extract", data={"raw_text": "סתם טקסט"}, headers=HX)
    assert r.status_code == 200
    assert message in r.text
    assert 'role="alert"' in r.text
    assert ">סתם טקסט</textarea>" in r.text


def test_extract_empty_text_does_not_call_provider(client: TestClient, provider: FakeProvider):
    r = client.post("/extract", data={"raw_text": "   "}, headers=HX)
    assert "הדביקו טקסט" in r.text
    assert provider.calls == []


# --- Save, view, edit, delete ---------------------------------------------


def test_save_creates_recipe_and_redirects(client: TestClient, repo: Repository):
    r = post_form(client, "/recipes", form_data(), follow_redirects=False)
    assert r.status_code == 303
    [recipe] = repo.list()
    assert r.headers["location"] == f"/recipes/{recipe.id}"
    assert recipe.title == "סלט חומוס"
    assert recipe.source_text == "טקסט מקורי"
    assert [i.item for i in recipe.ingredients] == ["חומוס", "לימון"]
    assert recipe.ingredients[1].quantity == 0.5
    assert recipe.steps == ["מערבבים.", "מגישים."]
    assert recipe.cook_minutes is None


def test_save_via_htmx_uses_hx_redirect(client: TestClient, repo: Repository):
    r = post_form(client, "/recipes", form_data(), headers=HX)
    assert r.headers["HX-Redirect"] == f"/recipes/{repo.list()[0].id}"


def test_save_without_title_rerenders_form(client: TestClient, repo: Repository):
    r = post_form(client, "/recipes", form_data(title="  "))
    assert r.status_code == 400
    assert "חובה למלא שם" in r.text
    assert 'name="ing_item" value="לימון"' in r.text  # user input kept
    assert repo.list() == []


def test_view_recipe(client: TestClient, repo: Repository):
    recipe = repo.create(
        make_draft(ingredients=[Ingredient(quantity=0.5, unit="כוס", item="טחינה", note="גולמית"),
                                Ingredient(quantity=2.0, item="ביצים")]),
        "src",
    )
    r = client.get(f"/recipes/{recipe.id}")
    assert r.status_code == 200
    assert "<h1>סלט חומוס</h1>" in r.text
    assert '<bdi dir="ltr">½</bdi> כוס' in r.text and "טחינה" in r.text and "גולמית" in r.text
    assert '<bdi dir="ltr">2</bdi>' in r.text and "2.0" not in r.text
    assert "<li>מערבבים הכול.</li>" in r.text
    assert "טעים יותר למחרת." in r.text
    assert "hx-confirm=" in r.text


def test_view_missing_recipe_is_404(client: TestClient):
    r = client.get("/recipes/999")
    assert r.status_code == 404
    assert "הדף לא נמצא" in r.text


def test_edit_and_update(client: TestClient, repo: Repository):
    recipe = repo.create(make_draft(), "מקור")
    r = client.get(f"/recipes/{recipe.id}/edit")
    assert r.status_code == 200
    assert f'action="/recipes/{recipe.id}"' in r.text and 'value="סלט חומוס"' in r.text

    r = post_form(client, f"/recipes/{recipe.id}", form_data(title="חומוס חם", source_text="זיוף"),
                  follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/recipes/{recipe.id}"
    updated = repo.get(recipe.id)
    assert updated.title == "חומוס חם"
    assert updated.source_text == "מקור"  # source text is not editable via the form


def test_update_and_edit_missing_is_404(client: TestClient):
    assert post_form(client, "/recipes/999", form_data()).status_code == 404
    assert client.get("/recipes/999/edit").status_code == 404


def test_reextract_uses_stored_source_text(client: TestClient, repo: Repository,
                                           provider: FakeProvider):
    recipe = repo.create(make_draft(title="ישן"), "הטקסט השמור")
    r = client.post(f"/recipes/{recipe.id}/reextract", headers=HX)
    assert r.status_code == 200
    assert provider.calls == ["הטקסט השמור"]
    assert 'value="שקשוקה"' in r.text
    assert f'action="/recipes/{recipe.id}"' in r.text
    assert repo.get(recipe.id).title == "ישן"  # not saved until the form is submitted


def test_reextract_error_keeps_existing_values(repo: Repository):
    recipe = repo.create(make_draft(title="ישן"), "src")
    client = TestClient(create_app(repo=repo, provider=FakeProvider(error=ExtractionRefused())))
    r = client.post(f"/recipes/{recipe.id}/reextract")
    assert "המודל סירב" in r.text and 'value="ישן"' in r.text


def test_delete(client: TestClient, repo: Repository):
    recipe = repo.create(make_draft(), "")
    r = client.post(f"/recipes/{recipe.id}/delete", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert repo.get(recipe.id) is None
    r = client.post(f"/recipes/{recipe.id}/delete", headers=HX)
    assert r.headers["HX-Redirect"] == "/"


def test_app_module_imports_lazily():
    import app.main

    assert app.main.app.state.repo is None and app.main.app.state.provider is None


# --- Form parsing ----------------------------------------------------------


def test_parse_form_edge_cases():
    form = FormData(form_data(
        tags="טבעוני، מהיר,  ,#קל;מהיר",
        servings="abc", prep_minutes="", cook_minutes="-5",
        cuisine="קלינגוני", meal_type="",
        description="   ",
        steps="  \nשלב א\n\n  שלב ב  \n", tips="\n",
    ))
    draft = parse_recipe_form(form)
    assert draft.tags == ["טבעוני", "מהיר", "קל"]
    assert draft.servings is None and draft.prep_minutes is None and draft.cook_minutes is None
    assert draft.cuisine == OTHER and draft.meal_type == OTHER
    assert draft.description is None
    assert draft.steps == ["שלב א", "שלב ב"] and draft.tips == []
    assert len(draft.ingredients) == 2  # blank row dropped
    assert draft.ingredients[0].note is None and draft.ingredients[1].unit is None


def test_parse_form_ragged_ingredient_lists():
    form = FormData([("title", "x"), ("ing_item", "מלח"), ("ing_item", "פלפל"), ("ing_quantity", "1")])
    draft = parse_recipe_form(form)
    assert [(i.quantity, i.item) for i in draft.ingredients] == [(1.0, "מלח"), (None, "פלפל")]


@pytest.mark.parametrize(
    ("text", "expected"),
    [("2", 2.0), ("0.5", 0.5), ("1,5", 1.5), ("1,25", 1.25), ("0,500", 0.5), ("1,500", 1500.0),
     ("12,000", 12000.0), ("1,500.5", 1500.5), ("1,50,0", None), ("1/2", 0.5), ("½", 0.5), ("1 1/2", 1.5),
     ("1½", 1.5), ("", None), ("  ", None), ("קצת", None), ("1/0", None), ("0", None)],
)
def test_parse_quantity(text: str, expected: float | None):
    assert parse_quantity(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("4", 4), ("1,500", 1500), ("1,5", 1), ("", None), ("-3", None), ("כמה", None)],
)
def test_parse_int(text: str, expected: int | None):
    assert parse_int(text) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(2.0, "2"), (0.5, "½"), (1.5, "1½"), (0.25, "¼"), (0.3, "0.3"), (250.0, "250"), (None, "")],
)
def test_format_quantity(value: float | None, expected: str):
    assert format_quantity(value) == expected


def test_parse_tags_splits_on_hebrew_and_latin_commas():
    assert parse_tags("א،ב,ג") == ["א", "ב", "ג"]

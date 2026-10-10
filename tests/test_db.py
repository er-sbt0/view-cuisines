import pytest

from app.db import Repository
from app.models import Ingredient
from tests.conftest import make_draft


def test_crud_roundtrip(repo: Repository):
    created = repo.create(make_draft(), source_text="raw text")
    assert created.id is not None
    assert created.source_text == "raw text"

    fetched = repo.get(created.id)
    assert fetched == created
    assert fetched.ingredients[1].note == "סחוט"
    assert fetched.tags == ["טבעוני", "מהיר"]

    updated = repo.update(created.id, make_draft(title="חומוס חם", servings=2))
    assert updated.title == "חומוס חם"
    assert updated.servings == 2
    assert updated.created_at == created.created_at
    assert updated.updated_at >= created.updated_at
    assert updated.source_text == "raw text"

    repo.delete(created.id)
    assert repo.get(created.id) is None


def test_update_missing_raises(repo: Repository):
    with pytest.raises(KeyError):
        repo.update(999, make_draft())


def test_file_path_creates_parent_dir(tmp_path):
    r = Repository(tmp_path / "nested" / "recipes.db")
    r.create(make_draft(), "x")
    assert (tmp_path / "nested" / "recipes.db").exists()
    r.close()


def test_list_filters_and_order(repo: Repository):
    a = repo.create(make_draft(title="א", cuisine="ישראלי", meal_type="סלט", tags=["טבעוני"]), "")
    b = repo.create(make_draft(title="ב", cuisine="איטלקי", meal_type="עיקרית", tags=["פסטה"]), "")
    c = repo.create(make_draft(title="ג", cuisine="ישראלי", meal_type="עיקרית", tags=["בשרי"]), "")

    assert [r.id for r in repo.list()] == [c.id, b.id, a.id]
    assert [r.id for r in repo.list(cuisine="ישראלי")] == [c.id, a.id]
    assert [r.id for r in repo.list(meal_type="עיקרית")] == [c.id, b.id]
    assert [r.id for r in repo.list(tag="פסטה")] == [b.id]
    assert [r.id for r in repo.list(cuisine="ישראלי", meal_type="עיקרית")] == [c.id]
    assert repo.list(tag="פסט") == []  # tags match exactly


def test_facet_counts(repo: Repository):
    repo.create(make_draft(cuisine="ישראלי", meal_type="סלט"), "")
    repo.create(make_draft(cuisine="ישראלי", meal_type="עיקרית"), "")
    repo.create(make_draft(cuisine="איטלקי", meal_type="עיקרית"), "")
    assert repo.facet_counts() == {
        "cuisine": {"ישראלי": 2, "איטלקי": 1},
        "meal_type": {"עיקרית": 2, "סלט": 1},
    }


def test_fts_finds_hebrew_ingredient(repo: Repository):
    hummus = repo.create(make_draft(title="סלט", description=None), "")
    repo.create(
        make_draft(title="פסטה", description=None, tags=[],
                   ingredients=[Ingredient(item="ספגטי")]),
        "",
    )
    assert [r.id for r in repo.list(query="חומוס")] == [hummus.id]
    assert [r.id for r in repo.list(query="חומ")] == [hummus.id]  # prefix
    assert [r.id for r in repo.list(query="טבעוני")] == [hummus.id]  # tags indexed
    assert repo.list(query="חומוס ספגטי") == []  # terms are ANDed


@pytest.mark.parametrize(
    "query",
    ['"', '"""', "***", "%_%", "\\", "*", "OR", "AND", "NOT", "חומוס OR", '"חומוס',
     "(", "-", "a:b", "^", "  ", ""],
)
def test_fts_survives_odd_input(repo: Repository, query: str):
    repo.create(make_draft(), "")
    repo.list(query=query)  # must not raise


def test_fts_tracks_update_and_delete(repo: Repository):
    r = repo.create(make_draft(), "")
    repo.update(r.id, make_draft(title="שקשוקה", ingredients=[Ingredient(item="עגבניות")], tags=[]))
    assert repo.list(query="חומוס") == []
    assert [x.id for x in repo.list(query="עגבניות")] == [r.id]
    assert [x.id for x in repo.list(query="שקשוקה")] == [r.id]

    repo.delete(r.id)
    assert repo.list(query="עגבניות") == []
    assert repo.list(query="שקשוקה") == []


def test_fts_matches_hebrew_prefixed_words(repo: Repository):
    r = repo.create(
        make_draft(title="סלט", description="מגישים עם החומוס וטחינה", tags=[],
                   ingredients=[Ingredient(item="וחומוס")]),
        "",
    )
    assert [x.id for x in repo.list(query="חומוס")] == [r.id]
    assert [x.id for x in repo.list(query="טחינה")] == [r.id]


def test_fts_short_terms_fall_back_to_substring(repo: Repository):
    r = repo.create(make_draft(title="פו מרק", description=None, tags=[]), "")
    repo.create(make_draft(title="פסטה", description=None, tags=[],
                           ingredients=[Ingredient(item="ספגטי")]), "")
    assert [x.id for x in repo.list(query="פו")] == [r.id]
    assert [x.id for x in repo.list(query="פו חומוס")] == [r.id]  # short + long, ANDed
    assert repo.list(query="%") == []  # LIKE wildcards are escaped
    assert repo.list(query="_") == []


def test_existing_unicode61_fts_is_rebuilt_as_trigram(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    r = Repository(path)
    created = r.create(make_draft(description="עם החומוס"), "")
    r.close()
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE recipes_fts")
    conn.execute("CREATE VIRTUAL TABLE recipes_fts USING fts5(title, description, ingredients, tags)")
    conn.commit()
    conn.close()

    r = Repository(path)
    assert [x.id for x in r.list(query="חומוס")] == [created.id]
    r.close()

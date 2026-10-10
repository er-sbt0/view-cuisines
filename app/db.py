"""SQLite persistence for recipes, with an FTS5 index for free-text search."""

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.models import Recipe, RecipeDraft

SCHEMA = """
CREATE TABLE IF NOT EXISTS recipes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    is_recipe    INTEGER NOT NULL DEFAULT 1,
    title        TEXT NOT NULL,
    description  TEXT,
    cuisine      TEXT NOT NULL,
    meal_type    TEXT NOT NULL,
    tags         TEXT NOT NULL DEFAULT '[]',  -- JSON list[str]
    servings     INTEGER,
    prep_minutes INTEGER,
    cook_minutes INTEGER,
    ingredients  TEXT NOT NULL DEFAULT '[]',  -- JSON list[Ingredient]
    steps        TEXT NOT NULL DEFAULT '[]',  -- JSON list[str]
    tips         TEXT NOT NULL DEFAULT '[]',  -- JSON list[str]
    source_text  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recipes_cuisine ON recipes(cuisine);
CREATE INDEX IF NOT EXISTS idx_recipes_meal_type ON recipes(meal_type);
CREATE INDEX IF NOT EXISTS idx_recipes_created ON recipes(created_at);

"""

# Standalone FTS table; rowid mirrors recipes.id. Kept in sync explicitly by
# the Repository because the ingredients text is derived from JSON. The trigram
# tokenizer gives substring matching, which Hebrew needs: prefix letters attach
# to words, so "חומוס" must also match "החומוס" and "וחומוס".
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS recipes_fts USING fts5(
    title, description, ingredients, tags,
    tokenize = 'trigram'
);
"""
FTS_COLUMNS = ("title", "description", "ingredients", "tags")

# Columns written from a RecipeDraft, in a fixed order.
_DRAFT_COLUMNS = (
    "is_recipe", "title", "description", "cuisine", "meal_type", "tags",
    "servings", "prep_minutes", "cook_minutes", "ingredients", "steps", "tips",
)
_JSON_COLUMNS = ("tags", "ingredients", "steps", "tips")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _draft_values(draft: RecipeDraft) -> list[Any]:
    data = draft.model_dump(mode="json")
    return [
        json.dumps(data[c], ensure_ascii=False) if c in _JSON_COLUMNS else data[c]
        for c in _DRAFT_COLUMNS
    ]


def _fts_values(draft: RecipeDraft) -> tuple[str, str, str, str]:
    return (
        draft.title,
        draft.description or "",
        " ".join(i.item for i in draft.ingredients),
        " ".join(draft.tags),
    )


def search_clause(text: str) -> tuple[str, list[str]] | None:
    """Turn raw user input into a safe SQL condition over `recipes_fts`.

    Each whitespace-separated term must appear as a substring (terms are ANDed).
    Terms of 3+ characters go through the trigram index as quoted FTS5 phrases,
    so operators and punctuation (`"`, `*`, `OR`, `-`, ...) are plain text.
    Shorter terms can't be trigram-matched and fall back to an escaped LIKE.
    Returns None if there is nothing to search.
    """
    terms = text.split()
    if not terms:
        return None
    long_terms = [t for t in terms if len(t) >= 3]
    short_terms = [t for t in terms if len(t) < 3]
    conditions: list[str] = []
    params: list[str] = []
    if long_terms:
        conditions.append("recipes_fts MATCH ?")
        params.append(" ".join('"{}"'.format(t.replace('"', '""')) for t in long_terms))
    for term in short_terms:
        pattern = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conditions.append(
            "(" + " OR ".join(f"{c} LIKE ? ESCAPE '\\'" for c in FTS_COLUMNS) + ")"
        )
        params.extend([pattern] * len(FTS_COLUMNS))
    return " AND ".join(conditions), params


class Repository:
    def __init__(self, path: str | Path) -> None:
        path = str(path)
        if path != ":memory:" and not path.startswith("file:"):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # A single connection is shared across FastAPI worker threads.
        self._lock = threading.RLock()
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)
            self._ensure_fts()

    def close(self) -> None:
        self._conn.close()

    # --- CRUD -------------------------------------------------------------

    def create(self, draft: RecipeDraft, source_text: str) -> Recipe:
        now = _now()
        cols = ", ".join((*_DRAFT_COLUMNS, "source_text", "created_at", "updated_at"))
        marks = ", ".join("?" * (len(_DRAFT_COLUMNS) + 3))
        with self._lock, self._conn:
            cur = self._conn.execute(
                f"INSERT INTO recipes ({cols}) VALUES ({marks})",
                [*_draft_values(draft), source_text, now, now],
            )
            recipe_id = cur.lastrowid
            self._conn.execute(
                "INSERT INTO recipes_fts (rowid, title, description, ingredients, tags) "
                "VALUES (?, ?, ?, ?, ?)",
                (recipe_id, *_fts_values(draft)),
            )
        recipe = self.get(recipe_id)
        assert recipe is not None
        return recipe

    def get(self, id: int) -> Recipe | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM recipes WHERE id = ?", (id,)).fetchone()
        return self._to_recipe(row) if row else None

    def update(self, id: int, draft: RecipeDraft) -> Recipe:
        """Replace a recipe's structured fields. Raises KeyError if it doesn't exist."""
        assignments = ", ".join(f"{c} = ?" for c in _DRAFT_COLUMNS)
        with self._lock, self._conn:
            cur = self._conn.execute(
                f"UPDATE recipes SET {assignments}, updated_at = ? WHERE id = ?",
                [*_draft_values(draft), _now(), id],
            )
            if cur.rowcount == 0:
                raise KeyError(id)
            self._conn.execute(
                "UPDATE recipes_fts SET title = ?, description = ?, ingredients = ?, tags = ? "
                "WHERE rowid = ?",
                (*_fts_values(draft), id),
            )
        recipe = self.get(id)
        assert recipe is not None
        return recipe

    def delete(self, id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM recipes WHERE id = ?", (id,))
            self._conn.execute("DELETE FROM recipes_fts WHERE rowid = ?", (id,))

    # --- Queries ----------------------------------------------------------

    def list(
        self,
        cuisine: str | None = None,
        meal_type: str | None = None,
        tag: str | None = None,
        query: str | None = None,
    ) -> list[Recipe]:
        """Recipes matching all given filters, newest first."""
        where: list[str] = []
        params: list[Any] = []
        if cuisine:
            where.append("r.cuisine = ?")
            params.append(cuisine)
        if meal_type:
            where.append("r.meal_type = ?")
            params.append(meal_type)
        if tag:
            where.append("EXISTS (SELECT 1 FROM json_each(r.tags) WHERE value = ?)")
            params.append(tag)
        if query and (search := search_clause(query)):
            condition, search_params = search
            where.append(f"r.id IN (SELECT rowid FROM recipes_fts WHERE {condition})")
            params.extend(search_params)
        sql = "SELECT r.* FROM recipes r"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY r.created_at DESC, r.id DESC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._to_recipe(row) for row in rows]

    def facet_counts(self) -> dict[str, dict[str, int]]:
        """Recipe counts per category: {"cuisine": {...}, "meal_type": {...}}."""
        result: dict[str, dict[str, int]] = {}
        with self._lock:
            for column in ("cuisine", "meal_type"):
                rows = self._conn.execute(
                    f"SELECT {column} AS value, COUNT(*) AS n FROM recipes "
                    f"GROUP BY {column} ORDER BY n DESC, value"
                ).fetchall()
                result[column] = {row["value"]: row["n"] for row in rows}
        return result

    # --- Helpers ----------------------------------------------------------

    def _ensure_fts(self) -> None:
        """Create the FTS table, rebuilding it if it predates the trigram tokenizer."""
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'recipes_fts'"
        ).fetchone()
        if row is not None and "trigram" in row["sql"]:
            return
        self._conn.execute("DROP TABLE IF EXISTS recipes_fts")
        self._conn.executescript(FTS_SCHEMA)
        for recipe_row in self._conn.execute("SELECT * FROM recipes").fetchall():
            recipe = self._to_recipe(recipe_row)
            self._conn.execute(
                "INSERT INTO recipes_fts (rowid, title, description, ingredients, tags) "
                "VALUES (?, ?, ?, ?, ?)",
                (recipe.id, *_fts_values(recipe)),
            )

    @staticmethod
    def _to_recipe(row: sqlite3.Row) -> Recipe:
        data = dict(row)
        for column in _JSON_COLUMNS:
            data[column] = json.loads(data[column])
        data["is_recipe"] = bool(data["is_recipe"])
        return Recipe.model_validate(data)

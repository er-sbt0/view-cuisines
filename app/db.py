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

-- Standalone FTS table; rowid mirrors recipes.id. Kept in sync explicitly by
-- the Repository because the ingredients text is derived from JSON.
CREATE VIRTUAL TABLE IF NOT EXISTS recipes_fts USING fts5(
    title, description, ingredients, tags,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""

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


def fts_query(text: str) -> str | None:
    """Turn raw user input into a safe FTS5 query.

    Each whitespace-separated term becomes a quoted prefix phrase, so FTS5
    operators and punctuation (`"`, `*`, `OR`, `-`, ...) are treated as plain
    text. Terms are implicitly ANDed. Returns None if there is nothing to search.
    """
    terms = [t.replace('"', '""') for t in text.split()]
    terms = [t for t in terms if t.strip('"')]
    if not terms:
        return None
    return " ".join(f'"{t}"*' for t in terms)


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
        if query and (match := fts_query(query)):
            where.append("r.id IN (SELECT rowid FROM recipes_fts WHERE recipes_fts MATCH ?)")
            params.append(match)
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

    @staticmethod
    def _to_recipe(row: sqlite3.Row) -> Recipe:
        data = dict(row)
        for column in _JSON_COLUMNS:
            data[column] = json.loads(data[column])
        data["is_recipe"] = bool(data["is_recipe"])
        return Recipe.model_validate(data)

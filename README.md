# המתכונים שלי — personal recipe catalog

A local, single-user recipe catalog in Hebrew (RTL). Paste a recipe from anywhere — a blog post,
an Instagram caption, a message from a friend — and an LLM turns it into a clean, structured
Hebrew recipe with metric units, categorized by cuisine and meal type. Review and fix the draft,
save it, then browse, filter and search your collection.

Stack: FastAPI + Jinja2 + HTMX (vendored, no JS build step), SQLite with FTS5 (trigram) search.

## Setup

```sh
uv sync
cp .env.example .env   # then set ANTHROPIC_API_KEY in .env
```

Settings (env vars or `.env`): `LLM_PROVIDER` (`claude` | `fake`), `LLM_MODEL`, `LLM_EFFORT`,
`DATABASE_PATH` (default `data/recipes.db`).

## Run

```sh
uv run uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. To run fully offline without an API key, use the fake provider
(it returns a trivial draft built from the pasted text):

```sh
LLM_PROVIDER=fake uv run uvicorn app.main:app --reload
```

## Tests

```sh
uv run pytest -q
```

The suite is offline (in-memory SQLite + `FakeProvider`) and runs in about a second.

## Adding an LLM provider

1. Add a module in `app/llm/` with a class implementing `extract(raw_text) -> RecipeDraft`
   (see `app/llm/base.py`). Raise `ExtractionError` / `NotARecipeError` / `ExtractionRefused`
   with Hebrew messages on failure, and call `draft.normalize_categories(load_taxonomy())`.
2. Register a factory in `PROVIDERS` in `app/llm/__init__.py`.
3. Select it with `LLM_PROVIDER=<name>`.

## Editing the taxonomy

Cuisines and meal types live in `app/taxonomy.yaml`. Edit the lists and restart the server;
the LLM prompt and the form dropdowns pick them up. Keep `אחר` in both lists — it is the fallback
for anything unrecognized. Existing recipes keep their stored values.

## Layout

- `app/main.py` — routes (`create_app()` factory; `app` for uvicorn)
- `app/forms.py` — review-form parsing and number formatting
- `app/db.py` — SQLite repository and search
- `app/llm/` — provider interface, Claude provider, fake provider, prompt
- `app/templates/`, `app/static/` — Hebrew RTL UI

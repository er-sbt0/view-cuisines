"""FastAPI web app: paste a recipe, review the extracted draft, browse and search the catalog."""

import threading
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import forms
from app.config import get_settings
from app.db import Repository
from app.llm import ExtractionError, LLMProvider, get_provider
from app.models import Recipe, RecipeDraft
from app.taxonomy import load_taxonomy

APP_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=APP_DIR / "templates")
templates.env.filters["qty"] = forms.format_quantity
templates.env.filters["num"] = forms.format_number
templates.env.filters["minutes"] = forms.format_minutes
templates.env.globals["total_minutes"] = forms.total_minutes


def catalog_url(**params: str | None) -> str:
    """`/` with the given non-empty query params."""
    clean = {k: v for k, v in params.items() if v}
    return "/?" + urlencode(clean) if clean else "/"


templates.env.globals["catalog_url"] = catalog_url


# --- Dependencies (lazy so importing `app.main` opens no DB and needs no API key) ---

_init_lock = threading.Lock()


def get_repo(request: Request) -> Repository:
    state = request.app.state
    if state.repo is None:
        with _init_lock:
            if state.repo is None:
                state.repo = Repository(get_settings().database_path)
    return state.repo


def get_llm(request: Request) -> LLMProvider:
    state = request.app.state
    if state.provider is None:
        with _init_lock:
            if state.provider is None:
                state.provider = get_provider()
    return state.provider


def is_htmx(request: Request) -> bool:
    return (
        request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-History-Restore-Request") != "true"
    )


def redirect(request: Request, url: str) -> Response:
    """Redirect that works for both HTMX and plain form submissions."""
    if is_htmx(request):
        return Response(status_code=200, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


def form_context(
    draft: RecipeDraft,
    source_text: str,
    action: str,
    error: str | None = None,
) -> dict:
    return {
        "draft": draft,
        "source_text": source_text,
        "action": action,
        "error": error,
        "taxonomy": load_taxonomy(),
    }


def get_recipe_or_404(repo: Repository, recipe_id: int) -> Recipe:
    recipe = repo.get(recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404)
    return recipe


# --- App factory -------------------------------------------------------------


def create_app(repo: Repository | None = None, provider: LLMProvider | None = None) -> FastAPI:
    app = FastAPI(title="המתכונים שלי")
    app.state.repo = repo
    app.state.provider = provider
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if exc.status_code == 404:
            return templates.TemplateResponse(request, "404.html", {}, status_code=404)
        return HTMLResponse(str(exc.detail), status_code=exc.status_code)

    # --- Catalog ---------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def catalog(
        request: Request,
        q: str = "",
        cuisine: str = "",
        meal_type: str = "",
        tag: str = "",
    ) -> Response:
        repo = get_repo(request)
        recipes = repo.list(
            cuisine=cuisine or None, meal_type=meal_type or None, tag=tag or None, query=q or None
        )
        context = {
            "recipes": recipes,
            "facets": repo.facet_counts(),
            "q": q,
            "cuisine": cuisine,
            "meal_type": meal_type,
            "tag": tag,
            "filtered": bool(q or cuisine or meal_type or tag),
        }
        template = "partials/catalog.html" if is_htmx(request) else "index.html"
        return templates.TemplateResponse(request, template, context)

    # --- New recipe: paste -> extract -> review -> save ------------------

    @app.get("/new", response_class=HTMLResponse)
    def new_recipe(request: Request) -> Response:
        return templates.TemplateResponse(request, "new.html", {"raw_text": ""})

    @app.post("/extract", response_class=HTMLResponse)
    def extract(request: Request, raw_text: Annotated[str, Form()] = "") -> Response:
        # Plain `def`: the LLM call blocks for several seconds, so it runs in the threadpool.
        htmx = is_htmx(request)
        raw_text = raw_text.replace("\r\n", "\n")
        if not raw_text.strip():
            context = {"raw_text": raw_text, "error": "הדביקו טקסט של מתכון כדי להתחיל."}
            template = "partials/paste_form.html" if htmx else "new.html"
            return templates.TemplateResponse(request, template, context, status_code=200)
        try:
            draft = get_llm(request).extract(raw_text)
        except ExtractionError as e:
            context = {"raw_text": raw_text, "error": str(e)}
            template = "partials/paste_form.html" if htmx else "new.html"
            return templates.TemplateResponse(request, template, context)
        context = form_context(draft, raw_text, action="/recipes")
        template = "partials/recipe_form.html" if htmx else "edit.html"
        return templates.TemplateResponse(request, template, context)

    @app.post("/recipes")
    async def create_recipe(request: Request) -> Response:
        form = await request.form()
        source_text = str(form.get("source_text") or "").replace("\r\n", "\n")
        try:
            draft = forms.parse_recipe_form(form)
        except ValueError as e:
            return _invalid_form(request, form, source_text, "/recipes", str(e))
        recipe = get_repo(request).create(draft, source_text)
        return redirect(request, f"/recipes/{recipe.id}")

    # --- Existing recipes -------------------------------------------------

    @app.get("/recipes/{recipe_id}", response_class=HTMLResponse)
    def view_recipe(request: Request, recipe_id: int) -> Response:
        recipe = get_recipe_or_404(get_repo(request), recipe_id)
        return templates.TemplateResponse(request, "recipe.html", {"recipe": recipe})

    @app.get("/recipes/{recipe_id}/edit", response_class=HTMLResponse)
    def edit_recipe(request: Request, recipe_id: int) -> Response:
        recipe = get_recipe_or_404(get_repo(request), recipe_id)
        context = form_context(recipe, recipe.source_text, action=f"/recipes/{recipe_id}")
        return templates.TemplateResponse(request, "edit.html", {**context, "recipe": recipe})

    @app.post("/recipes/{recipe_id}")
    async def update_recipe(request: Request, recipe_id: int) -> Response:
        form = await request.form()
        repo = get_repo(request)
        recipe = get_recipe_or_404(repo, recipe_id)
        try:
            draft = forms.parse_recipe_form(form)
        except ValueError as e:
            return _invalid_form(request, form, recipe.source_text, f"/recipes/{recipe_id}", str(e))
        try:
            repo.update(recipe_id, draft)
        except KeyError:
            raise HTTPException(status_code=404) from None
        return redirect(request, f"/recipes/{recipe_id}")

    @app.post("/recipes/{recipe_id}/reextract", response_class=HTMLResponse)
    def reextract_recipe(request: Request, recipe_id: int) -> Response:
        recipe = get_recipe_or_404(get_repo(request), recipe_id)
        action = f"/recipes/{recipe_id}"
        try:
            draft = get_llm(request).extract(recipe.source_text)
            context = form_context(draft, recipe.source_text, action=action)
        except ExtractionError as e:
            context = form_context(recipe, recipe.source_text, action=action, error=str(e))
        context["recipe"] = recipe
        template = "partials/recipe_form.html" if is_htmx(request) else "edit.html"
        return templates.TemplateResponse(request, template, context)

    @app.post("/recipes/{recipe_id}/delete")
    def delete_recipe(request: Request, recipe_id: int) -> Response:
        get_repo(request).delete(recipe_id)
        return redirect(request, "/")

    return app


def _invalid_form(request: Request, form, source_text: str, action: str, error: str) -> Response:
    """Re-render the review form with what the user typed and an error message."""
    draft = forms.parse_recipe_form(form, require_title=False)
    context = form_context(draft, source_text, action=action, error=error)
    return templates.TemplateResponse(request, "edit.html", context, status_code=400)


app = create_app()

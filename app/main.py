import json
import logging
import os
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .catalog import generate_catalog_toml, submit_to_catalog
from .db import get_db, get_setting, init_db, set_setting
from .github_client import create_or_update_repo, fetch_repo_summary, get_authenticated_user
from .llm import generate_bottle_files

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Cloud in a Bottle App Bottler", version="1.0.0")

# Mount static and templates
static_dir = BASE_DIR / "static"
static_dir.mkdir(parents=True, exist_ok=True)
templates_dir = BASE_DIR / "templates"
templates_dir.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
templates = Jinja2Templates(directory=str(templates_dir))


@app.on_event("startup")
def startup():
    init_db()


# Models
class SettingsPayload(BaseModel):
    github_token: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None


class AnalyzePayload(BaseModel):
    repo_url: str


class GeneratePayload(BaseModel):
    repo_summary: dict
    custom_prompt: str = ""


class PublishPayload(BaseModel):
    name: str
    title: str
    description: str = ""
    source_repo: str
    upstream_tag: str = ""
    files: dict[str, str]
    catalog_toml: str = ""


class SubmitCatalogPayload(BaseModel):
    name: str
    catalog_toml: str


@app.get("/healthz")
@app.get("/health")
def healthz():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


ZONE_DOMAIN = os.environ.get("BOTTLE_ZONE_DOMAIN") or os.environ.get("OPENHOST_ZONE_DOMAIN") or ""


@app.get("/api/settings")
def get_settings():
    gh_token = get_setting("github_token", "")
    llm_key = get_setting("llm_api_key", "")
    return {
        "github_token": f"{gh_token[:4]}...{gh_token[-4:]}" if len(gh_token) > 8 else ("configured" if gh_token else ""),
        "has_github_token": bool(gh_token),
        "llm_base_url": get_setting("llm_base_url", "https://api.openai.com/v1"),
        "llm_api_key": f"{llm_key[:4]}...{llm_key[-4:]}" if len(llm_key) > 8 else ("configured" if llm_key else ""),
        "has_llm_key": bool(llm_key),
        "llm_model": get_setting("llm_model", "gpt-4o"),
        "zone_domain": ZONE_DOMAIN,
    }


@app.post("/api/settings")
def save_settings(payload: SettingsPayload):
    if payload.github_token is not None and payload.github_token != "":
        set_setting("github_token", payload.github_token.strip())
    if payload.llm_base_url is not None and payload.llm_base_url != "":
        set_setting("llm_base_url", payload.llm_base_url.strip())
    if payload.llm_api_key is not None and payload.llm_api_key != "":
        set_setting("llm_api_key", payload.llm_api_key.strip())
    if payload.llm_model is not None and payload.llm_model != "":
        set_setting("llm_model", payload.llm_model.strip())
    return {"status": "ok"}


@app.post("/api/analyze")
async def analyze_repo(payload: AnalyzePayload):
    token = get_setting("github_token", "")
    try:
        summary = await fetch_repo_summary(payload.repo_url, token)
        return summary
    except Exception as e:
        logger.exception("Error analyzing repo")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/generate")
async def generate_app(payload: GeneratePayload):
    api_key = get_setting("llm_api_key", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="LLM API Key is not configured in Settings.")

    base_url = get_setting("llm_base_url", "https://api.openai.com/v1")
    model = get_setting("llm_model", "gpt-4o")

    try:
        res = await generate_bottle_files(
            repo_summary=payload.repo_summary,
            api_key=api_key,
            base_url=base_url,
            model=model,
            custom_prompt=payload.custom_prompt,
        )
        return res
    except Exception as e:
        logger.exception("Error generating app files")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/publish")
async def publish_app(payload: PublishPayload):
    token = get_setting("github_token", "")
    if not token:
        raise HTTPException(status_code=400, detail="GitHub Token is not configured in Settings.")

    # Determine repo name
    repo_name = f"bottled-{payload.name}" if not payload.name.startswith("bottled-") else payload.name
    desc = payload.description or f"Cloud in a Bottle wrapper for {payload.title}"

    try:
        target_repo_url = await create_or_update_repo(
            token=token,
            repo_name=repo_name,
            description=desc,
            files=payload.files,
        )

        # Save to DB
        with get_db() as conn:
            cursor = conn.execute(
                """
                INSERT OR REPLACE INTO projects (
                    name, title, source_repo, upstream_tag, target_repo_url, cloud_app_name, files_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    payload.name,
                    payload.title,
                    payload.source_repo,
                    payload.upstream_tag,
                    target_repo_url,
                    payload.name,
                    json.dumps(payload.files),
                ),
            )
            project_id = cursor.lastrowid
            conn.commit()

        # Generate catalog TOML if not already provided
        catalog_toml = payload.catalog_toml
        if not catalog_toml:
            catalog_toml = generate_catalog_toml(
                name=payload.name,
                title=payload.title,
                description=desc,
                repo_url=target_repo_url,
            )

        return {
            "status": "ok",
            "project_id": project_id,
            "name": payload.name,
            "repo_url": target_repo_url,
            "install_command": f"openhost app install {target_repo_url}",
            "catalog_toml": catalog_toml,
            "zone_domain": ZONE_DOMAIN,
        }
    except Exception as e:
        logger.exception("Error publishing repo")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/projects")
def list_projects():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
        projects = []
        for r in rows:
            projects.append(
                {
                    "id": r["id"],
                    "name": r["name"],
                    "title": r["title"],
                    "source_repo": r["source_repo"],
                    "upstream_tag": r["upstream_tag"],
                    "target_repo_url": r["target_repo_url"],
                    "cloud_app_name": r["cloud_app_name"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                }
            )
        return projects


@app.post("/api/projects/{project_id}/check-upstream")
async def check_upstream(project_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Project not found")

        source_repo = row["source_repo"]
        current_tag = row["upstream_tag"]

    token = get_setting("github_token", "")
    summary = await fetch_repo_summary(source_repo, token)
    latest_tag = summary.get("latest_tag", "")

    has_update = bool(latest_tag and latest_tag != current_tag)

    return {
        "source_repo": source_repo,
        "current_tag": current_tag,
        "latest_tag": latest_tag,
        "has_update": has_update,
    }


@app.post("/api/catalog/submit")
async def submit_catalog_endpoint(payload: SubmitCatalogPayload):
    token = get_setting("github_token", "")
    if not token:
        raise HTTPException(status_code=400, detail="GitHub Token is required for catalog submission.")

    res = await submit_to_catalog(
        token=token,
        name=payload.name,
        catalog_toml=payload.catalog_toml,
    )
    return res


@app.post("/api/projects/{project_id}/submit-catalog")
async def submit_project_catalog(project_id: int, payload: SubmitCatalogPayload | None = None):
    token = get_setting("github_token", "")
    if not token:
        raise HTTPException(status_code=400, detail="GitHub Token is required for catalog submission.")

    with get_db() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Project not found")

        name = row["name"]
        title = row["title"]
        target_repo_url = row["target_repo_url"]
        catalog_toml = payload.catalog_toml if (payload and payload.catalog_toml) else ""
        if not catalog_toml:
            catalog_toml = generate_catalog_toml(
                name=name,
                title=title,
                description=f"Cloud in a Bottle wrapper for {title}",
                repo_url=target_repo_url,
            )

    res = await submit_to_catalog(
        token=token,
        name=name,
        catalog_toml=catalog_toml,
    )
    return res


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        conn.commit()
    return {"status": "ok"}

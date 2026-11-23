from __future__ import annotations

import asyncio
import base64
import mimetypes
import re
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.agents.boss import run_buddy
from backend.agents.config import MODEL_NAME
from backend.agents.rules import answer_rules_question
from backend.models import BuddyRequest, UserContext

ROOT = Path(__file__).resolve().parents[1]
app = FastAPI(title="Board Game Buddy", version="0.1.0")
app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")
JOBS: dict[str, dict] = {}
TASKS: set[asyncio.Task] = set()


def start_job(work) -> str:
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"status": "running", "result": None, "detail": ""}

    async def run() -> None:
        try:
            JOBS[job_id]["result"] = await work
            JOBS[job_id]["status"] = "done"
        except Exception as exc:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["detail"] = str(exc)[:500]

    task = asyncio.create_task(run())
    TASKS.add(task)
    task.add_done_callback(TASKS.discard)
    return job_id


@app.get("/")
def home() -> FileResponse:
    return FileResponse(ROOT / "frontend" / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": "Board Game Buddy", "model": MODEL_NAME}


@app.post("/api/analyze")
async def analyze(
    photo: UploadFile | None = File(default=None),
    favorites: str = Form(default=""),
    games_to_assess: str = Form(default=""),
    corrected_title: list[str] = Form(default=[]),
    corrected_favorite: list[str] = Form(default=[]),
    player_count: int = Form(default=2),
    shelf_notes: str = Form(default=""),
    expansions: str = Form(default=""),
    variants: str = Form(default=""),
    purchase_preference: str = Form(default="either"),
    purchase_game: str = Form(default=""),
    purchase_search: bool = Form(default=False),
    skip_player_count: bool = Form(default=False),
    zip_code: str = Form(default=""),
    local_store_search: bool = Form(default=False),
) -> dict:
    if player_count < 1 or player_count > 20:
        raise HTTPException(status_code=400, detail="Player count must be between 1 and 20.")
    favorite_indexes = {int(index) for index in corrected_favorite if str(index).isdigit()}
    corrected_names = []
    favorite_names = []
    for index, raw_title in enumerate(corrected_title):
        title = raw_title.strip()
        if not title:
            continue
        corrected_names.append(title)
        if index in favorite_indexes:
            favorite_names.append(title)
    if corrected_names:
        typed_favorites = [part.strip() for part in re.split(r"[,;\n]", favorites) if part.strip()]
        corrected_keys = {re.sub(r"[^a-z0-9]+", "", title.lower()) for title in corrected_names}
        kept_favorites = [
            part for part in typed_favorites
            if re.sub(r"[^a-z0-9]+", "", part.lower()) not in corrected_keys
        ]
        favorites = ", ".join(favorite_names + kept_favorites)
    image_data_url = ""
    if photo and photo.filename:
        raw = await photo.read()
        if not raw:
            raise HTTPException(status_code=400, detail="The uploaded photo is empty.")
        content_type = photo.content_type or mimetypes.guess_type(photo.filename)[0] or "image/jpeg"
        image_data_url = f"data:{content_type};base64,{base64.b64encode(raw).decode('ascii')}"
    async def work() -> dict:
        result = await run_buddy(
            BuddyRequest(
                image_data_url=image_data_url,
                context=UserContext(
                    favorites=favorites.strip(),
                    games_to_assess=games_to_assess.strip(),
                    corrected_titles="\n".join(corrected_names),
                    typical_player_count=player_count,
                    shelf_notes=shelf_notes.strip(),
                    expansions_in_use=expansions.strip(),
                    variants_or_house_rules=variants.strip(),
                    purchase_preference=purchase_preference,
                    purchase_query=purchase_game.strip(),
                    purchase_search_requested=purchase_search,
                    skip_player_count=skip_player_count,
                    zip_code=zip_code.strip(),
                    local_store_search_consent=local_store_search,
                ),
            )
        )
        return result.model_dump()

    return {"job_id": start_job(work()), "status": "running"}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="That result is no longer available. Please submit it again.")
    if job["status"] == "done":
        return {"status": "done", "result": job["result"]}
    if job["status"] == "error":
        return {"status": "error", "detail": job["detail"] or "The analysis failed."}
    return {"status": "running"}


@app.post("/api/rules")
async def rules(
    question: str = Form(default=""),
    game_name: str = Form(default=""),
    rulebook_photo: UploadFile | None = File(default=None),
    expansions: str = Form(default=""),
    variants: str = Form(default=""),
) -> dict:
    if not game_name.strip():
        raise HTTPException(status_code=400, detail="Enter the game name.")
    if not question.strip():
        raise HTTPException(status_code=400, detail="Enter a rules question.")
    image_data_url = ""
    if rulebook_photo and rulebook_photo.filename:
        raw = await rulebook_photo.read()
        if not raw:
            raise HTTPException(status_code=400, detail="The uploaded photo is empty.")
        content_type = rulebook_photo.content_type or "image/jpeg"
        image_data_url = f"data:{content_type};base64,{base64.b64encode(raw).decode('ascii')}"
    async def work() -> dict:
        result = await answer_rules_question(
            question=question,
            game_name=game_name,
            image_data_url=image_data_url,
            expansions=expansions,
            variants=variants,
        )
        return result.model_dump()

    return {"job_id": start_job(work()), "status": "running"}

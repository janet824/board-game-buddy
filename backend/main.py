from __future__ import annotations

import base64
import mimetypes
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
    player_count: int = Form(default=2),
    shelf_notes: str = Form(default=""),
    expansions: str = Form(default=""),
    variants: str = Form(default=""),
    purchase_preference: str = Form(default="either"),
    purchase_game: str = Form(default=""),
    zip_code: str = Form(default=""),
    local_store_search: bool = Form(default=False),
) -> dict:
    if player_count < 1 or player_count > 20:
        raise HTTPException(status_code=400, detail="Player count must be between 1 and 20.")
    image_data_url = ""
    if photo and photo.filename:
        raw = await photo.read()
        if not raw:
            raise HTTPException(status_code=400, detail="The uploaded photo is empty.")
        content_type = photo.content_type or mimetypes.guess_type(photo.filename)[0] or "image/jpeg"
        image_data_url = f"data:{content_type};base64,{base64.b64encode(raw).decode('ascii')}"
    result = await run_buddy(
        BuddyRequest(
            image_data_url=image_data_url,
            context=UserContext(
                favorites=favorites.strip(),
                games_to_assess=games_to_assess.strip(),
                typical_player_count=player_count,
                shelf_notes=shelf_notes.strip(),
                expansions_in_use=expansions.strip(),
                variants_or_house_rules=variants.strip(),
                purchase_preference=purchase_preference,
                purchase_query=purchase_game.strip(),
                zip_code=zip_code.strip(),
                local_store_search_consent=local_store_search,
            ),
        )
    )
    return result.model_dump()


@app.post("/api/rules")
async def rules(
    question: str = Form(default=""),
    rulebook_photo: UploadFile | None = File(default=None),
    expansions: str = Form(default=""),
    variants: str = Form(default=""),
) -> dict:
    if not question.strip():
        raise HTTPException(status_code=400, detail="Enter a rules question.")
    if not rulebook_photo or not rulebook_photo.filename:
        raise HTTPException(status_code=400, detail="Upload a rulebook or card photo.")
    raw = await rulebook_photo.read()
    if not raw:
        raise HTTPException(status_code=400, detail="The uploaded photo is empty.")
    content_type = rulebook_photo.content_type or "image/jpeg"
    image_data_url = f"data:{content_type};base64,{base64.b64encode(raw).decode('ascii')}"
    result = await answer_rules_question(
        question=question,
        image_data_url=image_data_url,
        expansions=expansions,
        variants=variants,
    )
    return result.model_dump()

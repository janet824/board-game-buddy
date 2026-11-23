from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class UserContext(BaseModel):
    favorites: str = ""
    games_to_assess: str = ""
    typical_player_count: int = Field(default=2, ge=1, le=20)
    shelf_notes: str = ""
    expansions_in_use: str = ""
    variants_or_house_rules: str = ""
    purchase_preference: str = "either"
    purchase_query: str = ""
    zip_code: str = ""
    local_store_search_consent: bool = False


class BoardGame(BaseModel):
    title: str
    confidence: str = "medium"
    shelf_group: str = "unknown"
    evidence: str = ""


class ShelfReport(BaseModel):
    games: list[BoardGame] = []
    observations: list[str] = []
    uncertainty: list[str] = []


class WorkerReport(BaseModel):
    worker: str
    summary: str
    recommendations: list[dict[str, Any]] = []
    caveats: list[str] = []
    metadata: dict[str, Any] = {}


class BuddyRequest(BaseModel):
    image_data_url: str = ""
    context: UserContext = UserContext()


class BuddyResponse(BaseModel):
    answer: str
    shelf_report: ShelfReport
    worker_reports: list[WorkerReport]
    agents_used: list[str]


class RulesResponse(BaseModel):
    answer: str
    ruling: str
    steps: list[str] = []
    official_basis: list[str] = []
    uncertainties: list[str] = []
    sources: list[dict[str, str]] = []
    agents_used: list[str] = ["Board Game Buddy", "Rules Helper"]

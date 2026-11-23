from __future__ import annotations

import re

from backend.agents.workers import interpret_shelf, run_workers
from backend.models import BoardGame, BuddyRequest, BuddyResponse, ShelfReport, UserContext, WorkerReport

AREA_WORDS = {
    "top": ("top", "upper", "highest"),
    "upper": ("top", "upper", "highest"),
    "highest": ("top", "upper", "highest"),
    "bottom": ("bottom", "lower", "lowest"),
    "lower": ("bottom", "lower", "lowest"),
    "lowest": ("bottom", "lower", "lowest"),
    "second": ("second", "middle"),
    "middle": ("second", "middle"),
    "third": ("third",),
    "left": ("left",),
    "right": ("right",),
}


def _contains_word(text: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


def _favorite_area_words(notes: str) -> set[str]:
    words: set[str] = set()
    for clause in re.split(r"[;\n]| and ", notes.lower()):
        if "fav" not in clause:
            continue
        for key, synonyms in AREA_WORDS.items():
            if _contains_word(clause, key):
                words.update(synonyms)
    return words


def apply_noted_favorites(shelf: ShelfReport, context: UserContext) -> None:
    """Mark games as favorites when the shelf note says that area is the favorites."""
    area_words = _favorite_area_words(context.shelf_notes)
    titles = []
    for game in shelf.games:
        group = game.shelf_group.lower()
        described_area = f"{group} {game.evidence}".lower()
        if "fav" in group or any(_contains_word(described_area, word) for word in area_words):
            game.shelf_group = "favorite"
            titles.append(game.title)
    if not titles:
        return
    existing = [part.strip() for part in re.split(r"[,;\n]", context.favorites) if part.strip()]
    existing_keys = {re.sub(r"[^a-z0-9]+", "", part.lower()) for part in existing}
    for title in titles:
        key = re.sub(r"[^a-z0-9]+", "", title.lower())
        if key and key not in existing_keys:
            existing.append(title)
            existing_keys.add(key)
    context.favorites = ", ".join(existing)


def compose_answer(shelf: ShelfReport, reports: list[WorkerReport], has_photo: bool, corrected: bool = False) -> str:
    visible = [game.title for game in shelf.games]
    recommendation_report = next(
        (report for report in reports if report.worker == "Games for you"),
        None,
    )
    recommendations = [
        item["title"].removesuffix(" — candidate").removesuffix(" - candidate")
        for item in (recommendation_report.recommendations if recommendation_report else [])
        if item.get("title")
    ]
    detected = ", ".join(visible[:12]) if visible else (
        "No titles were detected from a shelf photo" if has_photo
        else "No shelf photo was provided"
    )
    purchase_report = next((report for report in reports if report.worker == "Purchase Advisor"), None)
    if purchase_report and recommendation_report is None:
        return purchase_report.summary
    suggestions = ", ".join(dict.fromkeys(recommendations)) or "No recommendations were returned"
    review_note = (
        "You can correct any name below if one is still wrong."
        if corrected or has_photo
        else "You can add a shelf photo later if you want the collection interpreted automatically."
    )
    if corrected:
        opening = "I used the names and favorites you corrected. "
    elif has_photo:
        favorite_titles = [game.title for game in shelf.games if game.shelf_group == "favorite"]
        opening = f"I found {len(visible)} possible game(s) in the photo. "
        if favorite_titles:
            opening += f"I used these as your favorites: {', '.join(favorite_titles)}. "
    else:
        opening = "I used your typed favorites without a shelf photo. "
    return opening + f"Here are five games to explore: {suggestions}. Five newer games are listed separately, and none of these are games you already own. " + review_note


async def run_buddy(request: BuddyRequest) -> BuddyResponse:
    corrected = [title.strip() for title in request.context.corrected_titles.splitlines() if title.strip()]
    if corrected:
        favorite_keys = {
            re.sub(r"[^a-z0-9]+", "", part.lower())
            for part in re.split(r"[,;\n]", request.context.favorites)
            if part.strip()
        }
        shelf = ShelfReport(
            games=[
                BoardGame(
                    title=title,
                    confidence="high",
                    shelf_group="favorite" if re.sub(r"[^a-z0-9]+", "", title.lower()) in favorite_keys else "owned",
                    evidence="Corrected by the user",
                )
                for title in corrected
            ],
            observations=["The user corrected the game names and favorites."],
        )
    elif request.context.purchase_search_requested or not request.image_data_url:
        shelf = ShelfReport(
            observations=[] if request.context.purchase_search_requested else ["No shelf photo was provided."],
            uncertainty=[] if request.context.purchase_search_requested else ["Upload a shelf photo so the shelf interpreter can identify games."],
        )
    else:
        shelf = await interpret_shelf(request.image_data_url, request.context)
        apply_noted_favorites(shelf, request.context)
    reports = await run_workers(shelf, request.context)
    return BuddyResponse(
        answer=compose_answer(shelf, reports, bool(request.image_data_url), bool(corrected)),
        shelf_report=shelf,
        worker_reports=reports,
        agents_used=(
            ["Board Game Buddy", "Player Count Advisor", "Purchase Advisor"]
            if request.context.purchase_search_requested
            else ["Board Game Buddy", "Shelf Interpreter", "Games for you", "New games"]
        ),
    )

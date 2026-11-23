from __future__ import annotations

from backend.agents.workers import interpret_shelf, run_workers
from backend.models import BoardGame, BuddyRequest, BuddyResponse, ShelfReport, WorkerReport


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
        opening = f"I used the names you corrected: {detected}. "
    elif has_photo:
        opening = f"I found {len(visible)} possible game(s) in the photo: {detected}. "
    else:
        opening = f"I used your typed preferences without a shelf photo: {detected}. "
    return opening + f"Here are five games to explore: {suggestions}. Five newer games are listed separately, and none of these are games you already own. " + review_note


async def run_buddy(request: BuddyRequest) -> BuddyResponse:
    corrected = [title.strip() for title in request.context.corrected_titles.splitlines() if title.strip()]
    if corrected:
        shelf = ShelfReport(
            games=[BoardGame(title=title, confidence="high", shelf_group="corrected", evidence="Corrected by the user") for title in corrected],
            observations=["The user corrected the game names."],
        )
    elif request.context.purchase_search_requested or not request.image_data_url:
        shelf = ShelfReport(
            observations=[] if request.context.purchase_search_requested else ["No shelf photo was provided."],
            uncertainty=[] if request.context.purchase_search_requested else ["Upload a shelf photo so the shelf interpreter can identify games."],
        )
    else:
        shelf = await interpret_shelf(request.image_data_url, request.context)
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

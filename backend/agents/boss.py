from __future__ import annotations

from backend.agents.workers import interpret_shelf, run_workers
from backend.models import BuddyRequest, BuddyResponse, ShelfReport, WorkerReport


def compose_answer(shelf: ShelfReport, reports: list[WorkerReport], has_photo: bool) -> str:
    visible = [game.title for game in shelf.games]
    recommendation_report = next(
        (report for report in reports if report.worker == "Game Recommender"),
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
    suggestions = ", ".join(dict.fromkeys(recommendations)) or "No recommendations were returned"
    review_note = (
        "Review the detected titles before treating the collection as final."
        if has_photo
        else "You can add a shelf photo later if you want the collection interpreted automatically."
    )
    return (
        f"{'I found ' + str(len(visible)) + ' possible game(s) in the photo: ' if has_photo else 'I used your typed preferences without a shelf photo: '}"
        f"{detected}. "
        f"Based on your favorites and player count, start by exploring: {suggestions}. "
        f"{review_note}"
    )


async def run_buddy(request: BuddyRequest) -> BuddyResponse:
    if not request.image_data_url:
        shelf = ShelfReport(
            observations=["No shelf photo was provided."],
            uncertainty=["Upload a shelf photo so the shelf interpreter can identify games."],
        )
    else:
        shelf = await interpret_shelf(request.image_data_url, request.context)
    reports = await run_workers(shelf, request.context)
    return BuddyResponse(
        answer=compose_answer(shelf, reports, bool(request.image_data_url)),
        shelf_report=shelf,
        worker_reports=reports,
        agents_used=["Board Game Buddy", "Shelf Interpreter", "Game Recommender", "Player Count Advisor"],
    )

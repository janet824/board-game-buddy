from __future__ import annotations

import asyncio
import re

from backend.agents.config import ask_json
from backend.agents.purchase import advise_purchases
from backend.agents.rules import verify_sources
from backend.models import ShelfReport, UserContext, WorkerReport

SHELF_SCHEMA = {
    "type": "object",
    "properties": {
        "games": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "shelf_group": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["title", "confidence", "shelf_group", "evidence"],
                "additionalProperties": False,
            },
        },
        "observations": {"type": "array", "items": {"type": "string"}},
        "uncertainty": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["games", "observations", "uncertainty"],
    "additionalProperties": False,
}

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "reason": {"type": "string"},
                    "fit": {"type": "string"},
                },
                "required": ["title", "reason", "fit"],
                "additionalProperties": False,
            },
        },
        "caveats": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "recommendations", "caveats"],
    "additionalProperties": False,
}

PLAYER_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "publisher_range": {"type": "string"},
                    "official_recommendation": {"type": "string"},
                    "optimal_player_count": {"type": "string"},
                    "reason": {"type": "string"},
                    "setup_burden": {"type": "string"},
                    "play_mode": {"type": "string"},
                    "digital_options": {"type": "string"},
                },
                "required": [
                    "title", "publisher_range", "official_recommendation",
                    "optimal_player_count", "reason",
                    "setup_burden", "play_mode", "digital_options",
                ],
                "additionalProperties": False,
            },
        },
        "caveats": {"type": "array", "items": {"type": "string"}},
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_type": {"type": "string"},
                    "title": {"type": "string"},
                    "version": {"type": "string"},
                    "page": {"type": "string"},
                    "url": {"type": "string"},
                    "url_status": {"type": "string"},
                },
                "required": ["source_type", "title", "version", "page", "url", "url_status"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "recommendations", "caveats", "sources"],
    "additionalProperties": False,
}


async def interpret_shelf(image_data_url: str, context: UserContext) -> ShelfReport:
    result = await ask_json(
        system=(
            "You are the shelf interpreter for Board Game Buddy. Read board-game box "
            "spines and covers conservatively. Never invent a title. Preserve the user's "
            "semantic shelf groups from shelf_notes. If a title is uncertain, include it "
            "with low confidence and explain the visual evidence."
        ),
        prompt=(
            f"Identify visible board games in this shelf photo. User notes: {context.shelf_notes or 'none'} "
            "Return one entry per distinct game, not per box when an expansion is obvious. "
            "Use shelf_group values such as favorite, unplayed, evaluating, or unknown."
        ),
        schema_name="shelf_report",
        schema=SHELF_SCHEMA,
        image_data_url=image_data_url,
    )
    return ShelfReport.model_validate(result)


async def recommend_games(shelf: ShelfReport, context: UserContext) -> WorkerReport:
    titles = ", ".join(game.title for game in shelf.games)
    result = await ask_json(
        system=(
            "You are the recommendation worker. Recommend board games based on the "
            "collection, favorites, and practical player count. Use collaborative filtering "
            "as the default: reason from games that players with similar collections and "
            "favorites also tend to enjoy, rather than matching only mechanics or themes. "
            "This is a cold-start profile, so label recommendations as candidates until "
            "the user rates them. Do not claim live prices or current availability. "
            "Explain the match in plain language."
        ),
        prompt=(
            f"Owned or photographed games: {titles or 'unknown'}. "
            f"Favorite games: {context.favorites or 'not provided'}. "
            f"Typical player count: {context.typical_player_count}. "
            f"Purchase preference: {context.purchase_preference}. "
            f"Local-store search consent: {context.local_store_search_consent}; "
            f"ZIP code supplied: {'yes' if context.zip_code else 'no'}. "
            "Do not recommend any game already in the owned collection. Suggest up to five "
            "games the user may not own. For each one, "
            "briefly identify the collaborative-filtering signal or explain in plain "
            "English that this is an early recommendation based on limited profile data. "
            "Do not append labels such as 'candidate' to game titles."
        ),
        schema_name="recommendation_report",
        schema=REPORT_SCHEMA,
    )
    return WorkerReport(worker="Game Recommender", **result)


async def assess_player_count(
    shelf: ShelfReport, context: UserContext, *, purchase_game: str = ""
) -> WorkerReport:
    titles = purchase_game or ", ".join(game.title for game in shelf.games) or context.games_to_assess
    task = (
        f"The user typed one game they may buy: {purchase_game}. Assess only that game. "
        "Do not call it a detected collection, shelf result, or missing game."
        if purchase_game else
        "Assess the named games and explain how each fits the usual group."
    )
    result = await ask_json(
        system=(
            "You are the player-count worker. Evaluate whether games are practical for "
            "the user's usual group, distinguishing publisher range from community-reported "
            "best counts. Check rulebooks and publisher materials for explicit recommended "
            "player counts in addition to the supported range. Consider BoardGameGeek "
            "discussions, reputable reviews, and "
            "player reports as community evidence, never as official rules. Also assess "
            "setup time, tedious bookkeeping, rules overhead, and whether digital play "
            "would reduce friction. Do not claim a digital implementation exists unless "
            "you can name it. Use plain English and never mention a detected collection "
            "when the user typed the game."
        ),
        prompt=(
            f"Games to assess: {titles or 'unknown'}. "
            f"The user usually plays with {context.typical_player_count} player(s). "
            f"{task} "
            "For each, report publisher_range, official_recommendation, "
            "optimal_player_count, setup_burden, "
            "play_mode ('physical', 'digital', or 'both'), and named digital_options. "
            "Include sources only when their URLs are trustworthy; never invent a page "
            "number or link."
        ),
        schema_name="player_count_report",
        schema=PLAYER_SCHEMA,
    )
    sources = await verify_sources(result.pop("sources"))
    if purchase_game:
        sentences = re.split(r"(?<=[.!?])\s+", result.get("summary", ""))
        kept = [
            sentence for sentence in sentences
            if "detected collection" not in sentence.lower() and "detected game" not in sentence.lower()
        ]
        result["summary"] = " ".join(kept).strip() or f"Here is how {purchase_game} fits different player counts."
    return WorkerReport(worker="Player Count Advisor", metadata={"sources": sources}, **result)


async def run_workers(shelf: ShelfReport, context: UserContext) -> list[WorkerReport]:
    if context.purchase_search_requested:
        query = context.purchase_query.strip()
        if not query:
            return [WorkerReport(
                worker="Purchase Advisor",
                summary="Type the game you want to buy, then search again.",
            )]
        player_context = context.model_copy(update={"games_to_assess": query})
        player_report, purchase_report = await asyncio.gather(
            assess_player_count(ShelfReport(), player_context, purchase_game=query),
            advise_purchases(titles=[query], owned_titles=[], context=context),
        )
        return [player_report, purchase_report]
    return list(await asyncio.gather(
        recommend_games(shelf, context),
        assess_player_count(shelf, context),
    ))

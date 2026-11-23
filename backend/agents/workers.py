from __future__ import annotations

import asyncio
import re
from urllib.parse import urlparse

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
                    "play_choice": {"type": "string", "enum": ["online", "physical"]},
                    "play_link": {"type": "string"},
                    "play_label": {"type": "string"},
                },
                "required": ["title", "reason", "fit", "play_choice", "play_link", "play_label"],
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
                },
                "required": [
                    "title", "publisher_range", "official_recommendation",
                    "optimal_player_count", "reason", "setup_burden",
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
            "with low confidence and explain the visual evidence. When the notes say an "
            "area holds favorites, set shelf_group to exactly favorite for every game in "
            "that area and name the area in evidence, such as 'top shelf'."
        ),
        prompt=(
            f"Identify visible board games in this shelf photo. User notes: {context.shelf_notes or 'none'} "
            "Return one entry per distinct game, not per box when an expansion is obvious. "
            "If the notes say the top shelf, upper shelf, or another area is the favorites, "
            "those games must use shelf_group 'favorite' and evidence must name that area. "
            "Use shelf_group values favorite, unplayed, evaluating, owned, or unknown."
        ),
        schema_name="shelf_report",
        schema=SHELF_SCHEMA,
        image_data_url=image_data_url,
    )
    return ShelfReport.model_validate(result)


PAIR_SCHEMA = {
    "type": "object",
    "properties": {
        "overall_summary": {"type": "string"},
        "overall": REPORT_SCHEMA["properties"]["recommendations"],
        "new_summary": {"type": "string"},
        "new_releases": REPORT_SCHEMA["properties"]["recommendations"],
    },
    "required": ["overall_summary", "overall", "new_summary", "new_releases"],
    "additionalProperties": False,
}


def _clean_title(title: str) -> str:
    return title.removesuffix(" — candidate").removesuffix(" - candidate").strip()


def _key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", title.lower())


DIGITAL_HOSTS = (
    "boardgamearena.com",
    "tabletopia.com",
    "store.steampowered.com",
    "steampowered.com",
    "apps.apple.com",
    "play.google.com",
    "yucata.de",
    "boardgamecore.net",
    "boiteajeux.net",
)


def _keep_play_choice(item: dict) -> dict:
    link = item.get("play_link", "").strip()
    if item.get("play_choice") == "online" and _digital_link(link):
        item["play_choice"] = "online"
        item["play_link"] = link
        item["play_label"] = item.get("play_label", "").strip() or "Play online"
        return item
    item["play_choice"] = "physical"
    item["play_link"] = ""
    item["play_label"] = ""
    return item


def _digital_link(url: str) -> bool:
    if not url.startswith("https://"):
        return False
    try:
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return False
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in DIGITAL_HOSTS)


def _favorite_titles(shelf: ShelfReport, context: UserContext) -> list[str]:
    titles = [part.strip() for part in re.split(r"[,;\n]", context.favorites) if part.strip()]
    seen = {_key(title) for title in titles}
    for game in shelf.games:
        if "fav" not in game.shelf_group.lower():
            continue
        key = _key(game.title)
        if key and key not in seen:
            titles.append(game.title)
            seen.add(key)
    return titles


def _summary_with_favorites(summary: str, favorites: list[str]) -> str:
    if not favorites:
        return summary
    cleaned = re.sub(
        r"[^.?!]*(?:no favorites|without (?:any )?favorites|favorites were not|no favorite games|not selected any favorites)[^.?!]*[.?!]?",
        "",
        summary or "",
        flags=re.IGNORECASE,
    ).strip()
    lead = f"These suggestions are based on your favorites: {', '.join(favorites)}."
    return f"{lead} {cleaned}".strip()


async def recommend_games(shelf: ShelfReport, context: UserContext) -> list[WorkerReport]:
    titles = ", ".join(game.title for game in shelf.games)
    favorites = _favorite_titles(shelf, context)
    result = await ask_json(
        system=(
            "You are the recommendation worker. Recommend board games based on the "
            "collection, favorites, and practical player count. Use collaborative filtering "
            "as the default: reason from games that players with similar collections and "
            "favorites also tend to enjoy, rather than matching only mechanics or themes. "
            "Do not claim live prices or current availability. "
            "Explain the match in plain language. Never add the word candidate to a title. "
            "Favorites are the user's taste even when they already own those games. "
            "For each game, choose online only when a real digital version is easier than "
            "owning the physical game. Otherwise choose physical."
        ),
        prompt=(
            f"Owned or photographed games: {titles or 'unknown'}. "
            f"Shelf groups: {', '.join(f'{game.title} ({game.shelf_group})' for game in shelf.games) or 'unknown'}. "
            f"Favorite games: {', '.join(favorites) or 'not provided'}. "
            "The favorite games are the taste to match, including games marked favorite from the shelf notes. "
            "Recommend different games that people who enjoy those favorites also tend to enjoy. "
            "Do not say that no favorites were selected when favorite games are listed. "
            f"Typical player count: {context.typical_player_count}. "
            f"Purchase preference: {context.purchase_preference}. "
            f"Local-store search consent: {context.local_store_search_consent}; "
            f"ZIP code supplied: {'yes' if context.zip_code else 'no'}. "
            "Return exactly five overall recommendations and exactly five different new games. "
            "Do not recommend a game the user already owns. The new games "
            "must have been released within the past 12 months or be an active Kickstarter or "
            "Gamefound campaign, and none of them may repeat an overall recommendation. "
            "Explain each match in plain English. Do not append labels such as 'candidate' to titles. "
            "For every game set play_choice to online or physical. Choose online only when "
            "setup, bookkeeping, or rules overhead makes a digital version the better way "
            "to play, and play_link is the direct https page for that game on Board Game "
            "Arena, Steam, Tabletopia, Yucata, Board Game Core, Boîte à Jeux, the App Store, "
            "or Google Play. play_label is the platform name. When physical is better, set "
            "play_choice to physical and leave play_link and play_label empty. Never invent a platform or URL."
        ),
        schema_name="recommendation_pair",
        schema=PAIR_SCHEMA,
    )
    owned = {_key(game.title) for game in shelf.games}
    owned.update(_key(part) for part in re.split(r"[,;\n]", context.favorites) if part.strip())
    overall = []
    for item in result["overall"]:
        item["title"] = _clean_title(item.get("title", ""))
        if item["title"] and _key(item["title"]) not in owned and _key(item["title"]) not in {_key(x["title"]) for x in overall}:
            overall.append(_keep_play_choice(item))
        if len(overall) == 5:
            break
    chosen = owned | {_key(item["title"]) for item in overall}
    new_releases = []
    for item in result["new_releases"]:
        item["title"] = _clean_title(item.get("title", ""))
        if item["title"] and _key(item["title"]) not in chosen:
            new_releases.append(_keep_play_choice(item))
            chosen.add(_key(item["title"]))
        if len(new_releases) == 5:
            break
    return [
        WorkerReport(worker="Games for you", summary=_summary_with_favorites(result["overall_summary"], favorites), recommendations=overall, caveats=[]),
        WorkerReport(worker="New games", summary=_summary_with_favorites(result["new_summary"], favorites), recommendations=new_releases, caveats=[]),
    ]


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
            "setup time, tedious bookkeeping, and rules overhead. Use plain English and "
            "never mention a detected collection when the user typed the game."
        ),
        prompt=(
            f"Games to assess: {titles or 'unknown'}. "
            f"The user usually plays with {context.typical_player_count} player(s). "
            f"{task} "
            "For each, report publisher_range, official_recommendation, "
            "optimal_player_count, and setup_burden. "
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
        if context.skip_player_count:
            return [await advise_purchases(titles=[query], owned_titles=[], context=context)]
        player_context = context.model_copy(update={"games_to_assess": query})
        player_report, purchase_report = await asyncio.gather(
            assess_player_count(ShelfReport(), player_context, purchase_game=query),
            advise_purchases(titles=[query], owned_titles=[], context=context),
        )
        return [player_report, purchase_report]
    return await recommend_games(shelf, context)

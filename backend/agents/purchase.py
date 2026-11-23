from __future__ import annotations

from urllib.parse import urlparse

import httpx

from backend.agents.config import ask_json
from backend.models import UserContext, WorkerReport

PURCHASE_HOSTS = (
    "amazon.com",
    "boardgamegeek.com",
    "boardlandia.com",
    "gamenerdz.com",
    "miniaturemarket.com",
    "nobleknight.com",
    "target.com",
    "walmart.com",
    "gamefound.com",
    "kickstarter.com",
)

PURCHASE_SCHEMA = {
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
                    "primary_url": {"type": "string"},
                    "secondary_url": {"type": "string"},
                    "big_box_url": {"type": "string"},
                    "big_box_store": {"type": "string"},
                    "big_box_price": {"type": "string"},
                    "big_box_availability": {"type": "string"},
                    "store_name": {"type": "string"},
                    "price": {"type": "string"},
                    "availability": {"type": "string"},
                    "local_search_note": {"type": "string"},
                },
                "required": [
                    "title", "reason", "fit", "primary_url",
                    "secondary_url", "store_name", "price", "availability",
                    "big_box_url", "big_box_store", "big_box_price",
                    "big_box_availability",
                    "local_search_note",
                ],
                "additionalProperties": False,
            },
        },
        "caveats": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "recommendations", "caveats"],
    "additionalProperties": False,
}


async def advise_purchases(
    titles: list[str], owned_titles: list[str], context: UserContext
) -> WorkerReport:
    result = await ask_json(
        system=(
            "You are the Purchase Advisor for Board Game Buddy. Recommend safe purchase "
            "paths for games the player does not own. Never claim a current price, stock "
            "status, or local availability unless a source explicitly provides it. Prefer "
            "publisher or campaign links for Kickstarter/exclusive games. For ordinary "
            "retail games, provide a primary retailer listing and a secondary value-search "
            "link. Use only HTTPS links on recognized retailers, BoardGameGeek, Kickstarter, "
            "or Gamefound. If you cannot provide a trustworthy direct link, leave it blank."
        ),
        prompt=(
            f"Candidate games: {', '.join(titles) or 'none'}\n"
            f"Owned games to exclude: {', '.join(owned_titles) or 'none'}\n"
            f"Specific purchase request: {context.purchase_query or 'none'}\n"
            f"Condition preference: {context.purchase_preference}\n"
            f"Local search consent: {context.local_store_search_consent}\n"
            f"ZIP code: {context.zip_code or 'not provided'}\n"
            "Return no owned games. If the specific purchase request is a game family "
            "with multiple editions or expansions (for example Azul or Catan), do not "
            "search yet unless the player selected 'any version'. If 'any version' was "
            "selected, list every relevant verified version or expansion found, clearly "
            "labeling base games, standalone editions, and expansions. Otherwise set the "
            "summary to a clarification question asking whether the player means any "
            "version, the base game, or a named expansion. A "
            "local_search_note must say what still needs to be checked if no local store "
            "website was supplied. Include the store name, displayed price, and displayed "
            "availability exactly as found; if any is unknown, say 'not verified'. Also "
            "include a third big-box option from Target, Walmart, or a comparable trusted "
            "retailer when one can be verified; otherwise leave its fields blank."
        ),
        schema_name="purchase_report",
        schema=PURCHASE_SCHEMA,
    )
    recommendations = await verify_links(result["recommendations"])
    return WorkerReport(
        worker="Purchase Advisor",
        summary=result["summary"],
        recommendations=recommendations,
        caveats=result["caveats"],
    )


async def verify_links(items: list[dict]) -> list[dict]:
    async with httpx.AsyncClient(follow_redirects=True, timeout=5) as http:
        for item in items:
            for key in ("primary_url", "secondary_url", "big_box_url"):
                url = item.get(key, "").strip()
                if not url:
                    continue
                try:
                    host = (urlparse(url).hostname or "").lower()
                except ValueError:
                    item[key] = ""
                    continue
                trusted = any(host == allowed or host.endswith(f".{allowed}") for allowed in PURCHASE_HOSTS)
                if not trusted or not url.startswith("https://"):
                    item[key] = ""
                    continue
                try:
                    response = await http.head(url)
                    if response.status_code >= 400:
                        response = await http.get(url)
                    if response.status_code >= 400:
                        item[key] = ""
                except (httpx.HTTPError, ValueError):
                    item[key] = ""
    return items

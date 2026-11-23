from __future__ import annotations

from urllib.parse import quote_plus, urlparse

import httpx

from backend.agents.config import ask_json
from backend.models import UserContext, WorkerReport

PURCHASE_HOSTS = (
    "amazon.com",
    "ebay.com",
    "barnesandnoble.com",
    "bestbuy.com",
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
            "link. Use only HTTPS links on Amazon, eBay, Walmart, Target, Barnes & Noble, "
            "Best Buy, BoardGameGeek, Kickstarter, or Gamefound. If you cannot provide a trustworthy direct link, leave it blank."
        ),
        prompt=(
            f"Game to buy: {context.purchase_query or ', '.join(titles) or 'none'}\n"
            f"Owned games to exclude: {', '.join(owned_titles) or 'none'}\n"
            f"Condition preference: {context.purchase_preference}\n"
            f"Local search consent: {context.local_store_search_consent}\n"
            f"ZIP code: {context.zip_code or 'not provided'}\n"
            "Search only for the game named above. A single title such as Carnegie is "
            "specific enough to search immediately. Do not add games from an earlier "
            "recommendation. Return no owned games. If the request is a broad game family "
            "with multiple editions or expansions (for example Azul or Catan), do not "
            "search yet unless the player selected 'any version'. If 'any version' was "
            "selected, list every relevant verified version or expansion found, clearly "
            "labeling base games, standalone editions, and expansions. Otherwise set the "
            "summary to a clarification question asking whether the player means any "
            "version, the base game, or a named expansion. A "
            "local_search_note must say what still needs to be checked if no local store "
            "website was supplied. Include the store name, displayed price, and displayed "
            "availability exactly as found. Leave any unknown price, store, or stock field blank. Also "
            "include a third big-box option from Target, Walmart, or a comparable trusted "
            "retailer when one can be verified; otherwise leave its fields blank."
        ),
        schema_name="purchase_report",
        schema=PURCHASE_SCHEMA,
    )
    recommendations = await verify_links(result["recommendations"])
    game = context.purchase_query.strip() or (titles[0] if titles else "")
    recommendations = [add_store_searches(item, game) for item in recommendations] or [add_store_searches({}, game)]
    return WorkerReport(
        worker="Purchase Advisor",
        summary=result["summary"],
        recommendations=recommendations,
        caveats=result["caveats"],
    )


def add_store_searches(item: dict, game: str) -> dict:
    """Add trusted major-retailer searches when a direct product page is unavailable."""
    if not game:
        return item
    query = quote_plus(f"{game} board game")
    retailers = (
        ("Amazon", f"https://www.amazon.com/s?k={query}"),
        ("eBay", f"https://www.ebay.com/sch/i.html?_nkw={query}"),
        ("Walmart", f"https://www.walmart.com/search?q={query}"),
        ("Target", f"https://www.target.com/s?searchTerm={query}"),
        ("Barnes & Noble", f"https://www.barnesandnoble.com/s/{query}"),
        ("Best Buy", f"https://www.bestbuy.com/site/searchpage.jsp?st={query}"),
    )
    item.setdefault("title", game)
    item.setdefault("reason", "Compare trusted major retailers, then confirm the edition before buying.")
    item.setdefault("fit", "purchase search")
    item["primary_url"] = item.get("primary_url") or retailers[0][1]
    item["secondary_url"] = item.get("secondary_url") or retailers[1][1]
    item["big_box_url"] = item.get("big_box_url") or retailers[3][1]
    item["store_name"] = item.get("store_name") or "Major retailers"
    item["big_box_store"] = item.get("big_box_store") or "Target"
    item["retailer_links"] = [{"store": name, "url": url} for name, url in retailers]
    item["local_search_note"] = "Each button opens a major retailer's search. Check the exact edition, condition, price, and stock before you buy."
    return item


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

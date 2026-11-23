from __future__ import annotations

import re
from urllib.parse import urlparse

from backend.agents.config import MODEL_NAME, ask_json, client
from backend.models import UserContext, WorkerReport

AMBIGUOUS_FAMILIES = (
    "azul",
    "catan",
    "wingspan",
    "dune",
    "monopoly",
    "ticket to ride",
    "everdell",
    "exploding kittens",
)
CHANNELS = ("Online", "Local", "Big box")
SHORTENERS = ("bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "buff.ly", "adf.ly", "tiny.cc")

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
                    "channel": {"type": "string", "enum": ["Online", "Local", "Big box"]},
                    "store_name": {"type": "string"},
                    "price": {"type": "string"},
                    "availability": {"type": "string"},
                    "url": {"type": "string"},
                    "reason": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": [
                    "title", "channel", "store_name", "price", "availability",
                    "url", "reason", "note",
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
    game = context.purchase_query.strip() or (titles[0] if titles else "")
    if not game:
        return WorkerReport(
            worker="Purchase Advisor",
            summary="Type one game to compare prices.",
            recommendations=[],
            caveats=[],
        )
    family = ambiguous_family(game)
    if family:
        return WorkerReport(
            worker="Purchase Advisor",
            summary=(
                f"{family.title()} has more than one edition. Do you want any version, "
                "the base game, or a named expansion?"
            ),
            recommendations=[],
            caveats=["The search waits until the exact edition is clear."],
        )
    research = await research_prices(game, context)
    result = await ask_json(
        system=(
            "You turn purchase research into three options for Board Game Buddy: "
            "the cheapest online listing, one local game store, and one big-box store. "
            "Use only prices, stock, stores, and page links that appear in the research. "
            "Never invent a price or stock count. Leave a field blank when the research "
            "does not show it. Do not use the word trusted. Do not return a search-results page."
        ),
        prompt=(
            f"Game: {game}\n"
            f"Condition preference: {context.purchase_preference}\n"
            f"ZIP code: {context.zip_code or 'not provided'}\n"
            f"Local search allowed: {context.local_store_search_consent}\n"
            f"Research:\n{research}\n\n"
            "Return exactly three recommendations, one each for Online, Local, and Big box. "
            "Online must be the lowest online price found. Big box must be the lower of "
            "Target and Walmart when both prices were found. The summary must say which "
            "displayed price is lowest. For the local store, the note must say to call "
            "before visiting because stock pages go stale. If no ZIP code was allowed, "
            "leave the local store, price, and link blank and say a ZIP code is needed."
        ),
        schema_name="purchase_report",
        schema=PURCHASE_SCHEMA,
    )
    recommendations = order_options(result["recommendations"], game, context)
    return WorkerReport(
        worker="Purchase Advisor",
        summary=with_lowest_price(result["summary"], recommendations),
        recommendations=recommendations,
        caveats=result["caveats"],
    )


def ambiguous_family(query: str) -> str:
    text = re.sub(r"\s+", " ", query.lower()).strip()
    if re.search(r"\bany (version|edition|game)\b", text):
        return ""
    for family in AMBIGUOUS_FAMILIES:
        if text == family or text == f"{family} board game":
            return family
    return ""


async def research_prices(game: str, context: UserContext) -> str:
    if context.local_store_search_consent and context.zip_code:
        local = (
            f"Find one local board-game store within about 25 miles of ZIP {context.zip_code}. "
            "Open its site and report the store name, the page URL, and the displayed price "
            "and stock for this game when the page shows them."
        )
    else:
        local = "Do not look up a local store. Say that a ZIP code and permission are needed."
    prompt = (
        f"Find current board-game prices for {game}. Condition preference: {context.purchase_preference}. "
        "1. Find the cheapest online product page and report the store, displayed price, "
        "stock text, and product-page URL. "
        f"2. {local} "
        "3. Compare Target and Walmart and report the cheaper big-box product page, its "
        "displayed price, stock text, and URL. "
        "Use only prices printed on the pages. If a page does not show a price, say that. "
        "Prefer a product page over a search page."
    )
    try:
        response = await client().responses.create(
            model=MODEL_NAME,
            tools=[{"type": "web_search", "search_context_size": "medium"}],
            input=prompt,
        )
    except Exception as error:
        return f"Price lookup failed ({error.__class__.__name__}). Do not invent prices or stock."
    return response.output_text or "No price research was returned. Do not invent prices or stock."


def order_options(items: list[dict], game: str, context: UserContext) -> list[dict]:
    by_channel = {}
    for item in items:
        channel = item.get("channel")
        if channel in CHANNELS and channel not in by_channel:
            by_channel[channel] = clean_option(item, game)
    if "Local" in by_channel and not (context.local_store_search_consent and context.zip_code):
        by_channel["Local"] = clean_option({
            "channel": "Local",
            "store_name": "",
            "price": "",
            "availability": "",
            "url": "",
            "reason": "A nearby store can be compared after you add a ZIP code and allow the search.",
            "note": "Add a ZIP code and allow nearby store search.",
        }, game)
    for channel in CHANNELS:
        by_channel.setdefault(channel, clean_option({
            "channel": channel,
            "store_name": "",
            "price": "",
            "availability": "",
            "url": "",
            "reason": "No displayed price was found for this option.",
            "note": "Check the store before you buy.",
        }, game))
    local = by_channel["Local"]
    if local.get("store_name") and "call" not in local.get("note", "").lower():
        local["note"] = f"{local.get('note', '').strip()} Call before you visit. Stock pages go stale.".strip()
    return [by_channel[channel] for channel in CHANNELS]


def clean_option(item: dict, game: str) -> dict:
    url = item.get("url", "").strip()
    if not acceptable_store_url(url) or is_search_url(url):
        url = ""
    return {
        "title": game,
        "channel": item.get("channel", ""),
        "store_name": item.get("store_name", "").strip(),
        "price": shown_price(item.get("price", "")),
        "availability": item.get("availability", "").strip(),
        "url": url,
        "reason": item.get("reason", "").strip(),
        "note": item.get("note", "").strip(),
    }


def shown_price(value: str) -> str:
    text = value.strip()
    if re.search(r"not shown|not listed|unknown|n/a|not verified", text, re.I):
        return ""
    return text


def acceptable_store_url(url: str) -> bool:
    if not url.startswith("https://"):
        return False
    try:
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return False
    if "." not in host:
        return False
    return not any(host == blocked or host.endswith(f".{blocked}") for blocked in SHORTENERS)


def is_search_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return True
    path = parsed.path.lower()
    query = parsed.query.lower()
    host = (parsed.hostname or "").lower()
    if "amazon." in host and (path == "/s" or path.startswith("/s/")):
        return True
    if path.startswith("/search") or "searchpage" in path or path.startswith("/sch/"):
        return True
    if "walmart." in host and "search" in path:
        return True
    if "target." in host and (path == "/s" or path.startswith("/s")):
        return True
    return "searchterm=" in query or "_nkw=" in query


def with_lowest_price(summary: str, recommendations: list[dict]) -> str:
    priced = []
    for item in recommendations:
        amount = parse_price(item.get("price", ""))
        if amount is not None and item.get("store_name"):
            priced.append((amount, item))
    if not priced or re.search(r"lowest|cheapest", summary, re.I):
        return summary
    amount, item = min(priced, key=lambda pair: pair[0])
    price = item["price"].strip()
    if not price.startswith("$"):
        price = f"${amount:.2f}"
    return f"Lowest listed price: {price} at {item['store_name']} ({item['channel']}). {summary}".strip()


def parse_price(value: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d{1,2})?)", value.replace(",", ""))
    if not match:
        return None
    return float(match.group(1))

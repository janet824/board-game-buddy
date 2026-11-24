from __future__ import annotations

import re
from urllib.parse import urlparse

from backend.agents.config import MODEL_NAME, ask_json, client, note_usage
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
    recommendations = options_from_research(research, game, context)
    return WorkerReport(
        worker="Purchase Advisor",
        summary=with_lowest_price("Compared the online, local, and big-box prices.", recommendations),
        recommendations=recommendations,
        caveats=["Call the local store before visiting. Stock pages go stale."],
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
            "Open that store's product page for this exact game, not only its homepage. "
            "Report the store name, the product-page URL, and the price printed on that page."
        )
    else:
        local = "Do not look up a local store. Say that a ZIP code and permission are needed."
    prompt = (
        f"Find current board-game prices for {game}. Condition preference: {context.purchase_preference}. "
        "1. Open the cheapest online product page. "
        f"2. {local} "
        "3. Open the cheaper Target or Walmart product page. "
        "Ignore add to cart, quantity, shipping cost, pickup, and delivery. "
        "End with exactly these lines and nothing after them:\n"
        "Online store: NAME\n"
        "Online price: $0.00\n"
        "Online url: https://product-page\n"
        "Local store: NAME\n"
        "Local price: $0.00\n"
        "Local url: https://product-page\n"
        "Big box store: NAME\n"
        "Big box price: $0.00\n"
        "Big box url: https://product-page\n"
        "Use the product page, not a search page. If a price is not shown, write Local price: not shown."
    )
    try:
        response = await client().responses.create(
            model=MODEL_NAME,
            tools=[{"type": "web_search", "search_context_size": "medium"}],
            input=prompt,
        )
        note_usage(response, "purchase_research")
    except Exception as error:
        return f"Price lookup failed ({error.__class__.__name__}). Do not invent prices or stock."
    return response.output_text or "No price research was returned. Do not invent prices or stock."


ONLINE_HOSTS = (
    "amazon.com", "ebay.com", "nobleknight.com", "boardlandia.com", "gamenerdz.com",
    "miniaturemarket.com", "barnesandnoble.com", "coolstuffinc.com", "cardhaus.com",
)
BIG_BOX_HOSTS = ("target.com", "walmart.com", "bestbuy.com")
STORE_NAMES = {
    "amazon.com": "Amazon",
    "ebay.com": "eBay",
    "walmart.com": "Walmart",
    "target.com": "Target",
    "bestbuy.com": "Best Buy",
    "nobleknight.com": "Noble Knight Games",
    "boardlandia.com": "Boardlandia",
    "gamenerdz.com": "Game Nerdz",
    "miniaturemarket.com": "Miniature Market",
    "barnesandnoble.com": "Barnes & Noble",
}


def options_from_research(text: str, game: str, context: UserContext) -> list[dict]:
    by_channel = {channel: blank_option(channel, game) for channel in CHANNELS}
    for channel_name, channel in (("online", "Online"), ("local", "Local"), ("big box", "Big box")):
        by_channel[channel].update(clean_option({
            "channel": channel,
            "store_name": labeled_field(text, channel_name, "store"),
            "price": labeled_field(text, channel_name, "price"),
            "url": labeled_field(text, channel_name, "url"),
        }, game))
    used = {item["url"] for item in by_channel.values() if item["url"]}
    for url in product_urls(text):
        if url in used:
            continue
        channel = channel_for_url(url)
        item = by_channel[channel]
        window = text_before(text, url)
        if not item["url"]:
            item["url"] = url
        if not item["price"]:
            item["price"] = price_in(window)
        if not item["store_name"]:
            item["store_name"] = store_name_for(url)
        used.add(url)
    for item in by_channel.values():
        if not item["price"]:
            item["price"] = price_near_store(text, item["store_name"])
        item["availability"] = ""
        item["reason"] = ""
    local = by_channel["Local"]
    if context.local_store_search_consent and context.zip_code:
        if local["store_name"]:
            local["note"] = "Call before you visit. Stock pages go stale."
    else:
        by_channel["Local"] = clean_option({
            "channel": "Local",
            "store_name": "",
            "price": "",
            "url": "",
            "note": "Add a ZIP code and allow nearby store search.",
        }, game)
    return [by_channel[channel] for channel in CHANNELS]


def blank_option(channel: str, game: str) -> dict:
    return clean_option({"channel": channel, "store_name": "", "price": "", "url": "", "note": ""}, game)


def labeled_field(text: str, channel: str, name: str) -> str:
    match = re.search(rf"(?im)^{channel} {name}:\s*(.+)$", text)
    return match.group(1).strip() if match else ""


def product_urls(text: str) -> list[str]:
    urls = []
    for raw in re.findall(r"https://[^\s)>\]]+", text):
        url = raw.rstrip(".,;")
        if acceptable_store_url(url) and not is_search_url(url) and url not in urls:
            urls.append(url)
    return urls


def channel_for_url(url: str) -> str:
    host = hostname(url)
    if any(host == domain or host.endswith(f".{domain}") for domain in BIG_BOX_HOSTS):
        return "Big box"
    if any(host == domain or host.endswith(f".{domain}") for domain in ONLINE_HOSTS):
        return "Online"
    return "Local"


def store_name_for(url: str) -> str:
    host = hostname(url)
    for domain, name in STORE_NAMES.items():
        if host == domain or host.endswith(f".{domain}"):
            return name
    return ""


def hostname(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def text_before(text: str, url: str) -> str:
    index = text.find(url)
    if index < 0:
        return ""
    return text[max(0, index - 240):index]


def price_near_store(text: str, store_name: str) -> str:
    if not store_name:
        return ""
    match = re.search(re.escape(store_name), text, re.I)
    if not match:
        return ""
    start = max(0, match.start() - 80)
    return price_in(text[start:match.end() + 180])


def price_in(text: str) -> str:
    for sentence in re.split(r"[\n.]", text):
        if re.search(r"shipping|add to cart|pickup|delivery|quantity", sentence, re.I):
            continue
        found = shown_price(sentence)
        if found:
            return found
    return ""


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
    match = re.search(r"https://[^\s)>\]]+", item.get("url", ""))
    url = match.group(0).rstrip(".,;") if match else ""
    if not acceptable_store_url(url) or is_search_url(url):
        url = ""
    return {
        "title": game,
        "channel": item.get("channel", ""),
        "store_name": item.get("store_name", "").strip(),
        "price": shown_price(item.get("price", "")),
        "availability": "",
        "url": url,
        "reason": "",
        "note": item.get("note", "").strip(),
    }


def shown_price(value: str) -> str:
    text = value.strip()
    if re.search(r"shipping|add to cart|pickup|delivery|quantity|not shown|not listed|unknown|n/a|not verified", text, re.I):
        return ""
    match = re.search(r"\$\s*(\d+(?:,\d{3})*(?:\.\d{2})?)", text)
    if match:
        return f"${float(match.group(1).replace(',', '')):.2f}"
    if re.fullmatch(r"\d+\.\d{2}", text):
        return f"${float(text):.2f}"
    return ""


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
    return path in {"/s", "/search"} and ("searchterm=" in query or "_nkw=" in query)


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

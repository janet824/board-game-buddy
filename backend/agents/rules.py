from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from backend.agents.config import MODEL_NAME, ask_json, client, note_usage
from backend.models import RulesResponse

SHORTENERS = ("bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "buff.ly", "adf.ly", "tiny.cc")

RULES_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "ruling": {"type": "string"},
        "steps": {"type": "array", "items": {"type": "string"}},
        "official_basis": {"type": "array", "items": {"type": "string"}},
        "uncertainties": {"type": "array", "items": {"type": "string"}},
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
    "required": ["answer", "ruling", "steps", "official_basis", "uncertainties", "sources"],
    "additionalProperties": False,
}

async def answer_rules_question(
    *,
    question: str,
    game_name: str,
    image_data_url: str,
    expansions: str,
    variants: str,
) -> RulesResponse:
    research = await research_rulebook(game_name, question, expansions)
    photo_note = (
        "A photo was also uploaded. It may be a card, player aid, or rulebook page. "
        "Use it as extra evidence. The ruling still comes from the looked-up sources."
        if image_data_url else
        "No photo was uploaded. Answer from the looked-up sources."
    )
    result = await ask_json(
        system=(
            "You are the Rules Helper for Board Game Buddy. Whenever a game name and "
            "question are given, answer from the looked-up rules sources for that game. "
            "Do this whether the player uploaded a card, a rulebook page, or nothing. "
            "Use the official rulebook first. Label publisher FAQs separately. Label "
            "BoardGameGeek or other community notes as unofficial. Do not say the "
            "rulebook was not provided. Never invent page numbers, quotations, or URLs."
        ),
        prompt=(
            f"Game: {game_name.strip()}\n"
            f"Player question: {question.strip()}\n"
            f"Expansions in use: {expansions.strip() or 'not specified'}\n"
            f"Variants or house rules: {variants.strip() or 'none stated'}\n"
            f"{photo_note}\n"
            f"Rules research from the official rulebook, publisher materials, and other sources:\n{research}\n"
            "Give a direct ruling first, then a short numbered explanation. Base the "
            "ruling on those sources. In official_basis, cite the rulebook title, "
            "edition, and page only when the research shows them, and keep unofficial "
            "sources out of that list. For every source, identify its type. Never invent "
            "a URL. Use an empty URL and url_status='not provided' when no link is available."
        ),
        schema_name="rules_response",
        schema=RULES_SCHEMA,
        image_data_url=image_data_url,
        usage_label="rules_answer",
    )
    response = RulesResponse.model_validate(result)
    if not research.startswith("Rulebook lookup failed"):
        response.answer = drop_missing_rulebook_claims(response.answer)
        response.ruling = drop_missing_rulebook_claims(response.ruling)
        response.official_basis = [drop_missing_rulebook_claims(item) for item in response.official_basis]
        response.official_basis = [item for item in response.official_basis if item]
        response.uncertainties = [drop_missing_rulebook_claims(item) for item in response.uncertainties]
        response.uncertainties = [item for item in response.uncertainties if item]
    response.sources = await verify_sources(response.sources)
    response.agents_used = ["Board Game Buddy", "Rules Helper"]
    return response


async def research_rulebook(game_name: str, question: str, expansions: str) -> str:
    prompt = (
        f"Look up the rules for the board game {game_name.strip()}. "
        f"Expansions in use: {expansions.strip() or 'none stated'}. "
        f"The player asked: {question.strip()}. "
        "Always search the sources below. Do this even if the player also uploaded a card or page. "
        "1. Open the publisher's official rulebook or rules reference. Report its title, edition, "
        "publisher, URL, the relevant rule, and the page number when the page shows one. "
        "2. Check the publisher's FAQ or updated rules when one exists, and label it as a publisher FAQ. "
        "3. Check BoardGameGeek or another established rules discussion, and label it as unofficial. "
        "Quote only text you can read. If an expansion changes the rule, include that source too."
    )
    try:
        response = await client().responses.create(
            model=MODEL_NAME,
            tools=[{"type": "web_search", "search_context_size": "medium"}],
            input=prompt,
        )
        note_usage(response, "rules_research")
    except Exception as error:
        return f"Rulebook lookup failed ({error.__class__.__name__}). Do not invent page numbers."
    return response.output_text or "Rulebook lookup failed. Do not invent page numbers."


def drop_missing_rulebook_claims(text: str) -> str:
    return re.sub(
        r"[^.?!]*(?:rulebook (?:was not|wasn't|was never) provided|no rulebook was provided|without a rulebook|rulebook needs to be checked|rulebook was not uploaded)[^.?!]*[.?!]?",
        "",
        text or "",
        flags=re.IGNORECASE,
    ).strip()


async def verify_sources(sources: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep only links that respond; preserve metadata for offline evidence."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=5) as http:
        for source in sources:
            url = source.get("url", "").strip()
            if not url:
                source["url_status"] = "not provided"
                continue
            if not url.startswith(("https://", "http://")):
                source["url"] = ""
                source["url_status"] = "invalid URL"
                continue
            try:
                host = (urlparse(url).hostname or "").lower()
            except ValueError:
                source["url"] = ""
                source["url_status"] = "invalid URL"
                continue
            host = host.removeprefix("www.")
            if not url.startswith("https://") or any(host == blocked or host.endswith(f".{blocked}") for blocked in SHORTENERS):
                source["url"] = ""
                source["url_status"] = "link not used"
                continue
            try:
                response = await http.head(url)
                if response.status_code >= 400:
                    response = await http.get(url)
                source["url_status"] = (
                    "verified" if response.status_code < 400 else f"failed ({response.status_code})"
                )
                if response.status_code == 404:
                    source["url"] = ""
            except (httpx.HTTPError, ValueError):
                source["url"] = ""
                source["url_status"] = "unreachable"
    return sources

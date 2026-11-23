from __future__ import annotations

import httpx
from urllib.parse import urlparse

from backend.agents.config import ask_json
from backend.models import RulesResponse

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

TRUSTED_HOSTS = (
    "fantasyflightgames.com",
    "mindclashgames.com",
    "beziergames.com",
    "boardgamegeek.com",
    "reddit.com",
    "kickstarter.com",
    "gamefound.com",
    "tabletopia.com",
    "boardgamearena.com",
    "store.steampowered.com",
    "apps.apple.com",
    "play.google.com",
)


async def answer_rules_question(
    *,
    question: str,
    image_data_url: str,
    expansions: str,
    variants: str,
) -> RulesResponse:
    result = await ask_json(
        system=(
            "You are the Rules Helper for Board Game Buddy. Answer a live board-game "
            "rules question using the uploaded rulebook or card photo as evidence. "
            "Distinguish official printed text from your interpretation. Treat expansion "
            "rules, variants, and house rules as separate layers. Never invent unreadable "
            "text or pretend a community interpretation is official. If the image is "
            "insufficient, say exactly what additional page or card information is needed."
        ),
        prompt=(
            f"Player question: {question.strip()}\n"
            f"Expansions in use: {expansions.strip() or 'not specified'}\n"
            f"Variants or house rules: {variants.strip() or 'none stated'}\n"
            "Give a direct ruling first, then a short numbered explanation. In "
            "official_basis, describe only what is visible or safely supported by the "
            "uploaded source; do not fabricate page numbers or quotations. For every "
            "rulebook source, include the exact edition/version and page only when known. "
            "For publisher sites and forums, identify the source type. Never invent a URL. "
            "Use an empty URL and url_status='not provided' when no verified link is available."
        ),
        schema_name="rules_response",
        schema=RULES_SCHEMA,
        image_data_url=image_data_url,
    )
    response = RulesResponse.model_validate(result)
    response.sources = await verify_sources(response.sources)
    response.agents_used = ["Board Game Buddy", "Rules Helper"]
    return response


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
            host = (urlparse(url).hostname or "").lower()
            if not any(host == allowed or host.endswith(f".{allowed}") for allowed in TRUSTED_HOSTS):
                source["url"] = ""
                source["url_status"] = "untrusted host"
                continue
            if not url.startswith("https://"):
                source["url"] = ""
                source["url_status"] = "HTTPS required"
                continue
            try:
                response = await http.head(url)
                if response.status_code >= 400:
                    response = await http.get(url)
                source["url_status"] = (
                    "verified" if response.status_code < 400 else f"failed ({response.status_code})"
                )
                if response.status_code >= 400:
                    source["url"] = ""
            except httpx.HTTPError:
                source["url"] = ""
                source["url_status"] = "unreachable"
    return sources

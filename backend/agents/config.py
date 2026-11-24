from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

ROOT = Path(__file__).resolve().parents[2]
# Prefer a project-local .env, then fall back to the course layout where the
# shared .env lives one folder above the cloned repository.
load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")

MODEL_NAME = os.getenv("MODEL_NAME", "gpt-6-luna")
USAGE: list[dict] = []


def client() -> AsyncOpenAI:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is missing. Copy .env.example to .env and add it.")
    return AsyncOpenAI(api_key=key)


def image_part(image_data_url: str) -> dict:
    return {"type": "input_image", "image_url": image_data_url, "detail": "high"}


def note_usage(response, label: str) -> None:
    """Record token counts when a measurement run asks for them. No prompt text is stored."""
    if os.getenv("BOARD_GAME_BUDDY_USAGE_LOG") != "1":
        return
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    details = usage.input_tokens_details
    output_details = usage.output_tokens_details
    searches = sum(1 for item in getattr(response, "output", []) or [] if getattr(item, "type", "") == "web_search_call")
    USAGE.append({
        "label": label,
        "model": MODEL_NAME,
        "input_tokens": usage.input_tokens,
        "cached_tokens": details.cached_tokens,
        "cache_write_tokens": details.cache_write_tokens,
        "output_tokens": usage.output_tokens,
        "reasoning_tokens": output_details.reasoning_tokens,
        "total_tokens": usage.total_tokens,
        "web_searches": searches,
    })


async def ask_json(
    *,
    system: str,
    prompt: str,
    schema_name: str,
    schema: dict,
    image_data_url: str = "",
    usage_label: str = "",
) -> dict:
    content: list[dict] = [{"type": "input_text", "text": prompt}]
    if image_data_url:
        content.append(image_part(image_data_url))
    response = await client().responses.create(
        model=MODEL_NAME,
        input=[
            {"role": "system", "content": [{"type": "input_text", "text": system}]},
            {"role": "user", "content": content},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": schema,
            }
        },
    )
    if usage_label:
        note_usage(response, usage_label)
    import json

    return json.loads(response.output_text)

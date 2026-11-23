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


def client() -> AsyncOpenAI:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is missing. Copy .env.example to .env and add it.")
    return AsyncOpenAI(api_key=key)


def image_part(image_data_url: str) -> dict:
    return {"type": "input_image", "image_url": image_data_url, "detail": "high"}


async def ask_json(
    *,
    system: str,
    prompt: str,
    schema_name: str,
    schema: dict,
    image_data_url: str = "",
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
    import json

    return json.loads(response.output_text)

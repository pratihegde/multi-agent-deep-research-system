from __future__ import annotations

import json
import os
from typing import TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class LLMConfigError(RuntimeError):
    pass


def _get_client() -> AsyncOpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise LLMConfigError("OPENAI_API_KEY is not set.")
    return AsyncOpenAI(api_key=api_key)


def _extract_json(content: str) -> dict:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    return json.loads(stripped)


async def call_openai_json(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
) -> dict:
    client = _get_client()
    selected_model = model or os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    response = await client.chat.completions.create(
        model=selected_model,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    content = response.choices[0].message.content
    if not content:
        return {}
    return _extract_json(content)


async def call_openai_typed(
    *,
    system_prompt: str,
    user_prompt: str,
    schema: type[T],
    model: str | None = None,
) -> T:
    data = await call_openai_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=model,
    )
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise RuntimeError(f"Failed to parse model response into {schema.__name__}: {exc}") from exc


async def stream_openai_text(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
):
    client = _get_client()
    selected_model = model or os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    response = await client.chat.completions.create(
        model=selected_model,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        stream=True,
    )
    async for chunk in response:
        try:
            delta = chunk.choices[0].delta
        except (AttributeError, IndexError):
            continue
        content = getattr(delta, "content", None)
        if content:
            yield content

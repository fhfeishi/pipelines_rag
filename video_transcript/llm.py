from __future__ import annotations

import json
import re
from typing import Any

import httpx

from configs.config import settings


class LlmError(RuntimeError):
    pass


def _api_key(explicit: str | None = None) -> str:
    key = explicit or settings.deepseek_api_key
    if not key:
        raise LlmError(
            "DeepSeek API key missing. Set DEEPSEEK_API_KEY or configs/.env deepseek_api_key."
        )
    return key


def _extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LlmError(f"LLM did not return valid JSON: {text[:300]}") from exc
    if not isinstance(payload, dict):
        raise LlmError(f"LLM JSON must be an object, got: {type(payload).__name__}")
    return payload


def chat_completion(
    *,
    system: str,
    user: str,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.2,
    timeout_s: float = 120.0,
) -> tuple[str, str | None]:
    model = model or settings.polish_model
    base_url = (base_url or settings.polish_base_url).rstrip("/")
    key = _api_key(api_key)

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "stream": False,
    }
    with httpx.Client(timeout=timeout_s) as client:
        response = client.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if response.status_code != 200:
        raise LlmError(
            f"LLM request failed (HTTP {response.status_code}): {response.text[:500]}"
        )
    data = response.json()
    request_id = data.get("id")
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmError(
            f"Unexpected LLM response: {json.dumps(data, ensure_ascii=False)[:500]}"
        ) from exc
    return str(content).strip(), str(request_id) if request_id else None


def chat_json(
    *,
    system: str,
    user: str,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.2,
) -> tuple[dict[str, Any], str | None]:
    content, request_id = chat_completion(
        system=system,
        user=user,
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
    )
    return _extract_json_object(content), request_id

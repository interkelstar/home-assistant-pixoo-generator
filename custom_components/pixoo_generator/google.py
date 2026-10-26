"""Google video provider; optional scene direction through Home Assistant AI Task."""

import asyncio
import base64
import json
import re

import aiohttp
from homeassistant.components.ai_task import async_generate_data
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .render import DIRECTION_PROMPT, PROMPT


class GenerationError(HomeAssistantError):
    """Safe provider error without credentials or raw response bodies."""


def model_name(value):
    if not re.fullmatch(r"[a-zA-Z0-9._-]{1,100}", value):
        raise ValueError("Invalid model name")
    return value


async def validate_key(hass, api_key):
    async with async_get_clientsession(hass).get(
        "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1",
        headers={"x-goog-api-key": api_key},
        timeout=aiohttp.ClientTimeout(total=15),
        allow_redirects=False,
    ) as response:
        if response.status in (400, 401, 403):
            raise GenerationError("invalid_auth")
        if response.status != 200:
            raise GenerationError("cannot_connect")


async def direction(hass, config, job):
    instructions = DIRECTION_PROMPT.format(text=job["text"], style=job["style"])
    if config.get("ai_task_entity"):
        async with asyncio.timeout(90):
            result = await async_generate_data(
                hass,
                task_name="Pixoo animation direction",
                entity_id=config["ai_task_entity"],
                instructions=instructions,
            )
        value = result.data
        if not isinstance(value, str) or not value.strip() or len(value) > 6000:
            raise GenerationError("AI Task did not return a scene description.")
        return value
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name(config['director_model'])}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": instructions}]}],
        "generationConfig": {"maxOutputTokens": 1024, "temperature": 0.5},
    }
    async with async_get_clientsession(hass).post(
        url,
        json=payload,
        headers={"x-goog-api-key": config["api_key"]},
        timeout=aiohttp.ClientTimeout(total=45),
        allow_redirects=False,
    ) as response:
        if response.status != 200:
            raise GenerationError(f"Scene preparation failed (HTTP {response.status}).")
        data = await response.json()
    job["direction_usage"] = data.get("usageMetadata", {})
    blocked = data.get("promptFeedback", {}).get("blockReason")
    if blocked:
        raise GenerationError("Google blocked this scene description. Try another scene.")
    candidates = data.get("candidates", [])
    if not candidates or candidates[0].get("finishReason") != "STOP":
        raise GenerationError(
            "Google did not finish the scene description. No video was requested."
        )
    text = " ".join(
        p["text"]
        for p in candidates[0].get("content", {}).get("parts", [])
        if p.get("text") and not p.get("thought")
    )
    if not text or len(text) > 6000:
        raise GenerationError("Invalid scene description.")
    return text


async def video(hass, config, job):
    payload = {
        "model": model_name(config["video_model"]),
        "input": PROMPT.format(text=job["text"], style=job["style"], direction=job["direction"]),
        "response_format": {"type": "video", "resolution": "360p", "aspect_ratio": "16:9"},
    }
    async with async_get_clientsession(hass).post(
        "https://generativelanguage.googleapis.com/v1beta/interactions",
        json=payload,
        headers={"x-goog-api-key": config["api_key"]},
        timeout=aiohttp.ClientTimeout(total=240),
        allow_redirects=False,
    ) as response:
        if response.status != 200:
            raise GenerationError(f"Video generation failed (HTTP {response.status}).")
        raw = bytearray()
        async for chunk in response.content.iter_chunked(65536):
            raw.extend(chunk)
            if len(raw) > 45_000_000:
                raise GenerationError("Video response is too large.")
        data = json.loads(raw)
    if data.get("status") != "completed":
        raise GenerationError("Video generation did not complete.")
    job["provider_usage"] = data.get("usage", {})
    for step in data.get("steps", []):
        if step.get("type") != "model_output":
            continue
        for part in step.get("content", []):
            if (
                part.get("type") == "video"
                and part.get("mime_type") == "video/mp4"
                and part.get("data")
            ):
                return base64.b64decode(part["data"], validate=True)
    raise GenerationError("The provider returned no video.")

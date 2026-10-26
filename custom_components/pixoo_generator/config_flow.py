"""UI setup and options; no house-specific IDs or borrowed provider credentials."""

import shutil
from urllib.parse import urlsplit

import aiohttp
import probatio as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .const import DEFAULT_DIRECTOR_MODEL, DEFAULT_HISTORY, DEFAULT_VIDEO_MODEL, DOMAIN
from .google import GenerationError, model_name, validate_key
from .transport import frame_address, post


def base_url(value):
    parts = urlsplit(value)
    if (
        parts.scheme != "http"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path not in ("", "/")
    ):
        raise ValueError("Use an HTTP LAN URL without a path or credentials")
    if parts.hostname in ("localhost", "127.0.0.1", "0.0.0.0"):
        raise ValueError("The frame must be able to reach Home Assistant")
    return value.rstrip("/")


def provider_schema(data, options=False):
    schema = {
        (
            vol.Optional("api_key", default="") if options else vol.Required("api_key")
        ): selector.TextSelector({"type": "password"}),
        vol.Required("video_model", default=data.get("video_model", DEFAULT_VIDEO_MODEL)): str,
        vol.Required(
            "director_model", default=data.get("director_model", DEFAULT_DIRECTOR_MODEL)
        ): str,
        vol.Optional("ai_task_entity"): selector.EntitySelector({"domain": "ai_task"}),
        vol.Required("history_limit", default=data.get("history_limit", DEFAULT_HISTORY)): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=100)
        ),
        vol.Required("keep_source", default=data.get("keep_source", False)): bool,
    }
    if data.get("ai_task_entity"):
        schema[vol.Optional("ai_task_entity", default=data["ai_task_entity"])] = schema.pop(
            vol.Optional("ai_task_entity")
        )
    return vol.Schema(schema)


async def validate_provider(hass, data):
    model_name(data["video_model"])
    model_name(data["director_model"])
    if data.get("ai_task_entity"):
        entity = hass.states.get(data["ai_task_entity"])
        if not entity or not entity.attributes.get("supported_features", 0) & 1:
            raise GenerationError("invalid_ai_task")
    await validate_key(hass, data["api_key"])


class PixooConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self.frame = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return PixooOptionsFlow()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                data = dict(user_input)
                linked = None
                if data.get("pixoo_entry_id"):
                    linked = self.hass.config_entries.async_get_entry(data["pixoo_entry_id"])
                    if not linked or linked.domain != "divoom_pixoo":
                        raise ValueError("Invalid frame integration")
                host = data.get("host", "").strip()
                if not host and linked:
                    host = linked.options.get("ip_address", linked.data.get("ip_address", ""))
                data["host"] = frame_address(host)
                data["base_url"] = base_url(data["base_url"])
                if any(
                    e.options.get("host", e.data.get("host")) == data["host"]
                    for e in self._async_current_entries()
                ):
                    return self.async_abort(reason="already_configured")
                if not await self.hass.async_add_executor_job(shutil.which, "ffmpeg"):
                    return self.async_abort(reason="ffmpeg_missing")
                await post(self.hass, data["host"], {"Command": "Channel/GetIndex"})
                self.frame = data
                return await self.async_step_provider()
            except ValueError:
                errors["base"] = "invalid_connection"
            except HomeAssistantError, aiohttp.ClientError, TimeoutError:
                errors["base"] = "cannot_connect"
        try:
            url = get_url(self.hass, allow_external=False, allow_cloud=False)
        except NoURLAvailableError:
            url = ""
        values = user_input or {}
        return self.async_show_form(
            step_id="user",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required("name", default=values.get("name", "Pixoo Art")): str,
                    vol.Optional("pixoo_entry_id"): selector.ConfigEntrySelector(
                        {"integration": "divoom_pixoo"}
                    ),
                    vol.Optional("host", default=values.get("host", "")): str,
                    vol.Required("base_url", default=values.get("base_url", url)): str,
                }
            ),
        )

    async def async_step_provider(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                data = {**self.frame, **user_input}
                await validate_provider(self.hass, data)
                await self.async_set_unique_id(data["host"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=data.pop("name"), data=data)
            except GenerationError as exc:
                errors["base"] = str(exc)
            except ValueError:
                errors["base"] = "invalid_model"
            except aiohttp.ClientError, TimeoutError:
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="provider", data_schema=provider_schema(user_input or {}), errors=errors
        )


class PixooOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        old = {**self.config_entry.data, **self.config_entry.options}
        errors = {}
        if user_input is not None:
            try:
                data = {**old, **user_input}
                data["api_key"] = user_input.get("api_key") or old["api_key"]
                data["ai_task_entity"] = user_input.get("ai_task_entity", "")
                data["host"] = frame_address(data["host"])
                data["base_url"] = base_url(data["base_url"])
                if any(
                    e.entry_id != self.config_entry.entry_id
                    and e.options.get("host", e.data.get("host")) == data["host"]
                    for e in self.hass.config_entries.async_entries(DOMAIN)
                ):
                    raise GenerationError("already_configured")
                await post(self.hass, data["host"], {"Command": "Channel/GetIndex"})
                await validate_provider(self.hass, data)
                return self.async_create_entry(title="", data=data)
            except GenerationError as exc:
                errors["base"] = str(exc)
            except ValueError:
                errors["base"] = "invalid_connection"
            except HomeAssistantError, aiohttp.ClientError, TimeoutError:
                errors["base"] = "cannot_connect"
        schema = provider_schema(old, options=True).extend(
            {
                vol.Required("host", default=old["host"]): str,
                vol.Required("base_url", default=old["base_url"]): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

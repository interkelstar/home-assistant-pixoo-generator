"""Generate pixel animations for Pixoo frames from Home Assistant."""

import secrets

import probatio as vol
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PLATFORMS
from .runtime import Manager, stamp

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
COMMON = {vol.Optional("entry_id"): cv.string}
GENERATE_SCHEMA = vol.Schema(
    {
        **COMMON,
        vol.Required("text"): vol.All(cv.string, vol.Length(min=1, max=800)),
        vol.Optional("style"): vol.All(cv.string, vol.Length(max=160)),
        vol.Optional("display", default=True): cv.boolean,
        vol.Optional("notify", default=True): cv.boolean,
    }
)
SHOW_SCHEMA = vol.Schema(
    {**COMMON, vol.Optional("job_id"): cv.string, vol.Optional("notify", default=True): cv.boolean}
)
DELETE_SCHEMA = vol.Schema({**COMMON, vol.Optional("job_id"): cv.string})
FAVORITE_SCHEMA = vol.Schema(
    {
        **COMMON,
        vol.Optional("job_id"): cv.string,
        vol.Optional("favorite", default=True): cv.boolean,
    }
)


class AnimationView(HomeAssistantView):
    url = "/api/pixoo_generator/{ticket}.gif"
    name = "api:pixoo_generator:file"
    requires_auth = False

    def __init__(self, hass):
        self.hass = hass

    async def get(self, request, ticket):
        if len(ticket) != 32:
            raise web.HTTPNotFound()
        for manager in self.hass.data[DOMAIN]["managers"].values():
            for job in manager.jobs.values():
                if secrets.compare_digest(job["ticket"], ticket) and "render" in job:
                    return await self.serve(request, manager, job)
        raise web.HTTPNotFound()

    async def serve(self, request, manager, job):
        path = manager.root / job["id"] / "animation.gif"
        try:
            data = await self.hass.async_add_executor_job(path.read_bytes)
        except OSError as exc:
            raise web.HTTPNotFound() from exc
        response = web.StreamResponse(
            headers={
                "Content-Type": "image/gif",
                "Content-Length": str(len(data)),
                "Cache-Control": "no-store",
            }
        )
        await response.prepare(request)
        await response.write(data)
        await response.write_eof()
        if request.remote == manager.config["host"]:
            job.update(fetched_at=stamp(), fetched_bytes=len(data))
            if event := manager.downloads.get(job["id"]):
                event.set()
        return response


def resolve(hass, entry_id):
    managers = hass.data[DOMAIN]["managers"]
    if entry_id:
        if entry_id not in managers:
            raise ServiceValidationError("The selected Pixoo generator is not loaded.")
        return managers[entry_id]
    if len(managers) != 1:
        raise ServiceValidationError("Choose entry_id when more than one frame is configured.")
    return next(iter(managers.values()))


async def async_setup(hass, config):
    hass.data.setdefault(DOMAIN, {"managers": {}})
    hass.http.register_view(AnimationView(hass))

    async def generate(call):
        data = dict(call.data)
        manager = resolve(hass, data.pop("entry_id", None))
        return await manager.generate(**data)

    async def show(call):
        data = dict(call.data)
        manager = resolve(hass, data.pop("entry_id", None))
        return await manager.show(**data)

    async def delete(call):
        data = dict(call.data)
        manager = resolve(hass, data.pop("entry_id", None))
        await manager.delete(**data)

    async def list_animations(call):
        return resolve(hass, call.data.get("entry_id")).catalog()

    async def favorite(call):
        data = dict(call.data)
        return await resolve(hass, data.pop("entry_id", None)).favorite(**data)

    hass.services.async_register(
        DOMAIN,
        "generate",
        generate,
        schema=GENERATE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, "show", show, schema=SHOW_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    hass.services.async_register(DOMAIN, "delete", delete, schema=DELETE_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        "list_animations",
        list_animations,
        schema=vol.Schema(COMMON),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "favorite",
        favorite,
        schema=FAVORITE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass, entry):
    manager = Manager(hass, entry)
    await manager.load()
    entry.runtime_data = manager
    hass.data[DOMAIN]["managers"][entry.entry_id] = manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def async_update_options(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    manager = entry.runtime_data
    await manager.close()
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN]["managers"].pop(entry.entry_id, None)
        return True
    return False


async def async_remove_entry(hass, entry):
    # Explicit removal of the integration removes its private gallery, too.
    # Unload/reload and HACS updates never remove it.
    manager = Manager(hass, entry)
    await hass.async_add_executor_job(manager._remove, manager.root)
    await manager.store.async_remove()

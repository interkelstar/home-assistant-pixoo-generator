"""Authenticated HA preview of the selected GIF."""

from datetime import datetime

from homeassistant.components.image import ImageEntity

from .entity import PixooEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([PixooPreview(entry.runtime_data)])


class PixooPreview(PixooEntity, ImageEntity):
    _attr_content_type = "image/gif"

    def __init__(self, manager):
        ImageEntity.__init__(self, manager.hass)
        PixooEntity.__init__(self, manager, "preview")

    @property
    def image_last_updated(self):
        job = self.manager.jobs.get(self.manager.selected, {})
        return datetime.fromisoformat(job["updated_at"]) if job else None

    async def async_image(self):
        if not self.manager.selected:
            return None
        path = self.manager.root / self.manager.selected / "animation.gif"
        try:
            return await self.hass.async_add_executor_job(path.read_bytes)
        except OSError:
            return None

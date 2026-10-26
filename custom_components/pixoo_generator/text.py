"""Prompt and style fields, usable without an assistant."""

from homeassistant.components.text import TextEntity, TextMode

from .entity import PixooEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        [PixooText(entry.runtime_data, "prompt", 255), PixooText(entry.runtime_data, "style", 160)]
    )


class PixooText(PixooEntity, TextEntity):
    _attr_mode = TextMode.TEXT
    _attr_native_min = 0

    def __init__(self, manager, key, limit):
        super().__init__(manager, key)
        self.key = key
        self._attr_native_max = limit

    @property
    def native_value(self):
        return getattr(self.manager, self.key)

    async def async_set_value(self, value):
        setattr(self.manager, self.key, value)
        await self.manager.save()

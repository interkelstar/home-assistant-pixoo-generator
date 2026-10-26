"""Select a saved animation without paying for generation again."""

from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import ServiceValidationError

from .entity import PixooEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([PixooGallery(entry.runtime_data, "gallery")])


class PixooGallery(PixooEntity, SelectEntity):
    def choices(self):
        return {
            f"{j['text'][:100]} · {j['id'][:8]}": k
            for k, j in reversed(self.manager.jobs.items())
            if "render" in j
        }

    @property
    def options(self):
        return list(self.choices())

    @property
    def current_option(self):
        return next(
            (label for label, jid in self.choices().items() if jid == self.manager.selected), None
        )

    async def async_select_option(self, option):
        if option not in self.choices():
            raise ServiceValidationError("Animation not found.")
        self.manager.selected = self.choices()[option]
        await self.manager.save()

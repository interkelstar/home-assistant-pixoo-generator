"""Manual generation and gallery controls."""

from homeassistant.components.button import ButtonEntity

from .entity import PixooEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        [PixooButton(entry.runtime_data, key) for key in ["generate", "show", "delete"]]
    )


class PixooButton(PixooEntity, ButtonEntity):
    def __init__(self, manager, key):
        super().__init__(manager, key)
        self.key = key

    @property
    def available(self):
        return self.manager.active is None and (
            self.key == "generate" or self.manager.selected is not None
        )

    async def async_press(self):
        if self.key == "generate":
            await self.manager.generate(self.manager.prompt)
        elif self.key == "show":
            await self.manager.show()
        else:
            await self.manager.delete()

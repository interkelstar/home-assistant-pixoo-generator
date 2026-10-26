"""Job status and useful diagnostics without credentials or download tokens."""

from homeassistant.components.sensor import SensorEntity

from .entity import PixooEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([PixooStatus(entry.runtime_data, "status")])


class PixooStatus(PixooEntity, SensorEntity):
    @property
    def native_value(self):
        return self.manager.jobs.get(self.manager.latest, {}).get("status", "idle")

    @property
    def extra_state_attributes(self):
        job = self.manager.jobs.get(self.manager.latest, {})
        return {
            "job_id": job.get("id"),
            "request": job.get("text"),
            "error": job.get("error"),
            "frames": job.get("render", {}).get("frames"),
            "fetched_at": job.get("fetched_at"),
            "saved_jobs": len(self.manager.jobs),
        }

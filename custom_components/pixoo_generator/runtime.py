"""Per-frame jobs, bounded gallery and verified delivery."""

import asyncio
import logging
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import google, transport
from .const import BUSY_STATES, DEFAULT_HISTORY, DEFAULT_STYLE, DOMAIN, EVENT_JOB
from .render import render_video

_LOGGER = logging.getLogger(__name__)


def stamp():
    return datetime.now(timezone.utc).isoformat()


class Manager:
    def __init__(self, hass, entry):
        self.hass, self.entry = hass, entry
        self.config = {**entry.data, **entry.options}
        self.root = Path(hass.config.path(".storage", "pixoo_generator_artifacts", entry.entry_id))
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self.jobs = {}
        self.prompt = ""
        self.style = DEFAULT_STYLE
        self.selected = None
        self.active = None
        self.task = None
        self.downloads = {}
        self.latest = None
        self.signal = f"{DOMAIN}_{entry.entry_id}"
        self.save_lock = asyncio.Lock()

    async def load(self):
        data = await self.store.async_load() or {}
        self.jobs = data.get("jobs", {})
        self.prompt = data.get("prompt", "")
        self.style = data.get("style", DEFAULT_STYLE)
        self.selected = data.get("selected")
        self.latest = data.get("latest")
        for job in self.jobs.values():
            if job.get("status") == "delivered" and job.get("fetched_at"):
                job.setdefault("last_displayed_at", job["fetched_at"])
            if job["status"] in BUSY_STATES:
                job.update(
                    status="interrupted",
                    error="Home Assistant restarted; generation was not retried.",
                    updated_at=stamp(),
                )
        await self.save()

    def changed(self):
        async_dispatcher_send(self.hass, self.signal)

    async def save(self):
        async with self.save_lock:
            limit = int(self.config.get("history_limit", DEFAULT_HISTORY))
            while sum(not j.get("favorite", False) for j in self.jobs.values()) > limit:
                old = next(
                    (k for k, j in self.jobs.items() if k != self.active and not j.get("favorite")),
                    None,
                )
                if old is None:
                    break
                path = self.root / old
                await self.hass.async_add_executor_job(self._remove, path)
                self.jobs.pop(old)
            if self.selected not in self.jobs or "render" not in self.jobs.get(self.selected, {}):
                self.selected = next(
                    (k for k in reversed(self.jobs) if "render" in self.jobs[k]), None
                )
            await self.store.async_save(
                {
                    "jobs": self.jobs,
                    "prompt": self.prompt,
                    "style": self.style,
                    "selected": self.selected,
                    "latest": self.latest,
                }
            )
        self.changed()

    @staticmethod
    def _remove(path):
        if path.exists():
            shutil.rmtree(path)

    async def update(self, job, status, **fields):
        job.update(status=status, updated_at=stamp(), **fields)
        self.latest = job["id"]
        await self.save()

    def event(self, job):
        self.hass.bus.async_fire(
            EVENT_JOB,
            {
                "entry_id": self.entry.entry_id,
                "job_id": job["id"],
                "status": job["status"],
                "text": job["text"],
                "error": job.get("error"),
                "notify": job.get("notify", True),
            },
        )

    async def generate(self, text, style=None, display=True, notify=True):
        text = text.strip()
        if not text or len(text) > 800:
            raise ServiceValidationError("Describe the animation using 1–800 characters.")
        if self.active:
            return {"accepted": False, "status": "busy", "job_id": self.active}
        jid = uuid4().hex
        job = {
            "id": jid,
            "ticket": secrets.token_hex(16),
            "text": text,
            "style": style or self.style,
            "display": display,
            "notify": notify,
            "model": self.config["video_model"],
            "status": "queued",
            "created_at": stamp(),
            "updated_at": stamp(),
        }
        self.jobs[jid] = job
        self.active = jid
        try:
            await self.update(job, "queued")
            self.task = self.entry.async_create_background_task(
                self.hass, self.run(job), f"pixoo-{jid}"
            )
        except BaseException:
            self.active = None
            raise
        return {"accepted": True, "status": "queued", "job_id": jid}

    def catalog(self):
        """Read-only metadata for Assist and automations; never expose download tokens."""
        animations = []
        for job in reversed(self.jobs.values()):
            if "render" not in job:
                continue
            item = {k: job.get(k) for k in ("text", "style", "created_at", "last_displayed_at")}
            created = dt_util.parse_datetime(job.get("created_at", ""))
            item.update(
                job_id=job["id"],
                favorite=bool(job.get("favorite")),
                created_local_date=dt_util.as_local(created).date().isoformat()
                if created
                else None,
            )
            animations.append(item)
        displayed = max(
            (j for j in self.jobs.values() if j.get("last_displayed_at") and "render" in j),
            key=lambda j: j["last_displayed_at"],
            default=None,
        )
        return {
            "animations": animations,
            "last_displayed_job_id": displayed["id"] if displayed else None,
            "history_limit": int(self.config.get("history_limit", DEFAULT_HISTORY)),
        }

    async def favorite(self, favorite=True, job_id=None):
        jid = job_id or self.catalog()["last_displayed_job_id"]
        job = self.jobs.get(jid)
        if not job or "render" not in job:
            raise ServiceValidationError(
                "Choose an existing animation; no confirmed display is available."
            )
        job["favorite"] = favorite
        await self.save()
        return {"job_id": jid, "favorite": favorite, "text": job["text"]}

    async def run(self, job):
        try:
            await self.update(job, "planning")
            direction = await google.direction(self.hass, self.config, job)
            await self.update(job, "generating", direction=direction)
            data = await google.video(self.hass, self.config, job)
            # A worker cannot be cancelled halfway through a filesystem mutation.
            render_task = self.hass.async_add_executor_job(
                render_video,
                data,
                self.root / job["id"],
                self.config.get("keep_source", False),
                self.config.get("ffmpeg", "ffmpeg"),
            )
            try:
                result = await asyncio.shield(render_task)
            except asyncio.CancelledError:
                await render_task
                raise
            self.selected = job["id"]
            await self.update(job, "ready", render=result)
            if job["display"]:
                await self.deliver(job)
            self.event(job)
        except asyncio.CancelledError:
            await self.update(
                job, "interrupted", error="Generation interrupted; no automatic retry."
            )
            raise
        except Exception as exc:
            error = (
                str(exc)
                if isinstance(exc, HomeAssistantError)
                else "Animation generation or delivery failed."
            )
            _LOGGER.warning("Pixoo job %s failed (%s)", job["id"], type(exc).__name__)
            await self.update(job, "failed", error=error)
            self.event(job)
        finally:
            self.active = None
            self.changed()

    async def deliver(self, job):
        # Optional cooperation with an installed Divoom integration.
        linked = self.hass.config_entries.async_get_entry(self.config.get("pixoo_entry_id", ""))
        if linked and linked.options.get("pages_data", linked.data.get("pages_data")):
            raise HomeAssistantError(
                "Disable rotating Pixoo pages before displaying generated animations."
            )
        await self.update(job, "sending", error=None)
        host = self.config["host"]
        await transport.post(self.hass, host, {"Command": "Channel/GetIndex"})
        event = asyncio.Event()
        self.downloads[job["id"]] = event
        job.pop("fetched_at", None)
        job.pop("fetched_bytes", None)
        url = f"{self.config['base_url'].rstrip('/')}/api/pixoo_generator/{job['ticket']}.gif"
        try:
            await transport.post(
                self.hass, host, {"Command": "Device/PlayTFGif", "FileType": 2, "FileName": url}
            )
            try:
                await asyncio.wait_for(event.wait(), 20)
            except TimeoutError:
                raise HomeAssistantError(
                    "The frame accepted the command but its download was not confirmed."
                ) from None
            device_state = await transport.post(self.hass, host, {"Command": "Channel/GetIndex"})
            await self.update(
                job, "delivered", device_state=device_state, last_displayed_at=stamp()
            )
        finally:
            self.downloads.pop(job["id"], None)

    async def show(self, job_id=None, notify=True):
        job = self.jobs.get(job_id or self.selected)
        if not job or "render" not in job:
            raise ServiceValidationError("Select a saved animation first.")
        if self.active:
            return {"accepted": False, "status": "busy", "job_id": self.active}
        self.active = job["id"]
        job["notify"] = notify
        self.selected = job["id"]
        try:
            await self.deliver(job)
        except Exception as exc:
            error = (
                str(exc) if isinstance(exc, HomeAssistantError) else "Animation delivery failed."
            )
            await self.update(job, "failed", error=error)
        finally:
            self.active = None
            self.changed()
        self.event(job)
        return {
            "accepted": job["status"] == "delivered",
            "status": job["status"],
            "job_id": job["id"],
        }

    async def delete(self, job_id=None):
        jid = job_id or self.selected
        if self.active:
            raise ServiceValidationError("Wait for the active job before deleting an animation.")
        if jid not in self.jobs:
            raise ServiceValidationError("Animation not found.")
        await self.hass.async_add_executor_job(self._remove, self.root / jid)
        self.jobs.pop(jid)
        if self.latest == jid:
            self.latest = next(reversed(self.jobs), None)
        await self.save()

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.save()

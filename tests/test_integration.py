"""Run with Python from Home Assistant 2026.10+; no house or provider calls."""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from custom_components.pixoo_generator import (
    AnimationView,
    config_flow,
    google,
    render,
    resolve,
    runtime,
    transport,
)


class RenderTest(unittest.TestCase):
    def test_motion_static_and_corrupt_controls(self):
        with tempfile.TemporaryDirectory() as d:
            frames = []
            for i in range(40):
                f = Image.new("RGB", (64, 64), "navy")
                f.paste("orange", (10 + i % 10, 20, 20 + i % 10, 35))
                frames.append(f)
            result = render.encode_frames(frames, Path(d) / "ok", b"fixture")
            self.assertGreater(result["frames"], 2)
            self.assertFalse((Path(d) / "ok/source.mp4").exists())
            with Image.open(Path(d) / "ok/animation.gif") as gif:
                self.assertEqual(gif.info["loop"], 0)
                self.assertEqual(gif.size, (64, 64))
            render.encode_frames(frames, Path(d) / "debug", b"fixture", True)
            self.assertTrue((Path(d) / "debug/source.mp4").exists())
            with self.assertRaisesRegex(ValueError, "static"):
                render.encode_frames([frames[0]] * 40, Path(d) / "bad", b"fixture")
            with self.assertRaises(Exception):
                render.render_video(b"broken", Path(d) / "broken")
            self.assertFalse((Path(d) / "bad").exists())
            self.assertFalse((Path(d) / "broken").exists())

    def test_connection_controls(self):
        self.assertEqual(transport.frame_address("192.0.2.38"), "192.0.2.38")
        for address in ["127.0.0.1", "http://192.0.2.38", "0.0.0.0", "192.0.2.38/secret"]:
            with self.assertRaises(ValueError):
                transport.frame_address(address)
        self.assertEqual(config_flow.base_url("http://192.0.2.1:8123/"), "http://192.0.2.1:8123")
        for url in [
            "http://localhost:8123",
            "https://ha.example",
            "http://user:secret@192.0.2.1",
            "http://192.0.2.1/path",
        ]:
            with self.assertRaises(ValueError):
                config_flow.base_url(url)


class RuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tasks = []
        self.events = []

        async def executor(fn, *args):
            return fn(*args)

        def task(hass, coro, name):
            t = asyncio.create_task(coro)
            self.tasks.append(t)
            return t

        self.config = {
            "host": "192.0.2.38",
            "base_url": "http://192.0.2.1:8123",
            "api_key": "fake-key",
            "video_model": "test-video",
            "director_model": "test-text",
            "history_limit": 20,
            "keep_source": False,
        }
        self.entry = SimpleNamespace(
            entry_id="frame-one", data=self.config, options={}, async_create_background_task=task
        )
        self.hass = SimpleNamespace(
            config=SimpleNamespace(path=lambda *a: str(Path(self.tmp.name).joinpath(*a))),
            config_entries=SimpleNamespace(async_get_entry=lambda _: None),
            async_add_executor_job=executor,
            data={},
            bus=SimpleNamespace(async_fire=lambda *a: self.events.append(a)),
        )
        self.store = SimpleNamespace(
            async_load=AsyncMock(return_value=None), async_save=AsyncMock()
        )
        with patch.object(runtime, "Store", return_value=self.store):
            self.m = runtime.Manager(self.hass, self.entry)
        self.m.changed = Mock()
        self.hass.data["pixoo_generator"] = {"managers": {"frame-one": self.m}}
        self.job = {
            "id": "a",
            "ticket": "a" * 32,
            "text": "cat",
            "style": "pixel",
            "display": True,
            "notify": True,
            "status": "queued",
            "updated_at": "now",
        }
        self.m.jobs["a"] = self.job

    async def asyncTearDown(self):
        for t in self.tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tmp.cleanup()

    async def test_failure_cannot_display_previous_animation(self):
        self.m.deliver = AsyncMock()
        with patch.object(
            google, "direction", AsyncMock(side_effect=google.GenerationError("Provider failed"))
        ):
            await self.m.run(self.job)
        self.m.deliver.assert_not_awaited()
        self.assertEqual(self.job["status"], "failed")
        self.assertEqual(self.events[-1][1]["status"], "failed")
        self.assertIsNone(self.m.active)

    async def test_busy_and_blank_cannot_start_paid_calls(self):
        self.m.active = "a"
        self.assertFalse((await self.m.generate("another"))["accepted"])
        with self.assertRaises(Exception):
            await self.m.generate("  ")
        self.assertEqual(self.tasks, [])

    async def test_start_and_complete_without_speaker_dependency(self):
        def result(data, path, *args):
            path.mkdir(parents=True)
            (path / "animation.gif").write_bytes(b"GIF89a")
            return {"frames": 2}

        self.m.deliver = AsyncMock(side_effect=lambda job: job.update(status="delivered"))
        with (
            patch.object(google, "direction", AsyncMock(return_value="brief")),
            patch.object(google, "video", AsyncMock(return_value=b"video")),
            patch.object(runtime, "render_video", result),
        ):
            response = await self.m.generate("new cat")
            await self.m.task
        self.assertTrue(response["accepted"])
        self.assertEqual(self.events[-1][1]["status"], "delivered")
        self.assertFalse(hasattr(self.hass, "services"))

    async def test_delivery_requires_ack_and_device_download(self):
        async def post(hass, host, payload):
            if payload["Command"] == "Device/PlayTFGif":
                self.assertLess(len(payload["FileName"]), 128)
                self.m.downloads["a"].set()
            return {"error_code": 0}

        with patch.object(transport, "post", post):
            await self.m.deliver(self.job)
        self.assertEqual(self.job["status"], "delivered")
        self.assertEqual(self.m.downloads, {})

        async def timeout(coro, seconds):
            coro.close()
            raise TimeoutError

        with (
            patch.object(transport, "post", AsyncMock(return_value={"error_code": 0})),
            patch.object(runtime.asyncio, "wait_for", timeout),
        ):
            with self.assertRaisesRegex(Exception, "download"):
                await self.m.deliver(self.job)
        self.assertNotEqual(self.job["status"], "delivered")

    async def test_file_ticket_and_device_fetch_control(self):
        folder = self.m.root / "a"
        folder.mkdir(parents=True)
        data = b"GIF89a-test"
        (folder / "animation.gif").write_bytes(data)
        self.job["render"] = {"frames": 2}
        event = asyncio.Event()
        self.m.downloads["a"] = event
        view = AnimationView(self.hass)

        async def serve(request):
            return await view.get(request, request.match_info["ticket"])

        app = web.Application()
        app.router.add_get(view.url, serve)
        async with TestClient(TestServer(app)) as client:
            bad = await client.get("/api/pixoo_generator/" + "b" * 32 + ".gif")
            self.assertEqual(bad.status, 404)
            self.assertFalse(event.is_set())
            good = await client.get("/api/pixoo_generator/" + "a" * 32 + ".gif")
            self.assertEqual(await good.read(), data)
            self.assertFalse(event.is_set())
            self.m.config["host"] = "127.0.0.1"
            device = await client.get("/api/pixoo_generator/" + "a" * 32 + ".gif")
            self.assertEqual(await device.read(), data)
            self.assertTrue(event.is_set())

    async def test_retention_deletes_files_and_metadata_together(self):
        self.m.config["history_limit"] = 2
        self.m.jobs = {
            k: {**self.job, "id": k, "render": {"frames": 2}, "status": "delivered"}
            for k in ["old", "middle", "new"]
        }
        for k in self.m.jobs:
            p = self.m.root / k
            p.mkdir(parents=True)
            (p / "animation.gif").write_bytes(b"GIF89a")
        self.m.active = "new"
        self.m.selected = "old"
        await self.m.save()
        self.assertEqual(set(self.m.jobs), {"middle", "new"})
        self.assertFalse((self.m.root / "old").exists())
        self.assertTrue((self.m.root / "new/animation.gif").exists())
        self.assertEqual(self.m.selected, "new")

    async def test_restart_keeps_gallery_and_does_not_repeat_generation(self):
        completed = {**self.job, "id": "done", "status": "delivered", "render": {"frames": 2}}
        self.store.async_load.return_value = {
            "jobs": {"a": self.job, "done": completed},
            "selected": "done",
            "latest": "a",
            "prompt": "saved prompt",
        }
        await self.m.load()
        self.assertEqual(self.m.jobs["a"]["status"], "interrupted")
        self.assertEqual(self.m.jobs["done"]["status"], "delivered")
        self.assertEqual(self.m.prompt, "saved prompt")
        self.assertEqual(self.tasks, [])

    async def test_show_saved_does_not_generate_and_delete_respects_busy(self):
        self.job["render"] = {"frames": 2}
        self.m.selected = "a"

        async def deliver(job):
            job["status"] = "delivered"

        with (
            patch.object(self.m, "deliver", deliver),
            patch.object(google, "video", AsyncMock()) as video,
        ):
            self.assertTrue((await self.m.show())["accepted"])
            video.assert_not_awaited()
        self.m.active = "a"
        with self.assertRaises(Exception):
            await self.m.delete("a")
        self.m.active = None
        await self.m.delete("a")
        self.assertNotIn("a", self.m.jobs)

    async def test_two_frames_require_explicit_selection(self):
        self.assertIs(resolve(self.hass, None), self.m)
        self.hass.data["pixoo_generator"]["managers"]["frame-two"] = object()
        with self.assertRaises(Exception):
            resolve(self.hass, None)
        self.assertIs(resolve(self.hass, "frame-one"), self.m)
        with self.assertRaises(Exception):
            resolve(self.hass, "missing")

    async def test_ai_task_text_provider_and_invalid_result(self):
        cfg = {**self.config, "ai_task_entity": "ai_task.test"}
        with patch.object(
            google,
            "async_generate_data",
            AsyncMock(return_value=SimpleNamespace(data="scene brief")),
        ) as task:
            self.assertEqual(await google.direction(self.hass, cfg, self.job), "scene brief")
            self.assertEqual(task.await_args.kwargs["entity_id"], "ai_task.test")
        with patch.object(
            google, "async_generate_data", AsyncMock(return_value=SimpleNamespace(data={}))
        ):
            with self.assertRaises(google.GenerationError):
                await google.direction(self.hass, cfg, self.job)

    async def test_google_blocked_incomplete_and_completed_direction(self):
        response = AsyncMock()
        response.__aenter__.return_value = response
        response.status = 200
        session = SimpleNamespace(post=Mock(return_value=response))
        usage = {"promptTokenCount": 10}
        with patch.object(google, "async_get_clientsession", return_value=session):
            for data, error in [
                ({"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}}, "blocked"),
                ({"candidates": [{"finishReason": "MAX_TOKENS"}]}, "did not finish"),
            ]:
                response.json.return_value = {**data, "usageMetadata": usage}
                with self.assertRaisesRegex(google.GenerationError, error):
                    await google.direction(self.hass, self.config, self.job)
                self.assertEqual(self.job["direction_usage"], usage)
            response.json.return_value = {
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {
                            "parts": [
                                {"text": "private reasoning", "thought": True},
                                {"text": "scene brief"},
                            ]
                        },
                    }
                ],
                "usageMetadata": usage,
            }
            self.assertEqual(
                await google.direction(self.hass, self.config, self.job), "scene brief"
            )
            self.assertEqual(session.post.call_count, 3)  # One request per invocation; no retries.

    async def test_frame_http_error_and_success_controls(self):
        response = AsyncMock()
        response.__aenter__.return_value = response
        session = SimpleNamespace(post=Mock(return_value=response))
        with patch.object(transport, "async_get_clientsession", return_value=session):
            response.status = 200
            response.json.return_value = {"error_code": 1}
            with self.assertRaises(Exception):
                await transport.post(self.hass, "192.0.2.38", {})
            response.json.return_value = {"error_code": 0}
            self.assertEqual(await transport.post(self.hass, "192.0.2.38", {}), {"error_code": 0})

    async def test_setup_validates_without_generating_video(self):
        flow = config_flow.PixooConfigFlow()
        flow.hass = self.hass
        flow._async_current_entries = lambda: []
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = Mock()
        self.hass.states = SimpleNamespace(
            get=lambda _: SimpleNamespace(attributes={"supported_features": 1})
        )
        with (
            patch.object(config_flow, "post", AsyncMock(return_value={"error_code": 0})),
            patch.object(config_flow, "validate_key", AsyncMock()),
            patch.object(config_flow, "get_url", return_value="http://192.0.2.1:8123"),
            patch.object(google, "video", AsyncMock()) as video,
        ):
            invalid = await flow.async_step_user(
                {"name": "Frame", "host": "127.0.0.1", "base_url": "http://192.0.2.1:8123"}
            )
            self.assertEqual(invalid["errors"]["base"], "invalid_connection")
            step = await flow.async_step_user(
                {"name": "Frame", "host": "192.0.2.38", "base_url": "http://192.0.2.1:8123"}
            )
            self.assertEqual(step["step_id"], "provider")
            done = await flow.async_step_provider(
                {
                    "api_key": "fake",
                    "video_model": "test-video",
                    "director_model": "test-text",
                    "history_limit": 20,
                    "keep_source": False,
                }
            )
            self.assertEqual(done["type"], "create_entry")
            self.assertEqual(done["data"]["host"], "192.0.2.38")
            video.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

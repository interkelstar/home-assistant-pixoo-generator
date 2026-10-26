"""Clean HA setup, real config flow and entities, fake external boundaries."""

import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homeassistant import loader
from homeassistant.bootstrap import async_from_config_dict
from homeassistant.core import HomeAssistant

from custom_components.pixoo_generator import config_flow, google


async def main():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        (root / "custom_components").symlink_to(
            Path(__file__).resolve().parents[1] / "custom_components", target_is_directory=True
        )
        hass = HomeAssistant(str(root))
        loader.async_setup(hass)
        with (
            patch.object(config_flow, "post", AsyncMock(return_value={"error_code": 0})),
            patch.object(config_flow, "validate_key", AsyncMock()),
        ):
            try:
                assert await async_from_config_dict(
                    {
                        "homeassistant": {
                            "name": "Isolated Pixoo test",
                            "latitude": 0,
                            "longitude": 0,
                            "elevation": 0,
                            "unit_system": "metric",
                            "time_zone": "UTC",
                        },
                        "http": {"server_port": 18765, "server_host": "127.0.0.1"},
                    },
                    hass,
                )
                flow = await hass.config_entries.flow.async_init(
                    "pixoo_generator", context={"source": "user"}
                )
                assert flow["step_id"] == "user", flow
                flow = await hass.config_entries.flow.async_configure(
                    flow["flow_id"],
                    {
                        "name": "Test Frame",
                        "host": "192.0.2.38",
                        "base_url": "http://192.0.2.1:8123",
                    },
                )
                assert flow["step_id"] == "provider", flow
                done = await hass.config_entries.flow.async_configure(
                    flow["flow_id"],
                    {
                        "api_key": "test-only",
                        "video_model": "test-video",
                        "director_model": "test-text",
                        "history_limit": 20,
                        "keep_source": False,
                    },
                )
                assert done["type"] == "create_entry", done
                await hass.async_block_till_done()
                entry = done["result"]
                print("entry state", entry.state, flush=True)
                states = [s for s in hass.states.async_all() if "test_frame" in s.entity_id]
                print("entities", [(s.entity_id, s.state) for s in states], flush=True)
                assert len(states) == 8, [(s.entity_id, s.state) for s in states]
                prompt = next(
                    s.entity_id
                    for s in states
                    if s.entity_id.startswith("text.") and "scene" in s.entity_id
                )
                generate = next(
                    s.entity_id
                    for s in states
                    if s.entity_id.startswith("button.") and "create" in s.entity_id
                )
                await hass.services.async_call(
                    "text",
                    "set_value",
                    {"entity_id": prompt, "value": "Robot chef stirring soup"},
                    blocking=True,
                )
                assert entry.runtime_data.prompt == "Robot chef stirring soup"
                with patch.object(
                    google,
                    "direction",
                    AsyncMock(side_effect=google.GenerationError("Controlled provider failure")),
                ):
                    await hass.services.async_call(
                        "button", "press", {"entity_id": generate}, blocking=True
                    )
                    await entry.runtime_data.task
                assert entry.runtime_data.jobs[entry.runtime_data.latest]["status"] == "failed"
                options = await hass.config_entries.options.async_init(entry.entry_id)
                assert options["step_id"] == "init", options
                result = await hass.config_entries.options.async_configure(
                    options["flow_id"],
                    {
                        "api_key": "",
                        "video_model": "test-video",
                        "director_model": "test-text",
                        "host": "192.0.2.38",
                        "base_url": "http://192.0.2.1:8123",
                        "history_limit": 12,
                        "keep_source": False,
                    },
                )
                assert result["type"] == "create_entry", result
                await hass.async_block_till_done()
                assert entry.runtime_data.config["api_key"] == "test-only"
                assert entry.runtime_data.config["history_limit"] == 12
                old_id = entry.entry_id
                assert await hass.config_entries.async_reload(old_id)
                await hass.async_block_till_done()
                assert entry.runtime_data.prompt == "Robot chef stirring soup"
                assert entry.runtime_data.jobs[entry.runtime_data.latest]["status"] == "failed"
                print("clean setup / text / button / controlled failure / reload: PASS", flush=True)
            finally:
                await hass.async_stop(force=True)


asyncio.run(main())

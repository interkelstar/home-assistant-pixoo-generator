"""Direct Pixoo 64 LAN transport; no household rest_command required."""

import ipaddress

import aiohttp
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession


def frame_address(value):
    address = ipaddress.IPv4Address(value.strip())
    if address.is_unspecified or address.is_multicast or address.is_loopback:
        raise ValueError("Use the frame's LAN IPv4 address")
    return str(address)


async def post(hass, host, payload):
    async with async_get_clientsession(hass).post(
        f"http://{frame_address(host)}/post",
        json=payload,
        timeout=aiohttp.ClientTimeout(total=15),
        allow_redirects=False,
    ) as response:
        if response.status != 200:
            raise HomeAssistantError("The frame did not accept the request.")
        try:
            data = await response.json(content_type=None)
        except ValueError:
            raise HomeAssistantError("Invalid response from the frame.") from None
    if not isinstance(data, dict) or data.get("error_code") != 0:
        raise HomeAssistantError("The frame rejected the command.")
    return data

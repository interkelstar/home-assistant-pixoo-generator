"""Shared constants."""

DOMAIN = "pixoo_generator"
PLATFORMS = ["text", "button", "sensor", "select", "image"]
EVENT_JOB = "pixoo_generator_job"
DEFAULT_VIDEO_MODEL = "gemini-omni-1.1-flash"
DEFAULT_DIRECTOR_MODEL = "gemini-3.5-flash-lite"
DEFAULT_STYLE = "polished pixel art"
DEFAULT_HISTORY = 20
BUSY_STATES = {"queued", "planning", "generating", "sending"}

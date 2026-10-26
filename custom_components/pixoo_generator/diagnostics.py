"""No credentials, prompts, IP addresses or capability URLs in diagnostics."""


async def async_get_config_entry_diagnostics(hass, entry):
    manager = entry.runtime_data
    return {
        "version": 1,
        "video_model": manager.config["video_model"],
        "uses_ai_task": bool(manager.config.get("ai_task_entity")),
        "history_limit": manager.config["history_limit"],
        "keep_source": manager.config["keep_source"],
        "job_counts": {
            state: sum(j["status"] == state for j in manager.jobs.values())
            for state in {j["status"] for j in manager.jobs.values()}
        },
    }

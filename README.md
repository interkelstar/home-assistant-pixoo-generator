# Pixoo Animation for Home Assistant

Describe a scene, generate a pixel animation, and display it on a **Divoom Pixoo 64**.
Use a text field and button in Home Assistant, an automation, or an exposed script in
Assist. A voice assistant is optional.

The integration runs inside Home Assistant. Google generates the video; FFmpeg and
Pillow turn it into a looping 64×64 GIF; the frame downloads it over your LAN.
Generation uses **your paid Google API credits**. Setup validates access without
creating a video. There are no automatic paid retries or runtime aesthetic reviews.

## Install

Requires Home Assistant **2026.10 or newer**, a Pixoo 64 on the same reachable LAN,
and a Google Gemini API key with access to the selected video model.

1. In HACS → Custom repositories, add
   `https://github.com/interkelstar/home-assistant-pixoo-generator` as an **Integration**.
2. Download **Pixoo Animation** and restart Home Assistant if requested.
3. Open Settings → Devices & services → Add integration → **Pixoo Animation**.
4. Select an existing `divoom_pixoo` integration, or enter the frame's IPv4 address.
   Enter the **HTTP LAN URL** of Home Assistant that the frame can reach.
5. Enter your Google API key and choose the models and gallery retention.

[Add repository to HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=interkelstar&repository=home-assistant-pixoo-generator&category=integration)

This is a HACS custom repository, not a claim of inclusion in the default catalog.
The existing Divoom integration is optional. Its other controls can coexist; turn
off automatic page rotation while using generated animations.

Home Assistant OS/Container normally provide FFmpeg. Setup explicitly checks for
it; other installations must make `ffmpeg` available to Home Assistant.

## Use without voice

The integration creates one device per configured frame with:

- **Scene** and **Style** text fields.
- **Create and display** button.
- **Animation status** sensor with the current job ID and error, if any.
- **Saved animation** dropdown and an animated **Animation preview** image.
- **Show saved animation** and **Delete selected animation** buttons.

Add these standard entities to a dashboard. Set Scene, then press Create and display.
The text entity accepts 255 characters (HA's state limit); the action accepts 800.
Each frame has its own configuration, gallery and active job. Multiple frames are
supported; explicit `entry_id` is required in actions when more than one is loaded.

## Models and credentials

The tested video backend is **Google Gemini Interactions** with
`gemini-omni-1.1-flash`, requesting a short 360p video. Model IDs are configurable,
but a model must implement this video API; an arbitrary chat model is not compatible.

Scene direction defaults to `gemini-3.5-flash-lite` using the same Google key.
Alternatively select an existing **AI Task entity** that supports text generation.
Its own integration handles that task's provider, credentials and model. Video still
uses the Google key configured here. Home Assistant currently has no generic AI Task
video-generation action.

Credentials stay in the integration's HA config entry and are excluded from diagnostics.
They are not read from another conversation integration. Blank API key in Options keeps
the existing key. Options can also change the frame address, models, source retention,
LAN URL and history limit. Changing options interrupts active generation without retry.

Provider usage metadata is kept per job. Billing rates and availability are controlled
by Google: [pricing](https://ai.google.dev/gemini-api/docs/pricing),
[video API](https://ai.google.dev/gemini-api/docs/omni).

## Actions and optional Assist

`pixoo_generator.generate` accepts:

```yaml
action: pixoo_generator.generate
data:
  text: A tiny dragon blowing smoke rings
  style: warm colors, playful pixel art
  display: true
  notify: true
response_variable: animation
```

The response is `accepted`, `status: queued`, and `job_id`. **Queued does not mean
displayed.** A busy frame rejects a second job without another provider request.
Use `entry_id` to choose a frame when there is more than one.

To use Assist, create a script with a text field that calls this action, returns the
response, and expose that script to your conversation agent. This is the same action
used by the button; the integration has no house-specific assistant prompt or script.

`pixoo_generator.show` accepts `entry_id`, optional `job_id` (otherwise uses the selected
animation), and `notify`. It shows a cached GIF **without generating another video**.
`pixoo_generator.delete` deletes the selected/job ID and its files; deletion is blocked
while a job is active. Use the gallery dropdown to choose the preview/show/delete target.

## Recall and favorites

`pixoo_generator.list_animations` returns saved descriptions, full `job_id` values,
creation timestamps, `created_local_date` in HA's timezone, favorite flags and
`last_displayed_job_id`. It is read-only and excludes failed jobs without a GIF,
provider credentials and download tokens. Expose a wrapper script to Assist so
it can find "yesterday's animation" and call `show` with the returned ID. When
several scenes match an ambiguous request, the assistant should ask which one.

`pixoo_generator.favorite` with `favorite: true` keeps an animation outside the
normal history limit. Pass `job_id`, or omit it to save the last successfully
sent animation. Selecting a gallery preview alone does not change that default.
Favorites have a star in the gallery and survive restarts/updates. Set
`favorite: false` to return to normal retention (an old item may then be removed).
Explicit `delete` still deletes a favorite; removing the integration deletes its
gallery. Favorites are persistent storage, not an independent backup, and their
count is not capped by the ordinary history limit.

## Completion and notifications

The integration fires `pixoo_generator_job` with `entry_id`, `job_id`, `status`,
`text`, `error`, and `notify`. Terminal statuses are `delivered`, `ready` (display
was disabled), and `failed`. It never calls a speaker or household script itself.

For example, an optional notification automation:

```yaml
triggers:
  - trigger: event
    event_type: pixoo_generator_job
conditions:
  - condition: template
    value_template: "{{ trigger.event.data.notify }}"
actions:
  - action: persistent_notification.create
    data:
      title: Pixoo animation
      message: >-
        {{ trigger.event.data.status }}: {{ trigger.event.data.text }}
        {{ trigger.event.data.error or '' }}
```

Filter `entry_id` for a particular frame. For speech, replace the notification action
with your own announcement script. Failed requests never generate a success event.

## Gallery, privacy and cleanup

Files live under `.storage/pixoo_generator_artifacts/<entry_id>/<job_id>/` in your HA
configuration directory; the index/settings live in `.storage/pixoo_generator.<entry_id>`.
They survive restarts, integration reloads and HACS updates. Do not edit them while the
integration is loaded. Include them in your normal HA backup.

By default the last **20 jobs** are kept (configurable 1–100). Old job folders and index
entries are removed together. Favorites are excluded from this limit. A failed request also counts as a job. Lowering the limit
removes older non-favorite jobs; favorites stay until explicitly unpinned or deleted.

Normal output is a GIF, preview and small metadata file. Source MP4 and contact sheets
are retained only with **Keep source video and diagnostic frames** enabled. Changing
that option affects future jobs; it does not erase existing source videos. The provider
input is limited to 30 MB; the limit bounds ordinary history, not favorites or exact disk usage.

Explicitly removing the integration entry deletes **that entry's gallery**. HACS
updates and ordinary reloads do not. Export or back up animations you want to keep
before removing the entry.

The frame cannot send HA authentication headers. Each GIF has a random 128-bit
capability token in its download URL. The URL grants access only to that GIF and remains
valid until deletion. Do not share it inadvertently. HA's authenticated image entity is
used for dashboard previews; raw download tokens are not published in entity attributes.

## What delivery and quality mean

Delivery requires both a successful Pixoo response and a completed HTTP file transfer
to the configured frame IP. Opening the URL from another client is not a delivery.
This confirms transfer, **not a camera observation of the physical LEDs**. If a reverse
proxy hides the frame's source IP, delivery cannot be confirmed: use a direct LAN URL.

The renderer preserves forward motion, uses a shared palette, and chooses similar edge
frames for a loop. It rejects corrupt/static video. Generative results vary: the scene
may simplify the request and loop seams can be visible. There is no promise of perfect
anatomy or seamlessness for every prompt, and no hidden regenerate-until-approved loop.

## Development

The tests require the installed Home Assistant Python environment (2026.10+), not just
Python and Pillow. They do not call Google or a real frame:

```sh
python tests/test_integration.py
python tests/smoke_ha.py
```

The clean HA test runs in a temporary configuration and verifies config flow, native
entities, text/button interaction, controlled provider failure, options and reload.
Unit controls cover invalid media, delivery errors, download tokens, retention, restart,
multiple frames, and the optional AI Task provider. CI also validates the HACS layout
and integration metadata. `brands` is excluded from the HACS custom-repository check;
this repository is not submitted to the default catalog.

## License

MIT. No Google or Divoom affiliation.

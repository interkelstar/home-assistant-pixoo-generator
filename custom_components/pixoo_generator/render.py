"""Convert a generated video to a small looping GIF; never execute generated code."""

import hashlib
import json
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

VIDEO_MODEL = "gemini-omni-1.1-flash"
PROMPT = """Create a 5-second seamless looping pixel-art animation for a 64x64 LED art display.
One single unbroken shot, locked camera, no scene cuts, no zoom. Polished handcrafted 16-bit pixel art, crisp square pixels and bold clusters, approximately 24-32 purposeful colors, no blur, gradients or dithering. A readable large subject and clean silhouette. All meaningful content must fit inside the CENTER SQUARE of the landscape video; side margins are calm background and will be cropped. Keep important silhouettes away from the edges of that square. No labels, typography, letters, numbers, subtitles or UI unless explicitly part of the requested scene. No decorative frame or borders.
Animate the requested action with smooth, visible, coherent movement. Maintain identical character design, anatomy, lighting, camera and stationary background throughout. Return to the initial pose and composition at the end so the loop joins naturally. Do not freeze the scene. Flowing water, smoke and rain move forward, never reverse time. Silent video.
Scene and style requested by the user: {text}. {style}.
Animation direction: {direction}"""
DIRECTION_PROMPT = """You are a pixel animation director. Convert the requested scene into a concise English art and motion brief for one 5-second seamless loop for a 64x64 LED frame. Preserve the subject and requested actions. Choose a readable composition and one or two specific visible motions. Describe the starting pose, the action over the middle of the clip, and the natural return to the starting pose at the end. A wave visibly moves the same paw through an arc; stirring moves the same spoon around the pot rather than waving; flowing water and rain move forward. Distinguish moving and stationary elements. Adapt a complex narrative into a recognizable looping moment. All important objects must fit the center square of the video with margin. Use large expressive features that read at 64x64, no random text, no extra limbs. No tools, code, markdown or evaluation. Output only the brief, at most 180 words.
User scene: {text}
Style: {style}"""


def encode_frames(frames, folder: Path, source: bytes, keep_source=False) -> dict:
    if not 20 <= len(frames) <= 80 or any(f.size != (64, 64) for f in frames):
        raise ValueError("Video must provide 2–8 seconds at 64x64")
    if len({f.tobytes() for f in frames}) < 2:
        raise ValueError("Provider returned a static video")
    # Close the loop by matching poses within a bounded edge window, without
    # reversing the action or blending distinct poses. Keep >= 75% of the clip.
    margin = max(1, len(frames) // 8)
    candidates = []
    for a in range(margin):
        for b in range(len(frames) - margin, len(frames)):
            difference = ImageStat.Stat(ImageChops.difference(frames[a], frames[b]))
            score = sum(difference.mean)
            candidates.append((score, -(b - a), a, b))
    _, _, start, end = min(candidates)
    frames = frames[start:end]
    columns = 8
    rows = math.ceil(len(frames) / columns)
    atlas = Image.new("RGB", (columns * 64, rows * 64))
    for i, f in enumerate(frames):
        atlas.paste(f, ((i % columns) * 64, (i // columns) * 64))
    palette = atlas.quantize(colors=48, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    frames = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    if len({f.tobytes() for f in frames}) < 2:
        raise ValueError("Static sequence after pixel conversion")
    folder.mkdir(parents=True, exist_ok=False)
    if keep_source:
        (folder / "source.mp4").write_bytes(source)
    frames[0].save(
        folder / "animation.gif",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
        disposal=2,
        optimize=False,
    )
    frames[0].save(folder / "preview.png")
    for i, f in enumerate(frames):
        atlas.paste(f.convert("RGB"), ((i % columns) * 64, (i // columns) * 64))
    if keep_source:
        atlas.save(folder / "frames-native.png")
        atlas.resize((atlas.width * 2, atlas.height * 2), Image.Resampling.NEAREST).save(
            folder / "frames-large.png"
        )
    with Image.open(folder / "animation.gif") as gif:
        count = gif.n_frames
        if gif.size != (64, 64) or count < 2 or gif.info.get("loop") != 0:
            raise ValueError("Invalid encoded animation")
        durations = []
        for i in range(count):
            gif.seek(i)
            gif.load()
            durations.append(gif.info.get("duration", 0))
        if any(d < 40 for d in durations):
            raise ValueError("Invalid frame timing")
    result = {
        "width": 64,
        "height": 64,
        "frames": count,
        "duration_ms": sum(durations),
        "selected_source_frames": [start, end],
        "sha256": hashlib.sha256((folder / "animation.gif").read_bytes()).hexdigest(),
    }
    (folder / "render.json").write_text(json.dumps(result, indent=2))
    return result


def render_video(video: bytes, folder: Path, keep_source=False, ffmpeg="ffmpeg") -> dict:
    if not video or len(video) > 30_000_000:
        raise ValueError("Invalid video size")
    command = [
        ffmpeg,
        "-v",
        "error",
        "-nostdin",
        "-i",
        "pipe:0",
        "-an",
        "-t",
        "8",
        "-vf",
        "fps=10,crop=min(iw\\,ih):min(iw\\,ih),scale=64:64:flags=area",
        "-frames:v",
        "80",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]
    result = subprocess.run(command, input=video, capture_output=True, timeout=40, check=True)
    raw = result.stdout
    size = 64 * 64 * 3
    if len(raw) % size:
        raise ValueError("Incomplete video frame")
    frames = [Image.frombytes("RGB", (64, 64), raw[i : i + size]) for i in range(0, len(raw), size)]
    return encode_frames(frames, folder, video, keep_source)

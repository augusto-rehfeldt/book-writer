"""Create a KDP-sized JPEG cover from generated background art."""

from __future__ import annotations

import io
import os
import random
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps


COVER_SIZE = (1600, 2560)


def create_cover(
    prompt: str,
    title: str,
    author: str,
    output_path: str | os.PathLike[str],
    background_path: str | os.PathLike[str] | None = None,
) -> str:
    """Generate or load background art, then add reliable typography."""
    if background_path:
        with Image.open(background_path) as source:
            background = source.convert("RGB")
    else:
        background = _generate_pollinations_background(prompt)
    cover = ImageOps.fit(background.convert("RGB"), COVER_SIZE, Image.Resampling.LANCZOS)
    _add_readability_gradient(cover)

    draw = ImageDraw.Draw(cover)
    title_font, title_lines = _fit_text(draw, title, 1320, 230, bold=True)
    author_font, author_lines = _fit_text(draw, author, 1200, 90)
    _draw_centered(draw, title_lines, title_font, 300, stroke_width=4)
    _draw_centered(draw, author_lines, author_font, 2240, stroke_width=3)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    cover.save(output, "JPEG", quality=95, subsampling=0, dpi=(300, 300))
    return str(output)


def _generate_pollinations_background(prompt: str) -> Image.Image:
    key = os.getenv("POLLINATIONS_API_KEY", "").strip()
    model = os.getenv("AI_BOOK_COVER_MODEL", "flux").strip() or "flux"
    seed = random.randint(1, 2_147_483_647)
    url = (
        "https://gen.pollinations.ai/image/"
        if key
        else "https://image.pollinations.ai/prompt/"
    ) + quote(prompt, safe="")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    response = requests.get(
        url,
        headers=headers,
        params={
            "model": model,
            "width": COVER_SIZE[0],
            "height": COVER_SIZE[1],
            "seed": seed,
            "nologo": "true",
        },
        timeout=300,
    )
    response.raise_for_status()
    try:
        image = Image.open(io.BytesIO(response.content))
        image.load()
        return image
    except Exception as exc:
        raise RuntimeError("The cover provider returned data that was not a usable image.") from exc


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    configured = os.getenv("AI_BOOK_COVER_FONT", "").strip()
    candidates = [
        configured,
        "C:/Windows/Fonts/georgiab.ttf" if bold else "C:/Windows/Fonts/georgia.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size=size)


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    max_size: int,
    bold: bool = False,
) -> tuple[ImageFont.ImageFont, str]:
    words = text.split() or ["Untitled"]
    for size in range(max_size, 47, -8):
        font = _font(size, bold)
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
        rendered = "\n".join(lines)
        box = draw.multiline_textbbox((0, 0), rendered, font=font, spacing=round(size * 0.18), align="center")
        if box[2] - box[0] <= max_width and box[3] - box[1] <= 900:
            return font, rendered
    return _font(48, bold), "\n".join(words)


def _add_readability_gradient(image: Image.Image) -> None:
    width, height = image.size
    mask = Image.new("L", (1, height))
    mask.putdata(
        [
            max(max(0, 175 - int(y * 0.45)), max(0, 190 - int((height - y) * 0.55)))
            for y in range(height)
        ]
    )
    image.paste(Image.new("RGB", image.size, "black"), mask=mask.resize((width, height)))


def _draw_centered(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    y: int,
    stroke_width: int,
) -> None:
    box = draw.multiline_textbbox((0, 0), text, font=font, spacing=20, align="center", stroke_width=stroke_width)
    x = (COVER_SIZE[0] - (box[2] - box[0])) // 2
    draw.multiline_text(
        (x, y),
        text,
        font=font,
        fill="white",
        spacing=20,
        align="center",
        stroke_width=stroke_width,
        stroke_fill="#111111",
    )

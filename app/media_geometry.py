"""Deterministic reference framing. Fit preserves every source pixel; it is not face detection."""
from __future__ import annotations

from io import BytesIO
from PIL import Image, ImageOps, UnidentifiedImageError

FRAMING = ("center", "fit")


def fit_reference(data: bytes, width: int, height: int) -> tuple[bytes, dict]:
    """EXIF-normalise, fit inside canvas, pad neutrally; never stretch or crop anatomy."""
    try:
        with Image.open(BytesIO(data)) as source:
            if source.width * source.height > 40_000_000:
                raise ValueError("Фото больше 40 МП")
            image = ImageOps.exif_transpose(source).convert("RGB")
            original = image.size
            fitted = ImageOps.contain(image, (width, height), Image.Resampling.LANCZOS)
            offset = ((width - fitted.width) // 2, (height - fitted.height) // 2)
            canvas = Image.new("RGB", (width, height), (127, 127, 127))
            canvas.paste(fitted, offset)
            buffer = BytesIO()
            canvas.save(buffer, format="PNG")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Не удалось прочитать фото для сохранения композиции") from exc
    return buffer.getvalue(), {"version": 1, "policy": "fit", "source_size": list(original),
                              "canvas_size": [width, height], "content_size": list(fitted.size),
                              "offset": list(offset), "padding_rgb": [127, 127, 127]}

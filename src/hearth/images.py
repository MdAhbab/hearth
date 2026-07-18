"""Image preparation for the vision-capable local model.

Everything funnels through one function so the size policy lives in one
place: images are downscaled to a bounded edge and re-encoded as JPEG
before being base64'd into the model context. On an 8 GB machine an
unscaled 12-MP photo would blow both memory and the context window.
"""

from __future__ import annotations

import base64
from pathlib import Path

MAX_EDGE_PX = 1024
JPEG_QUALITY = 85
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".heic"}


class ImageError(Exception):
    pass


def encode_image_file(path: str | Path, max_edge: int = MAX_EDGE_PX) -> str:
    """Load, downscale, and return base64-JPEG for an image file."""
    from PySide6.QtGui import QImage

    image = QImage(str(path))
    if image.isNull():
        raise ImageError(f"Could not read image: {path}")
    return encode_qimage(image, max_edge)


def encode_qimage(image, max_edge: int = MAX_EDGE_PX) -> str:
    """Downscale a QImage and return base64-JPEG."""
    from PySide6.QtCore import QBuffer, QIODevice, Qt
    from PySide6.QtGui import QImage

    if image.isNull():
        raise ImageError("Empty image")
    if max(image.width(), image.height()) > max_edge:
        image = image.scaled(
            max_edge,
            max_edge,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    # JPEG can't hold alpha; flatten transparent images onto white.
    if image.hasAlphaChannel():
        image = image.convertToFormat(QImage.Format.Format_RGB32)

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "JPEG", JPEG_QUALITY):
        raise ImageError("Could not encode image")
    return base64.b64encode(bytes(buffer.data())).decode()


def is_image_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_SUFFIXES

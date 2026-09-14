"""Validation of images attached to a chat message.

The format is decided by the file's magic bytes. The client controls both the
file extension and any declared MIME type, so neither is trusted.
"""

import base64
import binascii
import re

import settings

MAX_IMAGE_BYTES = int(settings.MAX_IMAGE_MB * 1024 * 1024)

# Optional "data:<anything>;base64," prefix, as produced by FileReader.readAsDataURL.
# The declared type in it is discarded; only the bytes decide.
_DATA_URI_PREFIX = re.compile(r"^data:[^,]*;base64,", re.IGNORECASE)


class ImageValidationError(ValueError):
    """Raised with a user-facing (Spanish) message."""


def detect_media_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_image(payload: str) -> tuple[str, str]:
    """Return (clean base64, media type) or raise ImageValidationError."""
    encoded = _DATA_URI_PREFIX.sub("", payload.strip(), count=1)
    encoded = "".join(encoded.split())  # tolerate line-wrapped base64

    # Cheap upper bound before allocating the decoded buffer: 4 base64 chars -> 3 bytes.
    if len(encoded) * 3 // 4 > MAX_IMAGE_BYTES + 2:
        raise ImageValidationError(f"La imagen supera el tamaño máximo de {settings.MAX_IMAGE_MB:g} MB.")

    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ImageValidationError("La imagen no está codificada correctamente en base64.") from None

    if not data:
        raise ImageValidationError("La imagen está vacía.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageValidationError(f"La imagen supera el tamaño máximo de {settings.MAX_IMAGE_MB:g} MB.")

    media_type = detect_media_type(data)
    if media_type is None:
        raise ImageValidationError("Formato de imagen no soportado. Usa JPEG, PNG, GIF o WebP.")

    # Re-encode from the verified bytes so what reaches the provider is exactly what was checked.
    return base64.b64encode(data).decode("ascii"), media_type

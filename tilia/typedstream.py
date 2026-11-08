from __future__ import annotations


def decode_attributed_body(blob: bytes | None) -> str | None:
    """Extract the plain text from an iMessage NSAttributedString typedstream blob."""
    if not isinstance(blob, (bytes, bytearray, memoryview)) or not blob:
        return None  # NULL, or a corrupt row holding some other type
    blob = bytes(blob)
    i = blob.find(b"NSString")
    if i < 0:
        return None
    i = blob.find(b"\x2b", i + len(b"NSString"))
    if i < 0:
        return None
    i += 1
    if i >= len(blob):
        return None
    marker = blob[i]
    if marker == 0x81:
        width, i = 2, i + 1
    elif marker == 0x82:
        width, i = 4, i + 1
    else:
        width = 0
    if width:
        if i + width > len(blob):
            return None
        length = int.from_bytes(blob[i : i + width], "little")
        i += width
    else:
        length = marker
        i += 1
    if i + length > len(blob):
        return None
    try:
        return blob[i : i + length].decode("utf-8")
    except UnicodeDecodeError:
        return None

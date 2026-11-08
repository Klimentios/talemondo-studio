from __future__ import annotations


LIMITS = {"image": 25 * 1024 * 1024, "video": 500 * 1024 * 1024, "audio": 100 * 1024 * 1024}


def detect_media(header: bytes) -> tuple[str, str]:
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image", "image/jpeg"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image", "image/webp"
    if header.startswith(b"RIFF") and header[8:12] == b"WAVE":
        return "audio", "audio/wav"
    if header.startswith(b"fLaC"):
        return "audio", "audio/flac"
    if header.startswith(b"OggS"):
        return "audio", "audio/ogg"
    if header.startswith(b"ID3") or (len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0):
        return "audio", "audio/mpeg"
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        return "video", "video/webm"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        brand = header[8:12]
        if brand in {b"M4A ", b"M4B ", b"M4P "}:
            return "audio", "audio/mp4"
        if brand == b"qt  ":
            return "video", "video/quicktime"
        return "video", "video/mp4"
    raise ValueError("Формат файла не распознан; поддерживаются PNG/JPEG/WebP, MP4/MOV/WebM, WAV/FLAC/MP3/M4A/OGG")

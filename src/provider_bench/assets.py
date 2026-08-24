from __future__ import annotations

from typing import Any

# A 1x1 transparent PNG, widely used as a minimal image fixture.
PNG_1PX_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

# A 1x1 transparent GIF used as a minimal placeholder for video-frame content.
# Real providers may expect a genuine video codec; keep the content type
# configurable so callers can point at a real asset when needed.
GIF_1PX_BASE64 = "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"


def image_content(prompt: str) -> list[dict[str, Any]]:
    return [
        {"type": "text", "text": prompt},
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{PNG_1PX_BASE64}"},
        },
    ]


def video_content(prompt: str) -> list[dict[str, Any]]:
    return [
        {"type": "text", "text": prompt},
        {
            "type": "video_url",
            "video_url": {"url": f"data:video/gif;base64,{GIF_1PX_BASE64}"},
        },
    ]

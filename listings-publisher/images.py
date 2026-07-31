"""Image utilities: sort by EXIF capture time, group per listing, upload to eBay Media API."""
import mimetypes
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
_EXIF_DATETIME_ORIGINAL = 36867
_MEDIA_API_URL = "https://apim.ebay.com/commerce/media/v1_beta/image/create_image_from_file"


def scan_and_sort(folder: Path) -> list[Path]:
    """Return images in folder sorted by EXIF capture time, then filename as tiebreaker.

    iPhone photos taken in quick succession share the same second-level timestamp.
    Using filename as tiebreaker preserves sequential order (IMG_6538, IMG_6539, ...).
    """
    found = [f for f in folder.iterdir() if f.suffix.lower() in IMAGE_EXTS]
    if not found:
        raise ValueError(f"No images found in {folder}")
    return sorted(found, key=lambda p: (_capture_time(p), p.name))


def group(images: list[Path], per_listing: int) -> list[list[Path]]:
    return [images[i : i + per_listing] for i in range(0, len(images), per_listing)]


def upload(path: Path, token: str) -> str:
    """Upload image to eBay Media API and return the i.ebayimg.com URL."""
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    with open(path, "rb") as f:
        response = requests.post(
            _MEDIA_API_URL,
            headers={"Authorization": f"Bearer {token}"},
            files={"image": (path.name, f, mime)},
        )
    if response.status_code != 201:
        raise RuntimeError(f"Media API upload failed: {response.status_code} — {response.text}")
    return response.json()["imageUrl"]


def _capture_time(path: Path) -> datetime:
    try:
        img = Image.open(path)
        exif = img.getexif()
        # DateTimeOriginal lives in the ExifIFD sub-table (tag 34665), not the main IFD
        dt_str = exif.get_ifd(34665).get(_EXIF_DATETIME_ORIGINAL)
        if dt_str:
            return datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    return datetime.fromtimestamp(path.stat().st_mtime)

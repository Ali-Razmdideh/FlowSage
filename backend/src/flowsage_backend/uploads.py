"""Bounded, safe staging for user-supplied screenshot uploads."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import UploadFile

from flowsage_backend.simulations import IMAGE_SUFFIXES

MAX_SIMULATION_UPLOAD_FILES = 50
MAX_SIMULATION_UPLOAD_BYTES_PER_FILE = 10 * 1024 * 1024
MAX_SIMULATION_UPLOAD_BYTES_TOTAL = 50 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024


class UploadValidationError(ValueError):
    """An upload does not meet the service's bounded image-upload contract."""


async def stage_image_uploads(files: list[UploadFile], destination: Path) -> list[str]:
    """Stream uploads and remove partial data whenever staging fails."""
    if not files:
        raise UploadValidationError("At least one screenshot is required")
    if len(files) > MAX_SIMULATION_UPLOAD_FILES:
        raise UploadValidationError(
            f"At most {MAX_SIMULATION_UPLOAD_FILES} screenshots may be uploaded at once"
        )

    filenames: list[str] = []
    seen: set[str] = set()
    for upload in files:
        filename = Path(upload.filename or "").name
        if (
            not filename
            or filename in {".", ".."}
            or Path(filename).suffix.lower() not in IMAGE_SUFFIXES
        ):
            raise UploadValidationError(f"Unsupported file type: {filename!r}")
        if filename in seen:
            raise UploadValidationError(f"Duplicate screenshot filename: {filename!r}")
        filenames.append(filename)
        seen.add(filename)

    destination.mkdir(parents=True, exist_ok=False)
    total_bytes = 0
    try:
        for upload, filename in zip(files, filenames, strict=True):
            file_bytes = 0
            with (destination / filename).open("xb") as output:
                while chunk := await upload.read(_READ_CHUNK_BYTES):
                    file_bytes += len(chunk)
                    total_bytes += len(chunk)
                    if file_bytes > MAX_SIMULATION_UPLOAD_BYTES_PER_FILE:
                        raise UploadValidationError(
                            f"Screenshot {filename!r} exceeds the "
                            f"{MAX_SIMULATION_UPLOAD_BYTES_PER_FILE // (1024 * 1024)} MiB limit"
                        )
                    if total_bytes > MAX_SIMULATION_UPLOAD_BYTES_TOTAL:
                        raise UploadValidationError(
                            f"Screenshots exceed the {MAX_SIMULATION_UPLOAD_BYTES_TOTAL // (1024 * 1024)} MiB total limit"
                        )
                    output.write(chunk)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    finally:
        for upload in files:
            await upload.close()
    return filenames

import os
import tempfile
import uuid
from typing import Optional

from fastapi import HTTPException, UploadFile, status


MAX_COUPON_IMAGE_BYTES = 5 * 1024 * 1024
COUPON_IMAGE_CHUNK_BYTES = 256 * 1024


def _detect_image_extension(header: bytes) -> Optional[str]:
    if header.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return ".webp"
    return None


async def store_coupon_image(coupon_image: Optional[UploadFile], *, target_dir: str) -> Optional[str]:
    if not coupon_image or not coupon_image.filename:
        return None

    os.makedirs(target_dir, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix="coupon-upload-", suffix=".tmp", dir=target_dir)
    total_size = 0
    header = b""

    try:
        with os.fdopen(fd, "wb") as temp_file:
            while True:
                chunk = await coupon_image.read(COUPON_IMAGE_CHUNK_BYTES)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > MAX_COUPON_IMAGE_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Файл слишком большой. Максимум 5 МБ.",
                    )
                if len(header) < 32:
                    header = (header + chunk)[:32]
                temp_file.write(chunk)

        detected_ext = _detect_image_extension(header)
        if not detected_ext:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Недопустимый формат файла. Разрешены: .gif, .jpg, .png, .webp",
            )

        filename = f"{uuid.uuid4().hex}{detected_ext}"
        final_path = os.path.join(target_dir, filename)
        os.replace(temp_path, final_path)
        return f"/static/coupons/{filename}"
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise
    finally:
        await coupon_image.close()

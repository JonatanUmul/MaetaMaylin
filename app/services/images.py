import io
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.datastructures import UploadFile

UPLOAD_DIR = Path(__file__).parents[1] / "static" / "uploads"
MAX_BYTES = 5 * 1024 * 1024


def encode_image(content: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise HTTPException(422, "La foto debe ser JPG, PNG o WEBP")
            if image.width * image.height > 20_000_000:
                raise HTTPException(422, "La imagen supera 20 megapíxeles")
            image = ImageOps.exif_transpose(image)
            image.thumbnail((1600, 1600))
            if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                rgba = image.convert("RGBA")
                output = Image.new("RGB", image.size, "white")
                output.paste(rgba, mask=rgba.getchannel("A"))
            else:
                output = image.convert("RGB")
            buffer = io.BytesIO()
            output.save(buffer, format="JPEG", quality=88, optimize=True)
            return buffer.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise HTTPException(422, "No se pudo leer la imagen") from error


async def save_upload(photo: UploadFile) -> str:
    from starlette.concurrency import run_in_threadpool

    content = await photo.read(MAX_BYTES + 1)
    if not content or len(content) > MAX_BYTES:
        raise HTTPException(422, "La foto debe pesar como máximo 5 MB")
    encoded = await run_in_threadpool(encode_image, content)
    UPLOAD_DIR.mkdir(exist_ok=True)
    name = f"{uuid4().hex}.jpg"
    await run_in_threadpool((UPLOAD_DIR / name).write_bytes, encoded)
    return f"/static/uploads/{name}"

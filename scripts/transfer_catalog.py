"""Copy local catalog and social links into an empty hosted catalog.

No orders, credentials, notification jobs or visitor records are transferred.
The destination URL is read from a private file, never command-line arguments.
"""

import argparse
import asyncio
import shutil
from pathlib import Path

from sqlalchemy import func, insert, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Config, get_config
from app.models import Category, Product, Setting
from app.services.social import SocialSettings

ROOT = Path(__file__).resolve().parents[1]


def published_image(url):
    if not url.startswith("/static/uploads/"):
        return url
    name = Path(url).name
    source = ROOT / "app" / "static" / "uploads" / name
    if url != f"/static/uploads/{name}" or not source.is_file():
        raise ValueError("Missing local product image")
    target = ROOT / "app" / "static" / "images" / f"catalog-{name}"
    shutil.copyfile(source, target)
    return f"/static/images/{target.name}"


async def transfer(args):
    local = get_config()
    source = create_async_engine(
        local.database_url, connect_args=local.database_connect_args()
    )
    try:
        async with source.connect() as connection:
            categories = [
                dict(row)
                for row in (
                    await connection.execute(select(Category.__table__))
                ).mappings()
            ]
            products = [
                dict(row)
                for row in (
                    await connection.execute(select(Product.__table__))
                ).mappings()
            ]
            social = (
                await connection.execute(
                    select(Setting.value).where(Setting.key == "social_links")
                )
            ).scalar_one_or_none()
        for product in products:
            product["image_url"] = published_image(product["image_url"])
        if args.prepare_images:
            print(f"Prepared images for {len(products)} products")
            return
        destination = Config(
            _env_file=None,
            database_url=Path(args.destination_file).read_text().strip(),
        )
        if destination.database_url == local.database_url:
            raise ValueError("Source and destination must differ")
        target = create_async_engine(
            destination.database_url,
            connect_args=destination.database_connect_args(),
        )
        try:
            async with target.begin() as connection:
                for table in (Category.__table__, Product.__table__):
                    count = await connection.scalar(
                        select(func.count()).select_from(table)
                    )
                    if count:
                        raise ValueError("Destination catalog must be empty")
                if categories:
                    await connection.execute(insert(Category), categories)
                if products:
                    await connection.execute(insert(Product), products)
                if social is not None:
                    validated = SocialSettings(**social).model_dump()
                    from sqlalchemy.dialects.postgresql import insert as pg_insert

                    statement = pg_insert(Setting).values(
                        key="social_links", value=validated
                    )
                    await connection.execute(statement.on_conflict_do_nothing())
                for table in ("categories", "products"):
                    await connection.execute(
                        text(
                            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                            f"COALESCE((SELECT max(id) FROM {table}), 1), "
                            f"EXISTS(SELECT 1 FROM {table}))"
                        )
                    )
                private = await connection.scalar(
                    text(
                        "SELECT bool_and(relrowsecurity) FROM pg_class "
                        "WHERE oid IN ('public.products'::regclass, "
                        "'public.settings'::regclass, 'public.orders'::regclass)"
                    )
                )
                if not private:
                    raise ValueError("Hosted table privacy is not configured")
            print(
                f"Transferred {len(categories)} categories, {len(products)} products and social links"
            )
        finally:
            await target.dispose()
    finally:
        await source.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-images", action="store_true")
    parser.add_argument("--destination-file")
    args = parser.parse_args()
    if not args.prepare_images and not args.destination_file:
        parser.error("--destination-file is required for transfer")
    try:
        asyncio.run(transfer(args))
    except Exception as error:  # noqa: BLE001 -- sanitize database errors at CLI boundary
        # Avoid leaking database passwords or private SQL parameters in tracebacks.
        raise SystemExit(f"Transfer failed: {type(error).__name__}") from None

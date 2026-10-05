"""Add sample products only when the catalog is empty."""

import asyncio
from decimal import Decimal

from sqlalchemy import func, select

from app.db import Session, engine
from app.models import Category, Product, Setting


async def seed():
    async with Session() as session, session.begin():
        if await session.scalar(select(func.count()).select_from(Product)):
            print("Catálogo existente: no se modificó ningún producto.")
            return
        categories = {}
        for name, slug in [("Limpiapipas", "limpiapipas"), ("Regalos", "regalos")]:
            category = await session.scalar(
                select(Category).where(Category.slug == slug)
            )
            if category is None:
                category = Category(name=name, slug=slug)
                session.add(category)
                await session.flush()
            categories[slug] = category.id
        for title, image, price, stock, category in [
            ("Un ramo de cariño", "bouquet", "125.00", 12, "limpiapipas"),
            ("Girasoles para sonreír", "sunflower", "95.00", 8, "limpiapipas"),
            ("Un poquito de lavanda", "lavender", "85.00", 15, "limpiapipas"),
            ("Margaritas para ti", "daisy", "75.00", 10, "limpiapipas"),
            ("Rosas que se quedan", "pink", "110.00", 0, "limpiapipas"),
            ("Un detalle especial", "gift", "145.00", 6, "regalos"),
        ]:
            session.add(
                Product(
                    title=title,
                    description="Flores artesanales para convertir un día "
                    "cualquiera en un recuerdo especial. Producto de demostración: "
                    "ilustración y precio de ejemplo.",
                    image_url=f"/static/images/{image}.svg",
                    price=Decimal(price),
                    stock=stock,
                    category_id=categories[category],
                )
            )
        session.add(Setting(key="demo_mode", value={"enabled": True}))
    await engine.dispose()
    print("Se añadieron seis productos de demostración.")


if __name__ == "__main__":
    asyncio.run(seed())

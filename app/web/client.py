from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_config
from app.db import get_session
from app.models import Category, Product, Setting
from app.services.social import SocialSettings

router = APIRouter(tags=["Tienda"])
templates = Jinja2Templates(directory=Path(__file__).parents[1] / "templates")
Db = Annotated[AsyncSession, Depends(get_session)]


async def context(session):
    demo = await session.get(Setting, "demo_mode")
    social = await session.get(Setting, "social_links")
    return {
        "social_links": SocialSettings(**(social.value if social else {})).links(),
        "currency": get_config().currency,
        "demo": bool(demo and demo.value.get("enabled")),
    }


@router.get("/")
async def catalog(
    request: Request,
    session: Db,
    q: str = Query(default="", max_length=100),
    category: int | None = Query(default=None, gt=0),
    section: Literal["flowers", "teaching"] | None = None,
    page: int = Query(default=1, ge=1, le=10000),
):
    query = select(Product).where(Product.active.is_(True))
    if section:
        slug = "limpiapipas" if section == "flowers" else "material-didactico"
        query = query.join(Category).where(Category.slug == slug)
    if q.strip():
        query = query.where(Product.title.contains(q.strip(), autoescape=True))
    if category:
        query = query.where(Product.category_id == category)
    products = list(
        (
            await session.scalars(
                query.order_by(Product.id).offset((page - 1) * 12).limit(13)
            )
        ).all()
    )
    categories = (await session.scalars(select(Category).order_by(Category.name))).all()
    return templates.TemplateResponse(
        request=request,
        name="client/catalog.html",
        context={
            **await context(session),
            "products": products[:12],
            "categories": categories,
            "q": q,
            "section": section,
            "flower_ids": [c.id for c in categories if c.slug == "limpiapipas"],
            "teaching_ids": [
                c.id for c in categories if c.slug == "material-didactico"
            ],
            "category": category,
            "page": page,
            "has_next": len(products) > 12,
        },
    )


@router.get("/productos/{product_id}")
async def product_detail(request: Request, product_id: int, session: Db):
    product = await session.get(Product, product_id)
    if product is None or not product.active:
        raise HTTPException(404, "Producto no disponible")
    category = await session.get(Category, product.category_id)
    return templates.TemplateResponse(
        request=request,
        name="client/product.html",
        context={**await context(session), "product": product, "category": category},
    )


@router.get("/carrito")
async def cart(request: Request, session: Db):
    return templates.TemplateResponse(
        request=request, name="client/cart.html", context=await context(session)
    )


@router.get("/checkout")
async def checkout_page(request: Request, session: Db):
    return templates.TemplateResponse(
        request=request, name="client/checkout.html", context=await context(session)
    )


@router.get("/api/catalog/products")
async def cart_products(session: Db, ids: Annotated[list[int], Query(max_length=100)]):
    products = (
        await session.scalars(
            select(Product).where(
                Product.id.in_(ids),
                Product.active.is_(True),
            )
        )
    ).all()
    return [
        {
            "id": p.id,
            "title": p.title,
            "image_url": p.image_url,
        }
        for p in products
    ]

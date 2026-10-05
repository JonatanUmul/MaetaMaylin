import secrets
from pathlib import Path
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.config import get_config
from app.db import get_session
from app.models import (
    Category,
    DailyTraffic,
    NotificationOutbox,
    Order,
    OrderStatus,
    Product,
    Setting,
)
from app.schemas import AdminProductInput, CategoryInput, StatusInput
from app.security.admin import (
    LoginInput,
    check_login_limit,
    credential_stamp,
    csrf_token,
    failed_login,
    require_admin,
    verify_csrf,
    verify_password,
)
from app.services.images import save_upload
from app.services.orders import change_status
from app.services.social import SocialSettings

router = APIRouter()
protected = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory=Path(__file__).parents[1] / "templates")
templates.env.filters["localtime"] = lambda date: date.astimezone(
    ZoneInfo("America/Guatemala")
).strftime("%d/%m/%Y %H:%M")
Db = Annotated[AsyncSession, Depends(get_session)]


def view(request, name, context=None, status=200):
    flash = request.session.pop("flash", None)
    return templates.TemplateResponse(
        request=request,
        name=f"admin/{name}.html",
        context={
            "csrf": csrf_token(request),
            "currency": get_config().currency,
            "admin": get_config().admin_username,
            "flash": flash,
            **(context or {}),
        },
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


def redirect(request, path, message=None):
    if message:
        request.session["flash"] = message
    return RedirectResponse(path, status_code=303)


def validation_message(error):
    return "; ".join(
        f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()
    )


@router.get("/admin/login")
async def login_page(request: Request):
    credential_stamp()
    return view(request, "login")


@router.post("/admin/login")
async def login(request: Request):
    stamp = credential_stamp()
    ip = check_login_limit(request)
    async with request.form(max_files=0, max_fields=4) as form:
        verify_csrf(request, str(form.get("csrf", "")))
        try:
            data = LoginInput.model_validate(
                {"username": form.get("username"), "password": form.get("password")}
            )
        except ValidationError:
            failed_login(ip)
            return view(request, "login", {"error": "Credenciales inválidas"}, 401)
    config = get_config()
    valid = await run_in_threadpool(
        verify_password, data.password, config.admin_password_hash.get_secret_value()
    )
    if not valid or not secrets.compare_digest(data.username, config.admin_username):
        failed_login(ip)
        return view(request, "login", {"error": "Credenciales inválidas"}, 401)
    request.session.clear()
    request.session.update(
        {
            "admin": config.admin_username,
            "credential_stamp": stamp,
            "csrf": secrets.token_urlsafe(32),
        }
    )
    return redirect(request, "/admin")


@protected.post("/logout")
async def logout(request: Request):
    async with request.form(max_files=0, max_fields=1) as form:
        verify_csrf(request, str(form.get("csrf", "")))
    request.session.clear()
    return redirect(request, "/admin/login")


@protected.get("")
async def dashboard(request: Request, session: Db):
    counts = dict(
        (
            await session.execute(
                select(Order.status, func.count()).group_by(Order.status)
            )
        ).all()
    )
    recent = (
        await session.scalars(select(Order).order_by(Order.created_at.desc()).limit(6))
    ).all()
    return view(
        request,
        "dashboard",
        {
            "counts": counts,
            "statuses": OrderStatus,
            "recent": recent,
            "product_count": await session.scalar(
                select(func.count()).select_from(Product)
            ),
            "low_stock": await session.scalar(
                select(func.count())
                .select_from(Product)
                .where(Product.active.is_(True), Product.stock <= 3)
            ),
            "failed_notifications": await session.scalar(
                select(func.count())
                .select_from(NotificationOutbox)
                .where(NotificationOutbox.failed_at.is_not(None))
            ),
            "notifications_enabled": get_config().evolution_enabled,
        },
    )


@protected.get("/products")
async def products(
    request: Request,
    session: Db,
    q: str = Query(default="", max_length=100),
    page: int = Query(default=1, ge=1, le=10000),
):
    query = select(Product).options(selectinload(Product.category))
    if q.strip():
        query = query.where(
            func.lower(Product.title).contains(q.strip().lower(), autoescape=True)
        )
    rows = list(
        (
            await session.scalars(
                query.order_by(Product.id.desc()).offset((page - 1) * 20).limit(21)
            )
        ).all()
    )
    return view(
        request,
        "products",
        {"products": rows[:20], "q": q, "page": page, "has_next": len(rows) > 20},
    )


async def product_form(
    request, session, product=None, error=None, values=None, status=200
):
    categories = (await session.scalars(select(Category).order_by(Category.name))).all()
    return view(
        request,
        "product_form",
        {
            "product": product,
            "categories": categories,
            "values": values or {},
            "error": error,
        },
        status,
    )


@protected.get("/products/new")
async def new_product(request: Request, session: Db):
    return await product_form(request, session)


@protected.get("/products/{product_id}/edit")
async def edit_product(request: Request, product_id: int, session: Db):
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "Producto no encontrado")
    return await product_form(request, session, product)


async def save_product(request, session, product_id=None):
    uploaded_path = None
    values = {}
    product = None
    try:
        async with request.form(max_files=1, max_fields=12) as form:
            verify_csrf(request, str(form.get("csrf", "")))
            values = {
                name: str(form.get(name, ""))
                for name in [
                    "title",
                    "description",
                    "price",
                    "category_id",
                    "image_url",
                    "version",
                ]
            }
            values["active"] = form.get("active") == "on"
            data = dict(values)
            data.pop("version")
            data["category_id"] = int(data["category_id"])
            photo = form.get("photo")
            if isinstance(photo, UploadFile) and photo.filename:
                uploaded_path = await save_upload(photo)
                data["image_url"] = uploaded_path
            validated = AdminProductInput.model_validate(data)
            version = int(values["version"]) if product_id is not None else None
        async with session.begin():
            if await session.get(Category, validated.category_id) is None:
                raise HTTPException(422, "La categoría no existe")
            if product_id is not None:
                product = await session.scalar(
                    select(Product).where(Product.id == product_id).with_for_update()
                )
                if product is None:
                    raise HTTPException(404, "Producto no encontrado")
                if product.version != version:
                    raise HTTPException(
                        409,
                        "El producto cambió. Recarga antes de guardar.",
                    )
                for name, value in validated.model_dump(exclude={"stock"}).items():
                    setattr(product, name, value)
            else:
                product = Product(**validated.model_dump())
                session.add(product)
            await session.flush()
    except (ValidationError, ValueError, HTTPException) as error:
        if isinstance(error, HTTPException) and error.status_code in {403, 404}:
            raise
        if uploaded_path:
            (Path(__file__).parents[1] / uploaded_path.lstrip("/")).unlink(
                missing_ok=True
            )
        message = (
            validation_message(error)
            if isinstance(error, ValidationError)
            else (
                error.detail
                if isinstance(error, HTTPException)
                else "Revisa precio y categoría"
            )
        )
        # Transaction context has rolled back before redisplaying the form.
        current = await session.get(Product, product_id) if product_id else None
        return await product_form(
            request,
            session,
            current,
            message,
            values,
            error.status_code if isinstance(error, HTTPException) else 422,
        )
    return redirect(request, "/admin/products", "Producto guardado correctamente")


@protected.post("/products/new")
async def create_product(request: Request, session: Db):
    return await save_product(request, session)


@protected.post("/products/{product_id}/edit")
async def update_product(request: Request, product_id: int, session: Db):
    return await save_product(request, session, product_id)


@protected.post("/products/{product_id}/archive")
async def archive_product(request: Request, product_id: int, session: Db):
    async with request.form(max_files=0, max_fields=2) as form:
        verify_csrf(request, str(form.get("csrf", "")))
        try:
            version = int(str(form.get("version", "")))
        except ValueError as error:
            raise HTTPException(422, "Versión inválida") from error
    async with session.begin():
        product = await session.scalar(
            select(Product).where(Product.id == product_id).with_for_update()
        )
        if product is None:
            raise HTTPException(404, "Producto no encontrado")
        if product.version != version:
            raise HTTPException(409, "El producto cambió. Recarga la lista.")
        product.active = False
    return redirect(
        request, "/admin/products", "Producto archivado; su historial se conserva"
    )


@protected.get("/categories")
async def categories(request: Request, session: Db):
    rows = (await session.scalars(select(Category).order_by(Category.name))).all()
    return view(request, "categories", {"categories": rows})


@protected.post("/categories")
async def create_category(request: Request, session: Db):
    try:
        async with request.form(max_files=0, max_fields=3) as form:
            verify_csrf(request, str(form.get("csrf", "")))
            data = CategoryInput.model_validate(
                {"name": form.get("name"), "slug": form.get("slug")}
            )
        async with session.begin():
            session.add(Category(**data.model_dump()))
            await session.flush()
    except (ValidationError, IntegrityError) as error:
        rows = (await session.scalars(select(Category).order_by(Category.name))).all()
        message = (
            validation_message(error)
            if isinstance(error, ValidationError)
            else "Ya existe ese nombre o identificador"
        )
        return view(request, "categories", {"categories": rows, "error": message}, 422)
    return redirect(request, "/admin/categories", "Categoría creada")


@protected.get("/orders")
async def orders(
    request: Request,
    session: Db,
    q: str = Query(default="", max_length=100),
    status: str = Query(default="", max_length=20),
    page: int = Query(default=1, ge=1, le=10000),
):
    query = select(Order)
    if status:
        try:
            query = query.where(Order.status == OrderStatus(status))
        except ValueError as error:
            raise HTTPException(422, "Estado inválido") from error
    if q.strip():
        term = q.strip().lower()
        query = query.where(
            or_(
                func.lower(Order.customer_name).contains(term, autoescape=True),
                Order.phone.contains(term, autoescape=True),
                cast(Order.id, String).contains(term, autoescape=True),
                Order.number == int(term[2:])
                if term.startswith("mm") and term[2:].isdigit()
                else False,
            )
        )
    rows = list(
        (
            await session.scalars(
                query.order_by(Order.created_at.desc())
                .offset((page - 1) * 20)
                .limit(21)
            )
        ).all()
    )
    return view(
        request,
        "orders",
        {
            "orders": rows[:20],
            "statuses": OrderStatus,
            "status_filter": status,
            "q": q,
            "page": page,
            "has_next": len(rows) > 20,
        },
    )


async def order_view(request, session, order_id, error=None, status=200):
    order = await session.scalar(
        select(Order).where(Order.id == order_id).options(selectinload(Order.items))
    )
    if order is None:
        raise HTTPException(404, "Pedido no encontrado")
    next_statuses = {
        OrderStatus.PENDING: [OrderStatus.CONFIRMED, OrderStatus.CANCELLED],
        OrderStatus.CONFIRMED: [OrderStatus.SHIPPED, OrderStatus.CANCELLED],
        OrderStatus.SHIPPED: [OrderStatus.DELIVERED],
        OrderStatus.DELIVERED: [],
        OrderStatus.CANCELLED: [],
    }
    event = await session.scalar(
        select(NotificationOutbox).where(NotificationOutbox.order_id == order_id)
    )
    return view(
        request,
        "order_detail",
        {
            "order": order,
            "next_statuses": next_statuses[order.status],
            "event": event,
            "error": error,
            "notifications_enabled": get_config().evolution_enabled,
        },
        status,
    )


@protected.get("/orders/{order_id}")
async def order_detail(request: Request, order_id: UUID, session: Db):
    return await order_view(request, session, order_id)


@protected.post("/orders/{order_id}/status")
async def order_status(request: Request, order_id: UUID, session: Db):
    async with request.form(max_files=0, max_fields=3) as form:
        verify_csrf(request, str(form.get("csrf", "")))
        try:
            target = StatusInput.model_validate({"status": form.get("status")}).status
            expected = OrderStatus(str(form.get("expected_status", "")))
        except (ValidationError, ValueError) as error:
            raise HTTPException(422, "Estado inválido") from error
    try:
        async with session.begin():
            current = await session.scalar(
                select(Order).where(Order.id == order_id).with_for_update()
            )
            if current is None:
                raise HTTPException(404, "Pedido no encontrado")
            if current.status != expected:
                raise HTTPException(
                    409, "El pedido cambió. Revisa el estado actual antes de continuar."
                )
            await change_status(session, order_id, target)
    except HTTPException as error:
        if error.status_code == 404:
            raise
        return await order_view(
            request, session, order_id, error.detail, error.status_code
        )
    return redirect(
        request, f"/admin/orders/{order_id}", "Estado actualizado correctamente"
    )


@protected.get("/social")
async def social_page(request: Request, session: Db):
    setting = await session.get(Setting, "social_links")
    return view(request, "social", {"values": setting.value if setting else {}})


@protected.post("/social")
async def social_save(request: Request, session: Db):
    async with request.form(max_files=0, max_fields=4) as form:
        verify_csrf(request, str(form.get("csrf", "")))
        values = {
            key: str(form.get(key, "")).strip()
            for key in ("whatsapp", "facebook", "instagram")
        }
        try:
            data = SocialSettings(**values)
        except (ValidationError, ValueError) as error:
            message = (
                validation_message(error)
                if isinstance(error, ValidationError)
                else str(error)
            )
            return view(
                request, "social", {"values": values, "error": message}, status=422
            )
    async with session.begin():
        setting = await session.get(Setting, "social_links")
        if setting:
            setting.value = data.model_dump()
        else:
            session.add(Setting(key="social_links", value=data.model_dump()))
    return redirect(request, "/admin/social", "Redes sociales actualizadas")


@protected.get("/visits")
async def visits_page(request: Request, session: Db):
    from datetime import datetime, timedelta

    today = datetime.now(ZoneInfo("America/Guatemala")).date()
    rows = (
        await session.scalars(
            select(DailyTraffic)
            .where(DailyTraffic.day >= today - timedelta(days=29))
            .order_by(DailyTraffic.day.desc())
        )
    ).all()
    return view(
        request,
        "visits",
        {
            "rows": rows,
            "total": sum(row.views for row in rows),
            "today": next((row.views for row in rows if row.day == today), 0),
        },
    )


router.include_router(protected)

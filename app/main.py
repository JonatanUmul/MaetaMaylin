import secrets
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api.checkout import router
from app.config import get_config
from app.web.admin import router as admin_router
from app.web.client import router as client_router

app = FastAPI(title="Comercio multiproducto", version="0.1.0")
app.mount(
    "/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static"
)
config = get_config()
app.add_middleware(
    SessionMiddleware,
    secret_key=config.admin_session_secret.get_secret_value()
    if config.admin_session_secret
    else secrets.token_urlsafe(32),
    session_cookie="mimo_admin",
    max_age=8 * 3600,
    same_site="strict",
    https_only=config.admin_cookie_secure,
)
app.include_router(admin_router)
app.include_router(router)
app.include_router(client_router)


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


@app.middleware("http")
async def count_public_views(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if (
        request.method == "GET"
        and response.status_code == 200
        and (path in {"/", "/carrito", "/checkout"} or path.startswith("/productos/"))
    ):
        from app.services.traffic import record_pageview

        await record_pageview()
    return response

# Publicar Maeta Mailyn en Render

La tienda, el carrito de cotizaciones y el panel `/admin` son un solo servicio Python.
La base local no es accesible desde Render: es obligatorio configurar una conexión
PostgreSQL alojada antes de iniciar el servicio. Las migraciones crean las tablas;
no transfieren automáticamente los productos ni las cotizaciones locales.

## Repositorio

Subir el contenido de `ecommerce` a un repositorio privado. No subir `.env`,
`.runtime`, `.venv`, copias de base de datos ni credenciales. El archivo
`.env.example` contiene únicamente ejemplos. No copiarlo sobre el `.env` existente.

## Servicio web

En Render, New → Web Service → conectar el repositorio:

- Runtime: Python.
- Root Directory: vacío si el repositorio contiene directamente este proyecto;
  `ecommerce` si se sube como subdirectorio.
- Build Command: `pip install .`
- Start Command: `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Health Check Path: `/health`

Variables de entorno:

- `PYTHON_VERSION=3.12.8`
- `DATABASE_URL`: conexión PostgreSQL alojada; se convierte automáticamente al
  controlador asyncpg si empieza por `postgresql://` o `postgres://`.
- `ADMIN_USERNAME=admin`
- `ADMIN_PASSWORD_HASH`: hash existente del `.env` local, pegar directamente en
  el panel privado de Render; no usar la contraseña sin hash.
- `ADMIN_SESSION_SECRET`: generar un secreto nuevo (por ejemplo con
  `python -c 'import secrets; print(secrets.token_urlsafe(48))'`).
- `ADMIN_COOKIE_SECURE=true`
- `EVOLUTION_ENABLED=false`

También se incluye `render.yaml` para un Blueprint con el proyecto en la raíz.
No crea una base de datos ni un worker. Mantener una sola instancia con este
comando de inicio; para múltiples instancias ejecutar las migraciones como un
paso independiente antes de desplegar.

## Fotos y datos

Las imágenes versionadas en `app/static/images` se publican con el código.
Las fotos actuales de `app/static/uploads` están excluidas de Git: se deben
transferir junto con los datos antes de anunciar la tienda publicada.

El servicio gratuito no conserva nuevas subidas entre reinicios o despliegues.
Para administrar fotos en producción se necesita almacenamiento externo, como
Supabase Storage, o un servicio de pago con disco persistente montado en
`/opt/render/project/src/app/static/uploads` (ajustar si existe Root Directory).
No usar el servicio gratuito como almacenamiento definitivo de fotos.

Después de conectar la base, transferir el catálogo y verificar `/`, `/admin`,
las imágenes y una cotización de prueba. El worker de Evolution se configura
por separado cuando se autorice activar las notificaciones.

Documentación: https://render.com/docs/deploy-fastapi y https://render.com/docs/free

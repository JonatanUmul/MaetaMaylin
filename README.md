# Backend de comercio multiproducto

Base implementada con FastAPI, SQLAlchemy 2, Pydantic 2, Alembic,
PostgreSQL/asyncpg y HTTPX. No procesa pagos. Incluye catálogo SSR,
carrito LocalStorage, checkout, notificaciones persistentes y un panel
administrativo protegido para productos, fotos, inventario y pedidos.

## Estructura implementada

```text
ecommerce/
├── app/
│   ├── main.py                   # FastAPI y registro de rutas
│   ├── config.py                 # Configuración desde entorno
│   ├── db.py                     # Motor y sesiones asíncronas
│   ├── models.py                 # Modelos y restricciones relacionales
│   ├── schemas.py                # Validación de entradas y respuestas
│   ├── api/
│   │   └── checkout.py           # POST /api/checkout
│   ├── services/
│   │   ├── orders.py             # Checkout y transiciones de inventario
│   │   └── evolution.py          # POST asíncrono y worker persistente
│   ├── templates/
│   │   ├── client/               # Catálogo, carrito y checkout
│   │   └── admin/                # Panel administrativo protegido
│   └── static/
│       ├── css/                  # Tailwind compilado y estilos del panel
│       └── js/                   # Carrito LocalStorage y previsualización de fotos
├── alembic/
│   ├── env.py
│   └── versions/0001_initial.py
├── tests/test_orders.py
├── .env.example
├── alembic.ini
├── requirements.lock.txt         # Versiones usadas en la verificación
└── pyproject.toml
```

El catálogo está en `web/client.py` y la administración en `web/admin.py`.
Las plantillas Jinja2 usan autoescape. Tailwind se sirve compilado localmente.
El carrito almacena IDs y cantidades; el servidor valida precios y existencias.

## Ejecutar

Python 3.12 o posterior. Desde esta carpeta:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
# Configurar .env con credenciales reales antes de continuar.
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

En un segundo proceso, usando el mismo entorno y la misma base de datos:

```bash
python -m app.services.evolution
```

El worker debe permanecer activo y supervisado para recuperar eventos
pendientes tras errores o reinicios. Para una instalación reproducible,
las versiones comprobadas están en `requirements.lock.txt`; el proyecto
requiere actualizar y verificar esas versiones periódicamente.

PostgreSQL es el destino de producción. En Supabase utilizar una conexión
compatible con asyncpg y configurar TLS según el proveedor; las credenciales
solo pertenecen al backend. La tabla `settings` almacena configuración no
secreta en JSON (por ejemplo, datos públicos del negocio). Las credenciales
de Evolution y la moneda del checkout provienen del entorno. La moneda
predeterminada es GTQ; se puede cambiar antes de comenzar a recibir pedidos.

## Contrato del checkout

El navegador debe generar un UUID con `crypto.randomUUID()` por intento
lógico de compra y conservarlo si la solicitud falla o se pierde la respuesta.
Si el cliente cambia el pedido, utilizar una clave nueva. Vaciar el carrito
solo tras recibir una respuesta exitosa. No guardar datos personales en
LocalStorage. No enviar precios, totales ni estado desde el cliente.

```http
POST /api/checkout
Content-Type: application/json
Idempotency-Key: 80a22ea3-f180-490f-83e0-1c8ec9b024e9

{
  "customer_name": "Ana Pérez",
  "phone": "+50255555555",
  "address": "Calle principal 123, Guatemala",
  "notes": "Coordinar entrega por WhatsApp",
  "items": [{"product_id": 1, "quantity": 2}]
}
```

Respuesta HTTP 201, con precio ilustrativo de GTQ 25.50 por producto:

```json
{
  "id": "287533b7-ecbd-4f01-a8b1-204fb55d6f39",
  "status": "Pendiente",
  "total": "51.00",
  "currency": "GTQ"
}
```

La misma clave y cuerpo devuelve el recibo del mismo pedido (también HTTP
201), sin crear otro evento. La misma clave con datos diferentes devuelve
409. Productos inactivos, inexistentes o stock insuficiente devuelven 409;
entradas mal formadas devuelven 422. Una falla de base de datos revierte la
transacción y no debe presentarse como pedido recibido.

El pedido y su evento se guardan en una misma transacción. El servidor toma
los precios actuales del catálogo con Decimal/Numeric y guarda copias del
título y precio en `order_items`. El total actual incluye solo artículos:
no se calcula envío ni impuestos adicionales en esta base. Si se necesitan,
agregar sus importes explícitos al modelo y al cálculo del servidor.

## Inventario y administración

`change_status()` en `services/orders.py` se llama únicamente desde una ruta
administrativa autenticada, dentro de `async with session.begin()`:

```python
async with session.begin():
    order = await change_status(session, order_id, input.status)
```

Transiciones permitidas:

- Pendiente → Confirmado o Cancelado.
- Confirmado → Enviado o Cancelado.
- Enviado y Cancelado son terminales en esta fase.
- Repetir el mismo estado no altera el stock.

Un pedido pendiente no reserva ni descuenta unidades. Al confirmar, se
bloquea la orden y se bloquean sus productos en un orden consistente con
`SELECT FOR UPDATE`; se comprueba nuevamente la disponibilidad y se
descuenta todo dentro de una transacción. Si falla cualquier artículo, se
revierte todo. Cancelar una orden confirmada devuelve el stock una sola vez;
cancelar una pendiente no lo modifica. Las órdenes enviadas no se cancelan
por esta función; las devoluciones requieren un flujo separado.

Los productos con historial usan eliminación lógica (`active = false`).
La FK con `RESTRICT` impide borrar físicamente productos referenciados;
los que no tienen historial pueden borrarse desde el CRUD. La categoría
no puede eliminarse mientras tenga productos. El panel debe utilizar
`ProductInput` y `StatusInput` como esquemas de entrada, validar existencia
de la categoría, paginar consultas y capturar conflictos de integridad.

## Evolution API

El JSON interno del evento contiene `order_id`, `status`, `customer`,
`items`, `total` y `currency`. El adaptador lo transforma al contrato de
mensajes de Evolution API v2:

```json
{
  "number": "50255555555",
  "text": "Pedido recibido #...\nCliente: ...\nTotal: GTQ 51.00\n..."
}
```

Se envía a `POST {EVOLUTION_URL}/message/sendText/{EVOLUTION_INSTANCE}`
con header `apikey`. El contrato de envío no acepta directamente el objeto
arbitrario de negocio como un webhook de entrada: si se necesita ese formato
para otro servicio, crear otro adaptador con su contrato correspondiente.

Tras el commit se programa un intento inmediato con `BackgroundTasks`; el
HTTP usa `httpx.AsyncClient`. El worker recoge eventos vencidos mediante
`FOR UPDATE SKIP LOCKED`, evitando que dos procesos envíen simultáneamente
el mismo evento. Los timeouts son acotados y los errores temporales usan
reintentos exponenciales. Tras ocho intentos, o un error HTTP 4xx permanente,
el evento queda marcado con `failed_at` para revisión. Los logs no contienen
credenciales, cuerpo de respuesta ni datos personales.

La garantía es de entrega **al menos una vez con reintentos limitados**:
un timeout después de que el proveedor acepte el mensaje, o una caída antes
del commit del envío, puede duplicar la notificación. El ID visible del
pedido permite reconocer duplicados. No se promete entrega instantánea ni
exactamente una vez. Un HTTP 2xx significa aceptación por Evolution, no
confirmación de entrega de WhatsApp. Para verificar entrega añadir manejo
de los webhooks de estado del proveedor.

Monitorizar eventos con `failed_at` y retrasos de `available_at`. Tras corregir
el problema se puede reactivar el evento poniendo `failed_at = NULL`,
`attempts = 0` y `available_at = now()` en un procedimiento administrativo
protegido; no modificar `sent_at` de eventos ya enviados.

## Validación y límites de producción

```bash
pytest -q
ruff check app tests alembic
```

Verificación realizada: catorce pruebas aprobadas, Ruff sin errores, SQL de la
migración generado para PostgreSQL y ciclo upgrade/downgrade ejecutado en
SQLite.

Las pruebas usan SQLite para validar reglas, rollback, idempotencia de ruta
HTTP y Evolution con transporte simulado. No verifican el comportamiento
concurrente de bloqueos de PostgreSQL ni hacen envíos reales. Antes del
despliegue, verificar con PostgreSQL dos confirmaciones simultáneas sobre
el mismo producto y varios workers procesando la cola. Las migraciones se
incluyen; no se ejecuta `create_all()` al iniciar la aplicación.

La interfaz del cliente y el panel están implementados. El despliegue requiere
HTTPS, secretos de entorno, límites de solicitudes y de cuerpo en el proxy,
supervisión del worker y backups de PostgreSQL y `app/static/uploads`.
El login tiene límite por proceso; para varias réplicas usar un límite compartido.
El checkout público debe recibir límites de frecuencia para impedir spam.

Referencias primarias consultadas:

- [FastAPI BackgroundTasks](https://fastapi.tiangolo.com/tutorial/background-tasks/)
- [SQLAlchemy 2](https://docs.sqlalchemy.org/en/20/orm/queryguide/dml.html)
- [Evolution API: DTO de envío](https://github.com/EvolutionAPI/evolution-api/blob/main/src/api/dto/sendMessage.dto.ts)

## Frontend Mimo

Abrir http://127.0.0.1:8000/ para la tienda. Jinja2 renderiza catálogo y
fichas; JavaScript administra LocalStorage, consulta precios actuales y
envía el checkout real. La página de confirmación muestra el ID del pedido.
El único proceso web sigue siendo FastAPI: no hay un servidor frontend
separado.

CSS de Tailwind compilado localmente, sin CDN. Para regenerarlo tras
cambiar clases o estilos:

```bash
npx --yes tailwindcss@3.4.17 -i app/static/css/input.css -o app/static/css/store.css --minify
```

Se añadieron seis productos ilustrados de demostración al catálogo local,
solo si estaba vacío, con `python -m scripts.seed_demo`. La configuración
`settings.demo_mode` activa el aviso público de demostración. Antes de
operar con productos reales, reemplazar el catálogo y desactivar ese aviso.

El entorno local tiene `EVOLUTION_ENABLED=false`: el worker permanece
activo, pero deja los eventos pendientes sin intentar envíos. Configurar
credenciales reales, revisar los eventos de prueba y activar esta opción
antes de enviar notificaciones; reiniciar backend y worker después de
cambiar el entorno. El flujo se probó en el navegador con un pedido
identificado como prueba; no se descontó stock ni se envió WhatsApp.

## Dirección visual actual: ecommerce violeta

La interfaz se basa en la referencia aportada: superficies blancas,
acentos violetas, tipografía DM Sans, Hero compacto con una bolsa ilustrada,
tarjetas redondeadas y un carrito lateral persistente. El carrito consulta
el catálogo y se actualiza al añadir, quitar o cambiar cantidades.
En móviles el resumen aparece debajo del catálogo.

HTML: `app/templates/client/catalog.html` y `base.html`.
CSS fuente: `app/static/css/input.css`; compilado: `store.css`.
Ilustración del Hero: `app/static/images/shop-bag.svg`.
El checkout continúa sin pagos en línea. Las ilustraciones y los productos
locales siguen siendo de demostración.

## Administración

Abrir `/admin`. Todos los formularios privados requieren sesión y CSRF.
Preparar el acceso local con:

```bash
python -m scripts.setup_admin
```

El comando genera un usuario `admin`, un hash PBKDF2 y una clave de sesión.
Guarda la contraseña inicial en `.runtime/admin-access.txt` con permisos 600;
este archivo y `.env` están excluidos de Git. Reiniciar FastAPI al cambiar
credenciales. En producción configurar `ADMIN_COOKIE_SECURE=true`, HTTPS,
un `ADMIN_SESSION_SECRET` aleatorio de al menos 32 caracteres y
`ADMIN_PASSWORD_HASH` generado por `app.security.admin.hash_password`.

- `/admin/products`: crear, editar o archivar productos y ajustar precio/stock.
- Fotos: subir JPEG, PNG o WEBP de hasta 5 MiB, o usar una URL HTTPS.
  Las subidas se validan, normalizan a JPEG y guardan en `app/static/uploads`.
- `/admin/categories`: crear categorías para el catálogo.
- `/admin/orders`: buscar pedidos, filtrar por estado y revisar sus detalles.
- Estados: Pendiente → Confirmado → Enviado → Entregado. Se puede cancelar
  desde Pendiente o Confirmado. Confirmar descuenta stock; cancelar un pedido
  confirmado lo repone una sola vez. Entregado y Cancelado son estados finales.

Las ediciones detectan formularios desactualizados para evitar sobrescribir
cambios de inventario realizados por otra sesión. Archivar oculta el producto
del catálogo y conserva el historial de pedidos; puede republicarse al editarlo.

## Solicitudes de cotización

La tienda pública no muestra precios ni totales y no permite filtrar por precio.
La API del catálogo devuelve identificación, foto, título y stock, sin precio.
El checkout conserva validación e idempotencia, pero su respuesta pública
solo incluye id, estado y mensaje. Se guardan los precios del catálogo como
referencias internas para administración; no representan una cotización
aceptada. Al confirmar se descuenta stock, tras acordar con el cliente precio,
pago y entrega. El contador público muestra unidades seleccionadas.

## Catálogo por encargo, sin inventario

Los productos publicados admiten solicitudes independientemente del stock
histórico. No se muestran existencias ni se filtra por disponibilidad. Confirmar,
cancelar, enviar y entregar no modifican stock. Los campos históricos se conservan
en la base para mantener datos existentes, pero no se usan ni editan en el panel.
La cantidad admite de 1 a 100 unidades por producto como límite de validación;
para mayores cantidades el cliente puede contactar por WhatsApp o Facebook.

## Folios cortos y notificaciones

Cada solicitud obtiene un folio MM000001, MM000002, etc., mediante un contador
persistente de base de datos; los UUID se mantienen como identificadores internos.
La migración 0004 asigna folios a solicitudes existentes por fecha y actualiza los
eventos aún no enviados. Los reintentos idempotentes conservan el mismo folio.
La numeración aumenta y es única; operaciones fallidas pueden dejar saltos.
Los mensajes WhatsApp usan bloques y listas sin importes, más un enlace al cliente.
Los mensajes ya enviados no se modifican ni se reenvían por la migración.

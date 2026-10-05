"""Generate local admin access once, without printing credentials."""

import secrets
from pathlib import Path

from app.security.admin import password_hash

path = Path(".env")
content = path.read_text()
if "ADMIN_PASSWORD_HASH=" not in content:
    password = secrets.token_urlsafe(18)
    content += (
        f"ADMIN_USERNAME=admin\nADMIN_PASSWORD_HASH={password_hash(password)}\n"
        f"ADMIN_SESSION_SECRET={secrets.token_urlsafe(48)}\n"
        "ADMIN_COOKIE_SECURE=false\n"
    )
    path.write_text(content)
    path.chmod(0o600)
    access = Path(".runtime/admin-access.txt")
    access.write_text(
        "Administración local de Mimo\n\n"
        "URL: http://127.0.0.1:8000/admin\n"
        f"Usuario: admin\nContraseña: {password}\n"
    )
    access.chmod(0o600)
    print("Acceso generado en .runtime/admin-access.txt")
else:
    print("El acceso ya existe; se conservan las credenciales.")

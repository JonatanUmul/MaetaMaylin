import ssl
from functools import lru_cache
from pathlib import Path

from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    admin_username: str = "admin"
    admin_password_hash: SecretStr | None = None
    admin_session_secret: SecretStr | None = Field(default=None, min_length=32)
    admin_cookie_secure: bool = True

    database_url: str
    currency: str = Field(default="GTQ", pattern=r"^[A-Z]{3}$")
    evolution_enabled: bool = False
    evolution_url: HttpUrl = "https://example.com"
    evolution_instance: str = Field(default="shop", pattern=r"^[a-zA-Z0-9_-]+$")
    evolution_api_key: SecretStr = SecretStr("")
    admin_whatsapp: str = Field(default="50253234824", pattern=r"^[1-9][0-9]{7,14}$")

    @field_validator("database_url")
    @classmethod
    def use_async_driver(cls, value: str) -> str:
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+asyncpg://" + value[len(prefix) :]
        return value

    def database_connect_args(self) -> dict:
        host = make_url(self.database_url).host or ""
        if host.endswith((".supabase.co", ".supabase.com")):
            context = ssl.create_default_context()
            # Supabase's 2021 root predates the key-usage requirement enforced
            # by Python 3.13+. Keep certificate and hostname verification.
            context.verify_flags &= ~ssl.VERIFY_X509_STRICT
            context.load_verify_locations(
                cafile=str(Path(__file__).parent / "certs" / "supabase-ca.crt")
            )
            return {"ssl": context}
        return {}


@lru_cache
def get_config() -> Config:
    return Config()

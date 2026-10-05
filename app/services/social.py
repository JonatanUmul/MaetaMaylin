from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SocialSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    whatsapp: str = Field(default="", max_length=15)
    facebook: str = Field(default="", max_length=300)
    instagram: str = Field(default="", max_length=300)

    @field_validator("whatsapp")
    @classmethod
    def phone(cls, value):
        if value and (
            not value.isascii() or not value.isdigit() or not 8 <= len(value) <= 15
        ):
            raise ValueError(
                "Usa de 8 a 15 dígitos con código de país, sin + ni espacios"
            )
        return value

    @field_validator("facebook", "instagram")
    @classmethod
    def profile(cls, value, info):
        if not value:
            return value
        parsed = urlsplit(value)
        domains = {
            "facebook": {"facebook.com", "www.facebook.com", "m.facebook.com"},
            "instagram": {"instagram.com", "www.instagram.com"},
        }
        if (
            parsed.scheme != "https"
            or parsed.hostname not in domains[info.field_name]
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
        ):
            raise ValueError("Usa un enlace HTTPS del sitio oficial de esta red")
        return value

    def links(self):
        return {
            "WhatsApp": f"https://wa.me/{self.whatsapp}" if self.whatsapp else "",
            "Facebook": self.facebook,
            "Instagram": self.instagram,
        }

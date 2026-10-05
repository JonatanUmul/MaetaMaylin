from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from app.models import OrderStatus


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CheckoutItem(InputModel):
    product_id: int = Field(strict=True, gt=0)
    quantity: int = Field(strict=True, ge=1, le=100)


class CheckoutInput(InputModel):
    customer_name: str = Field(min_length=2, max_length=150)
    phone: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$")
    address: str = Field(min_length=10, max_length=1000)
    notes: str = Field(default="", max_length=2000)
    items: list[CheckoutItem] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_products(self) -> "CheckoutInput":
        ids = [item.product_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("Cada producto debe aparecer una sola vez")
        self.items.sort(key=lambda item: item.product_id)
        return self


class OrderReceipt(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: OrderStatus
    reference: str
    message: str = "Solicitud recibida; te contactaremos para cotizar."


class ProductInput(InputModel):
    title: str = Field(min_length=2, max_length=200)
    description: str = Field(min_length=1, max_length=10000)
    price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    image_url: HttpUrl = Field(max_length=2048)
    stock: int = Field(strict=True, ge=0, le=2147483647)
    category_id: int = Field(strict=True, gt=0)
    active: bool = Field(default=True, strict=True)


class StatusInput(InputModel):
    status: OrderStatus


class AdminProductInput(ProductInput):
    stock: int = Field(default=0, strict=True, ge=0, le=2147483647)
    image_url: str = Field(min_length=1, max_length=2048)

    @model_validator(mode="after")
    def safe_image(self):
        import re

        from pydantic import TypeAdapter

        if not re.fullmatch(
            r"/static/(images|uploads)/[a-zA-Z0-9_-]+\.(svg|jpg|png|webp)",
            self.image_url,
        ):
            self.image_url = str(TypeAdapter(HttpUrl).validate_python(self.image_url))
        return self


class CategoryInput(InputModel):
    name: str = Field(min_length=2, max_length=100)
    slug: str = Field(
        min_length=2, max_length=120, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    )

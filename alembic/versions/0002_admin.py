"""Product versions and delivered status (PostgreSQL)."""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "products",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.drop_constraint("order_status", "orders", type_="check")
    op.create_check_constraint(
        "order_status",
        "orders",
        "status IN ('Pendiente', 'Confirmado', 'Enviado', 'Entregado', 'Cancelado')",
    )


def downgrade():
    count = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM orders WHERE status = 'Entregado'"))
        .scalar()
    )
    if count:
        raise RuntimeError("No se puede revertir mientras existan pedidos entregados")
    op.drop_constraint("order_status", "orders", type_="check")
    op.create_check_constraint(
        "order_status",
        "orders",
        "status IN ('Pendiente', 'Confirmado', 'Enviado', 'Cancelado')",
    )
    op.drop_column("products", "version")

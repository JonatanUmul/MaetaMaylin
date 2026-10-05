"""Keep server-managed application tables private on Supabase."""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TABLES = (
    "categories",
    "products",
    "orders",
    "order_numbers",
    "order_items",
    "settings",
    "notification_outbox",
    "daily_traffic",
    "alembic_version",
)


def upgrade():
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    roles = (
        connection.execute(
            sa.text(
                "SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')"
            )
        )
        .scalars()
        .all()
    )
    if not roles:
        return
    for table in TABLES:
        op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')
        for role in roles:
            op.execute(f'REVOKE ALL ON TABLE public."{table}" FROM "{role}"')


def downgrade():
    # A rollback must not expose customer data or restore anonymous grants.
    pass

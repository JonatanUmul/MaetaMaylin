"""Short sequential order references, preserving UUID identifiers."""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "order_numbers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    )
    op.add_column("orders", sa.Column("number", sa.Integer(), nullable=True))
    op.execute("""
        WITH numbered AS (
            SELECT id, row_number() OVER (ORDER BY created_at, id) AS n FROM orders
        ) UPDATE orders SET number = numbered.n FROM numbered WHERE orders.id = numbered.id
    """)
    op.execute(
        "INSERT INTO order_numbers (id) SELECT number FROM orders ORDER BY number"
    )
    op.execute("""SELECT setval(pg_get_serial_sequence('order_numbers', 'id'),
        COALESCE((SELECT max(id) FROM order_numbers), 1),
        EXISTS(SELECT 1 FROM order_numbers))""")
    op.alter_column("orders", "number", nullable=False)
    op.create_unique_constraint("uq_orders_number", "orders", ["number"])
    op.execute("""UPDATE notification_outbox AS n SET payload =
        (n.payload::jsonb || jsonb_build_object('reference',
            'MM' || CASE WHEN length(o.number::text) < 6
            THEN lpad(o.number::text, 6, '0') ELSE o.number::text END))::json
        FROM orders AS o WHERE n.order_id = o.id AND n.sent_at IS NULL""")


def downgrade():
    op.drop_constraint("uq_orders_number", "orders", type_="unique")
    op.drop_column("orders", "number")
    op.drop_table("order_numbers")

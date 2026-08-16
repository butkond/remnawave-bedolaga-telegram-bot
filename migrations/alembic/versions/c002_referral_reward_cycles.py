"""Allow recurring referral traffic reward grants.

Revision ID: c002_referral_reward_cycles
Revises: 0095
Create Date: 2026-08-15
"""

from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = 'c002_referral_reward_cycles'
down_revision: Union[str, None] = '0095'
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


OLD_UNIQUE = 'uq_referral_traffic_reward_grants_referrer'
NEW_UNIQUE = 'uq_referral_traffic_reward_grants_referrer_cycle'
TABLE_NAME = 'referral_traffic_reward_grants'


def _has_table(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column['name'] for column in inspector.get_columns(table_name)}


def _has_unique(inspector: sa.Inspector, table_name: str, constraint_name: str) -> bool:
    return constraint_name in {constraint['name'] for constraint in inspector.get_unique_constraints(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _has_table(inspector, TABLE_NAME):
        return

    has_reward_cycle = _has_column(inspector, TABLE_NAME, 'reward_cycle')
    has_old_unique = _has_unique(inspector, TABLE_NAME, OLD_UNIQUE)
    has_new_unique = _has_unique(inspector, TABLE_NAME, NEW_UNIQUE)

    with op.batch_alter_table(TABLE_NAME) as batch_op:
        if not has_reward_cycle:
            batch_op.add_column(sa.Column('reward_cycle', sa.Integer(), nullable=False, server_default='1'))
        if has_old_unique:
            batch_op.drop_constraint(OLD_UNIQUE, type_='unique')
        if not has_new_unique:
            batch_op.create_unique_constraint(NEW_UNIQUE, ['referrer_id', 'reward_cycle'])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _has_table(inspector, TABLE_NAME):
        return

    has_reward_cycle = _has_column(inspector, TABLE_NAME, 'reward_cycle')
    has_old_unique = _has_unique(inspector, TABLE_NAME, OLD_UNIQUE)
    has_new_unique = _has_unique(inspector, TABLE_NAME, NEW_UNIQUE)

    if has_reward_cycle:
        op.execute(f'DELETE FROM {TABLE_NAME} WHERE reward_cycle > 1')

    with op.batch_alter_table(TABLE_NAME) as batch_op:
        if has_new_unique:
            batch_op.drop_constraint(NEW_UNIQUE, type_='unique')
        if not has_old_unique:
            batch_op.create_unique_constraint(OLD_UNIQUE, ['referrer_id'])
        if has_reward_cycle:
            batch_op.drop_column('reward_cycle')

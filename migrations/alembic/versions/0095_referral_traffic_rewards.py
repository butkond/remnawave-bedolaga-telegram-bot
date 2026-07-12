"""referral first-connected usage rewards

Revision ID: 0095
Revises: 0094
Create Date: 2026-07-12

Adds persistent attribution, qualification and grant tables for the referral
mode that rewards referrers when invited users first really connect to VPN.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0095'
down_revision: Union[str, None] = '0094'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_table(inspector, 'users'):
        user_columns = {col['name'] for col in inspector.get_columns('users')}
        if 'referral_reward_mode' not in user_columns:
            op.add_column('users', sa.Column('referral_reward_mode', sa.String(length=32), nullable=True))

    if not _has_table(inspector, 'referral_attributions'):
        op.create_table(
            'referral_attributions',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('referral_id', sa.Integer(), nullable=False),
            sa.Column('referrer_id', sa.Integer(), nullable=False),
            sa.Column('referral_code', sa.String(length=64), nullable=True),
            sa.Column('mode', sa.String(length=32), nullable=False),
            sa.Column('mode_captured_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(['referral_id'], ['users.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['referrer_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('referral_id', name='uq_referral_attributions_referral'),
        )
        op.create_index('idx_referral_attributions_referrer', 'referral_attributions', ['referrer_id'])
        op.create_index('idx_referral_attributions_mode', 'referral_attributions', ['mode'])
        op.create_index(op.f('ix_referral_attributions_id'), 'referral_attributions', ['id'])

    if not _has_table(inspector, 'referral_traffic_qualifications'):
        op.create_table(
            'referral_traffic_qualifications',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('referrer_id', sa.Integer(), nullable=False),
            sa.Column('referral_id', sa.Integer(), nullable=False),
            sa.Column('subscription_id', sa.Integer(), nullable=True),
            sa.Column('remnawave_uuid', sa.String(length=255), nullable=False),
            sa.Column('source_event', sa.String(length=64), nullable=False),
            sa.Column('qualified_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(['referral_id'], ['users.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['referrer_id'], ['users.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['subscription_id'], ['subscriptions.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('referral_id', name='uq_referral_traffic_qualifications_referral'),
        )
        op.create_index(
            'idx_referral_traffic_qualifications_referrer',
            'referral_traffic_qualifications',
            ['referrer_id'],
        )
        op.create_index(
            'idx_referral_traffic_qualifications_uuid',
            'referral_traffic_qualifications',
            ['remnawave_uuid'],
        )
        op.create_index(op.f('ix_referral_traffic_qualifications_id'), 'referral_traffic_qualifications', ['id'])

    if not _has_table(inspector, 'referral_traffic_reward_grants'):
        op.create_table(
            'referral_traffic_reward_grants',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('referrer_id', sa.Integer(), nullable=False),
            sa.Column('qualified_count_at_grant', sa.Integer(), nullable=False),
            sa.Column('reward_days', sa.Integer(), nullable=False),
            sa.Column('subscription_id', sa.Integer(), nullable=True),
            sa.Column('granted_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(['referrer_id'], ['users.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['subscription_id'], ['subscriptions.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('referrer_id', name='uq_referral_traffic_reward_grants_referrer'),
        )
        op.create_index(
            'idx_referral_traffic_reward_grants_subscription',
            'referral_traffic_reward_grants',
            ['subscription_id'],
        )
        op.create_index(op.f('ix_referral_traffic_reward_grants_id'), 'referral_traffic_reward_grants', ['id'])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table_name in (
        'referral_traffic_reward_grants',
        'referral_traffic_qualifications',
        'referral_attributions',
    ):
        if _has_table(inspector, table_name):
            op.drop_table(table_name)

    if _has_table(inspector, 'users'):
        user_columns = {col['name'] for col in inspector.get_columns('users')}
        if 'referral_reward_mode' in user_columns:
            op.drop_column('users', 'referral_reward_mode')

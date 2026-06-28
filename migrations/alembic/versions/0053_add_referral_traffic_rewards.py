"""add referral traffic rewards

Revision ID: 0053
Revises: 0052
Create Date: 2026-06-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0053'
down_revision: Union[str, None] = '0052'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('referral_reward_mode', sa.String(length=50), nullable=True))
    op.add_column(
        'users',
        sa.Column('referral_reward_mode_updated_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_users_referral_reward_mode', 'users', ['referral_reward_mode'])

    op.create_table(
        'referral_attributions',
        sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('referral_id', sa.Integer(), nullable=False),
        sa.Column('referrer_id', sa.Integer(), nullable=False),
        sa.Column('referral_code', sa.String(length=20), nullable=True),
        sa.Column('mode', sa.String(length=50), nullable=False),
        sa.Column('mode_captured_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['referral_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['referrer_id'], ['users.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('referral_id', name='uq_referral_attributions_referral'),
    )
    op.create_index(
        'ix_referral_attributions_referrer_id',
        'referral_attributions',
        ['referrer_id'],
    )

    op.create_table(
        'referral_traffic_qualifications',
        sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('referrer_id', sa.Integer(), nullable=False),
        sa.Column('referral_id', sa.Integer(), nullable=False),
        sa.Column('subscription_id', sa.Integer(), nullable=True),
        sa.Column('remnawave_uuid', sa.String(length=255), nullable=True),
        sa.Column('traffic_used_gb', sa.Float(), nullable=False),
        sa.Column('qualified_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['referrer_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['referral_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['subscription_id'], ['subscriptions.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('referral_id', name='uq_referral_traffic_qualifications_referral'),
    )
    op.create_index(
        'ix_referral_traffic_qualifications_referrer_id',
        'referral_traffic_qualifications',
        ['referrer_id'],
    )
    op.create_index(
        'ix_referral_traffic_qualifications_program_referral',
        'referral_traffic_qualifications',
        ['referral_id', 'referrer_id'],
    )

    op.create_table(
        'referral_traffic_reward_grants',
        sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('referrer_id', sa.Integer(), nullable=False),
        sa.Column('qualified_count_at_grant', sa.Integer(), nullable=False),
        sa.Column('reward_days', sa.Integer(), nullable=False),
        sa.Column('subscription_id', sa.Integer(), nullable=True),
        sa.Column('granted_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['referrer_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['subscription_id'], ['subscriptions.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('referrer_id', name='uq_referral_traffic_reward_grants_referrer'),
    )
    op.create_index(
        'ix_referral_traffic_reward_grants_referrer_id',
        'referral_traffic_reward_grants',
        ['referrer_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_referral_traffic_reward_grants_referrer_id', table_name='referral_traffic_reward_grants')
    op.drop_table('referral_traffic_reward_grants')

    op.drop_index('ix_referral_traffic_qualifications_program_referral', table_name='referral_traffic_qualifications')
    op.drop_index('ix_referral_traffic_qualifications_referrer_id', table_name='referral_traffic_qualifications')
    op.drop_table('referral_traffic_qualifications')

    op.drop_index('ix_referral_attributions_referrer_id', table_name='referral_attributions')
    op.drop_table('referral_attributions')

    op.drop_index('ix_users_referral_reward_mode', table_name='users')
    op.drop_column('users', 'referral_reward_mode_updated_at')
    op.drop_column('users', 'referral_reward_mode')

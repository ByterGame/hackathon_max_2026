"""Первоначальные предметные таблицы сервиса обращений.

Revision ID: 20260928_001
Revises:
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260928_001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    for schema in ("identity", "housing", "access", "issues", "system"):
        op.execute(f"CREATE SCHEMA {schema}")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table('users',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('max_user_id', sa.Text(), nullable=True),
    sa.Column('phone_number', sa.Text(), nullable=True),
    sa.Column('phone_verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('full_name', sa.Text(), nullable=True),
    sa.Column('kind', sa.Text(), server_default=sa.text("'unassigned'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.BigInteger(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint("kind IN ('unassigned', 'resident', 'employee', 'support')", name=op.f('ck_users_user_kind')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('max_user_id', name=op.f('uq_users_max_user_id')),
    sa.UniqueConstraint('phone_number', name=op.f('uq_users_phone_number')),
    schema='identity'
    )
    op.create_table('categories',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('code', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('sort_order', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_categories')),
    sa.UniqueConstraint('code', name=op.f('uq_categories_code')),
    schema='issues'
    )
    op.create_index('ix_categories_active_order', 'categories', ['is_active', 'sort_order'], unique=False, schema='issues')
    op.create_table('outbox_events',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('event_kind', sa.Text(), nullable=False),
    sa.Column('subject_kind', sa.Text(), nullable=False),
    sa.Column('subject_id', sa.UUID(), nullable=False),
    sa.Column('payload', postgresql.JSONB(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_outbox_events')),
    schema='system'
    )
    op.create_index('ix_outbox_events_pending', 'outbox_events', ['processed_at', 'next_attempt_at'], unique=False, schema='system')
    op.create_table('company_registration_requests',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('applicant_user_id', sa.UUID(), nullable=False),
    sa.Column('phone_number', sa.Text(), nullable=False),
    sa.Column('proposed_company_name', sa.Text(), nullable=True),
    sa.Column('free_text', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), server_default=sa.text("'open'"), nullable=False),
    sa.Column('outcome', sa.Text(), nullable=True),
    sa.Column('decision_note', sa.Text(), nullable=True),
    sa.Column('decided_by', sa.UUID(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancel_requested_by', sa.UUID(), nullable=True),
    sa.Column('cancel_requested_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('discussion', postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.BigInteger(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint("outcome IS NULL OR outcome IN ('approved', 'rejected')", name=op.f('ck_company_registration_requests_outcome_allowed')),
    sa.CheckConstraint("status IN ('open', 'reviewing', 'needs_info', 'closed', 'cancelled')", name=op.f('ck_company_registration_requests_status_allowed')),
    sa.ForeignKeyConstraint(['applicant_user_id'], ['identity.users.id'], name=op.f('fk_company_registration_requests_applicant_user_id_users')),
    sa.ForeignKeyConstraint(['cancel_requested_by'], ['identity.users.id'], name=op.f('fk_company_registration_requests_cancel_requested_by_users')),
    sa.ForeignKeyConstraint(['decided_by'], ['identity.users.id'], name=op.f('fk_company_registration_requests_decided_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_company_registration_requests')),
    schema='access'
    )
    op.create_index('ix_company_registration_requests_status_created', 'company_registration_requests', ['status', 'created_at'], unique=False, schema='access')
    op.create_table('audit_events',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('entity_kind', sa.Text(), nullable=False),
    sa.Column('entity_id', sa.UUID(), nullable=False),
    sa.Column('action', sa.Text(), nullable=False),
    sa.Column('actor_user_id', sa.UUID(), nullable=True),
    sa.Column('before_data', postgresql.JSONB(), nullable=True),
    sa.Column('after_data', postgresql.JSONB(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['identity.users.id'], name=op.f('fk_audit_events_actor_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_events')),
    schema='system'
    )
    op.create_index('ix_audit_events_entity_created', 'audit_events', ['entity_kind', 'entity_id', 'created_at'], unique=False, schema='system')
    op.create_table('command_receipts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('external_key', sa.Text(), nullable=False),
    sa.Column('actor_user_id', sa.UUID(), nullable=False),
    sa.Column('operation', sa.Text(), nullable=False),
    sa.Column('request_hash', sa.Text(), nullable=False),
    sa.Column('result_kind', sa.Text(), nullable=True),
    sa.Column('result_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("source IN ('max_bot', 'miniapp')", name=op.f('ck_command_receipts_source_allowed')),
    sa.ForeignKeyConstraint(['actor_user_id'], ['identity.users.id'], name=op.f('fk_command_receipts_actor_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_command_receipts')),
    sa.UniqueConstraint('source', 'actor_user_id', 'external_key', name='uq_command_receipts_source_actor_key'),
    schema='system'
    )
    op.create_table('drafts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_user_id', sa.UUID(), nullable=False),
    sa.Column('flow_kind', sa.Text(), nullable=False),
    sa.Column('payload', postgresql.JSONB(), nullable=False),
    sa.Column('revision', sa.BigInteger(), server_default=sa.text('1'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['owner_user_id'], ['identity.users.id'], name=op.f('fk_drafts_owner_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_drafts')),
    schema='system'
    )
    op.create_index('ix_drafts_owner_flow', 'drafts', ['owner_user_id', 'flow_kind', 'updated_at'], unique=False, schema='system')
    op.create_table('notifications',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('outbox_event_id', sa.UUID(), nullable=False),
    sa.Column('recipient_user_id', sa.UUID(), nullable=False),
    sa.Column('subject_kind', sa.Text(), nullable=False),
    sa.Column('subject_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('bot_state', sa.Text(), server_default=sa.text("'pending'"), nullable=False),
    sa.Column('bot_attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('next_bot_attempt_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('bot_sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.CheckConstraint("bot_state IN ('pending', 'sent', 'muted', 'blocked', 'failed')", name=op.f('ck_notifications_bot_state_allowed')),
    sa.ForeignKeyConstraint(['outbox_event_id'], ['system.outbox_events.id'], name=op.f('fk_notifications_outbox_event_id_outbox_events')),
    sa.ForeignKeyConstraint(['recipient_user_id'], ['identity.users.id'], name=op.f('fk_notifications_recipient_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notifications')),
    sa.UniqueConstraint('outbox_event_id', 'recipient_user_id', name='uq_notifications_event_recipient'),
    schema='system'
    )
    op.create_index('ix_notifications_recipient_unread', 'notifications', ['recipient_user_id', 'read_at'], unique=False, schema='system')
    op.create_table('companies',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('registration_request_id', sa.UUID(), nullable=True),
    sa.Column('display_name', sa.Text(), nullable=False),
    sa.Column('legal_name', sa.Text(), nullable=True),
    sa.Column('inn', sa.Text(), nullable=True),
    sa.Column('ogrn', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['registration_request_id'], ['access.company_registration_requests.id'], name=op.f('fk_companies_registration_request_id_company_registration_requests')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_companies')),
    sa.UniqueConstraint('inn', name=op.f('uq_companies_inn')),
    sa.UniqueConstraint('ogrn', name=op.f('uq_companies_ogrn')),
    sa.UniqueConstraint('registration_request_id', name=op.f('uq_companies_registration_request_id')),
    schema='housing'
    )
    op.create_table('houses',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('company_id', sa.UUID(), nullable=False),
    sa.Column('address_display', sa.Text(), nullable=False),
    sa.Column('address_key', sa.Text(), nullable=False),
    sa.Column('entrance_count', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('entrance_count IS NULL OR entrance_count > 0', name=op.f('ck_houses_entrance_count_positive')),
    sa.ForeignKeyConstraint(['company_id'], ['housing.companies.id'], name=op.f('fk_houses_company_id_companies')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_houses')),
    sa.UniqueConstraint('address_key', name=op.f('uq_houses_address_key')),
    schema='housing'
    )
    op.create_index('ix_houses_company_id', 'houses', ['company_id'], unique=False, schema='housing')
    op.create_table('staff_assignments',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('company_id', sa.UUID(), nullable=False),
    sa.Column('phone_number', sa.Text(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('can_manage_staff', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('can_manage_residents', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('can_manage_issues', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('granted_by', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('bound_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.BigInteger(), server_default=sa.text('1'), nullable=False),
    sa.ForeignKeyConstraint(['company_id'], ['housing.companies.id'], name=op.f('fk_staff_assignments_company_id_companies')),
    sa.ForeignKeyConstraint(['granted_by'], ['identity.users.id'], name=op.f('fk_staff_assignments_granted_by_users')),
    sa.ForeignKeyConstraint(['user_id'], ['identity.users.id'], name=op.f('fk_staff_assignments_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_staff_assignments')),
    schema='identity'
    )
    op.create_index('uq_staff_active_company_user', 'staff_assignments', ['company_id', 'user_id'], unique=True, schema='identity', postgresql_where=sa.text('revoked_at IS NULL AND user_id IS NOT NULL'))
    op.create_index('uq_staff_active_company_phone', 'staff_assignments', ['company_id', 'phone_number'], unique=True, schema='identity', postgresql_where=sa.text('revoked_at IS NULL'))
    op.create_table('house_addition_requests',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('applicant_user_id', sa.UUID(), nullable=False),
    sa.Column('registration_request_id', sa.UUID(), nullable=True),
    sa.Column('company_id', sa.UUID(), nullable=True),
    sa.Column('entered_address', sa.Text(), nullable=False),
    sa.Column('resolved_house_id', sa.UUID(), nullable=True),
    sa.Column('free_text', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'open'"), nullable=False),
    sa.Column('outcome', sa.Text(), nullable=True),
    sa.Column('decision_note', sa.Text(), nullable=True),
    sa.Column('decided_by', sa.UUID(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancel_requested_by', sa.UUID(), nullable=True),
    sa.Column('cancel_requested_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('discussion', postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.BigInteger(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint("outcome IS NULL OR outcome IN ('approved', 'rejected')", name=op.f('ck_house_addition_requests_outcome_allowed')),
    sa.CheckConstraint("status IN ('open', 'reviewing', 'needs_info', 'closed', 'cancelled')", name=op.f('ck_house_addition_requests_status_allowed')),
    sa.CheckConstraint('registration_request_id IS NOT NULL OR company_id IS NOT NULL', name=op.f('ck_house_addition_requests_request_has_company_source')),
    sa.ForeignKeyConstraint(['applicant_user_id'], ['identity.users.id'], name=op.f('fk_house_addition_requests_applicant_user_id_users')),
    sa.ForeignKeyConstraint(['cancel_requested_by'], ['identity.users.id'], name=op.f('fk_house_addition_requests_cancel_requested_by_users')),
    sa.ForeignKeyConstraint(['company_id'], ['housing.companies.id'], name=op.f('fk_house_addition_requests_company_id_companies')),
    sa.ForeignKeyConstraint(['decided_by'], ['identity.users.id'], name=op.f('fk_house_addition_requests_decided_by_users')),
    sa.ForeignKeyConstraint(['registration_request_id'], ['access.company_registration_requests.id'], name=op.f('fk_house_addition_requests_registration_request_id_company_registration_requests')),
    sa.ForeignKeyConstraint(['resolved_house_id'], ['housing.houses.id'], name=op.f('fk_house_addition_requests_resolved_house_id_houses')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_house_addition_requests')),
    schema='access'
    )
    op.create_index('ix_house_addition_requests_registration', 'house_addition_requests', ['registration_request_id'], unique=False, schema='access')
    op.create_index('ix_house_addition_requests_company_status', 'house_addition_requests', ['company_id', 'status'], unique=False, schema='access')
    op.create_table('apartments',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('house_id', sa.UUID(), nullable=False),
    sa.Column('entrance_number', sa.Integer(), nullable=False),
    sa.Column('apartment_number', sa.Integer(), nullable=False),
    sa.CheckConstraint('apartment_number > 0', name=op.f('ck_apartments_apartment_number_positive')),
    sa.CheckConstraint('entrance_number > 0', name=op.f('ck_apartments_entrance_number_positive')),
    sa.ForeignKeyConstraint(['house_id'], ['housing.houses.id'], name=op.f('fk_apartments_house_id_houses')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_apartments')),
    sa.UniqueConstraint('house_id', 'entrance_number', 'apartment_number', name='uq_apartment_location'),
    schema='housing'
    )
    op.create_table('cards',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('house_id', sa.UUID(), nullable=False),
    sa.Column('author_user_id', sa.UUID(), nullable=False),
    sa.Column('category_id', sa.UUID(), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), server_default=sa.text("'open'"), nullable=False),
    sa.Column('close_result', sa.Text(), nullable=True),
    sa.Column('current_note', sa.Text(), nullable=True),
    sa.Column('scope_all_house', sa.Boolean(), nullable=False),
    sa.Column('merged_into_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.BigInteger(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint("close_result IS NULL OR close_result IN ('solved', 'invalid')", name=op.f('ck_cards_close_result_allowed')),
    sa.CheckConstraint("status <> 'closed' OR (close_result IS NOT NULL AND NULLIF(BTRIM(current_note), '') IS NOT NULL AND closed_at IS NOT NULL)", name=op.f('ck_cards_closed_has_result')),
    sa.CheckConstraint("status = 'closed' OR (close_result IS NULL AND closed_at IS NULL)", name=op.f('ck_cards_open_has_no_close_result')),
    sa.CheckConstraint("status IN ('open', 'reviewing', 'needs_info', 'in_progress', 'closed')", name=op.f('ck_cards_status_allowed')),
    sa.CheckConstraint('merged_into_id IS NULL OR merged_into_id <> id', name=op.f('ck_cards_not_merged_into_self')),
    sa.ForeignKeyConstraint(['author_user_id'], ['identity.users.id'], name=op.f('fk_cards_author_user_id_users')),
    sa.ForeignKeyConstraint(['category_id'], ['issues.categories.id'], name=op.f('fk_cards_category_id_categories')),
    sa.ForeignKeyConstraint(['house_id'], ['housing.houses.id'], name=op.f('fk_cards_house_id_houses')),
    sa.ForeignKeyConstraint(['merged_into_id'], ['issues.cards.id'], name=op.f('fk_cards_merged_into_id_cards')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_cards')),
    schema='issues'
    )
    op.create_index('ix_cards_house_status_created', 'cards', ['house_id', 'status', 'created_at'], unique=False, schema='issues')
    op.create_table('resident_offers',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('house_id', sa.UUID(), nullable=False),
    sa.Column('company_id_at_offer', sa.UUID(), nullable=False),
    sa.Column('apartment_id', sa.UUID(), nullable=False),
    sa.Column('phone_number', sa.Text(), nullable=False),
    sa.Column('offered_by', sa.UUID(), nullable=False),
    sa.Column('status', sa.Text(), server_default=sa.text("'pending'"), nullable=False),
    sa.Column('accepted_by', sa.UUID(), nullable=True),
    sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('proposed_access_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('pending', 'accepted', 'declined', 'cancelled')", name=op.f('ck_resident_offers_status_allowed')),
    sa.ForeignKeyConstraint(['accepted_by'], ['identity.users.id'], name=op.f('fk_resident_offers_accepted_by_users')),
    sa.ForeignKeyConstraint(['apartment_id'], ['housing.apartments.id'], name=op.f('fk_resident_offers_apartment_id_apartments')),
    sa.ForeignKeyConstraint(['company_id_at_offer'], ['housing.companies.id'], name=op.f('fk_resident_offers_company_id_at_offer_companies')),
    sa.ForeignKeyConstraint(['house_id'], ['housing.houses.id'], name=op.f('fk_resident_offers_house_id_houses')),
    sa.ForeignKeyConstraint(['offered_by'], ['identity.users.id'], name=op.f('fk_resident_offers_offered_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_resident_offers')),
    schema='access'
    )
    op.create_index('ix_resident_offers_phone_status', 'resident_offers', ['phone_number', 'status'], unique=False, schema='access')
    op.create_table('resident_requests',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('applicant_user_id', sa.UUID(), nullable=False),
    sa.Column('house_id', sa.UUID(), nullable=False),
    sa.Column('submitted_full_name', sa.Text(), nullable=False),
    sa.Column('submitted_entrance_number', sa.Integer(), nullable=False),
    sa.Column('submitted_apartment_number', sa.Integer(), nullable=False),
    sa.Column('resolved_apartment_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'open'"), nullable=False),
    sa.Column('outcome', sa.Text(), nullable=True),
    sa.Column('decision_note', sa.Text(), nullable=True),
    sa.Column('decided_by', sa.UUID(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancel_requested_by', sa.UUID(), nullable=True),
    sa.Column('cancel_requested_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('discussion', postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.BigInteger(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint("outcome IS NULL OR outcome IN ('granted', 'denied')", name=op.f('ck_resident_requests_outcome_allowed')),
    sa.CheckConstraint("status <> 'cancelled' OR outcome IS NULL", name=op.f('ck_resident_requests_cancelled_without_outcome')),
    sa.CheckConstraint("status <> 'closed' OR (outcome IS NOT NULL AND NULLIF(BTRIM(decision_note), '') IS NOT NULL AND decided_by IS NOT NULL AND decided_at IS NOT NULL)", name=op.f('ck_resident_requests_closed_has_decision')),
    sa.CheckConstraint("status IN ('open', 'reviewing', 'needs_info', 'closed', 'cancelled')", name=op.f('ck_resident_requests_status_allowed')),
    sa.CheckConstraint('submitted_apartment_number > 0', name=op.f('ck_resident_requests_apartment_number_positive')),
    sa.CheckConstraint('submitted_entrance_number > 0', name=op.f('ck_resident_requests_entrance_number_positive')),
    sa.ForeignKeyConstraint(['applicant_user_id'], ['identity.users.id'], name=op.f('fk_resident_requests_applicant_user_id_users')),
    sa.ForeignKeyConstraint(['cancel_requested_by'], ['identity.users.id'], name=op.f('fk_resident_requests_cancel_requested_by_users')),
    sa.ForeignKeyConstraint(['decided_by'], ['identity.users.id'], name=op.f('fk_resident_requests_decided_by_users')),
    sa.ForeignKeyConstraint(['house_id'], ['housing.houses.id'], name=op.f('fk_resident_requests_house_id_houses')),
    sa.ForeignKeyConstraint(['resolved_apartment_id'], ['housing.apartments.id'], name=op.f('fk_resident_requests_resolved_apartment_id_apartments')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_resident_requests')),
    schema='access'
    )
    op.create_index('ix_resident_requests_house_status', 'resident_requests', ['house_id', 'status'], unique=False, schema='access')
    op.create_index('ix_resident_requests_applicant', 'resident_requests', ['applicant_user_id', 'created_at'], unique=False, schema='access')
    op.create_index('uq_resident_requests_active_location', 'resident_requests', ['applicant_user_id', 'house_id', 'submitted_entrance_number', 'submitted_apartment_number'], unique=True, schema='access', postgresql_where=sa.text("status IN ('open', 'reviewing', 'needs_info')"))
    op.create_table('messages',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('card_id', sa.UUID(), nullable=False),
    sa.Column('origin_card_id', sa.UUID(), nullable=False),
    sa.Column('author_user_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('resident_comment', 'official_uk')", name=op.f('ck_messages_kind_allowed')),
    sa.ForeignKeyConstraint(['author_user_id'], ['identity.users.id'], name=op.f('fk_messages_author_user_id_users')),
    sa.ForeignKeyConstraint(['card_id'], ['issues.cards.id'], name=op.f('fk_messages_card_id_cards')),
    sa.ForeignKeyConstraint(['origin_card_id'], ['issues.cards.id'], name=op.f('fk_messages_origin_card_id_cards')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_messages')),
    schema='issues'
    )
    op.create_index('ix_messages_card_created', 'messages', ['card_id', 'created_at'], unique=False, schema='issues')
    op.create_table('reports',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('card_id', sa.UUID(), nullable=False),
    sa.Column('origin_card_id', sa.UUID(), nullable=False),
    sa.Column('author_user_id', sa.UUID(), nullable=False),
    sa.Column('raw_description', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['author_user_id'], ['identity.users.id'], name=op.f('fk_reports_author_user_id_users')),
    sa.ForeignKeyConstraint(['card_id'], ['issues.cards.id'], name=op.f('fk_reports_card_id_cards')),
    sa.ForeignKeyConstraint(['origin_card_id'], ['issues.cards.id'], name=op.f('fk_reports_origin_card_id_cards')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_reports')),
    schema='issues'
    )
    op.create_index('ix_reports_card_created', 'reports', ['card_id', 'created_at'], unique=False, schema='issues')
    op.create_table('supports',
    sa.Column('card_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('supported_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['card_id'], ['issues.cards.id'], name=op.f('fk_supports_card_id_cards')),
    sa.ForeignKeyConstraint(['user_id'], ['identity.users.id'], name=op.f('fk_supports_user_id_users')),
    sa.PrimaryKeyConstraint('card_id', 'user_id', name=op.f('pk_supports')),
    schema='issues'
    )
    op.create_index('ix_supports_user', 'supports', ['user_id'], unique=False, schema='issues')
    op.create_table('targets',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('card_id', sa.UUID(), nullable=False),
    sa.Column('house_id', sa.UUID(), nullable=False),
    sa.Column('entrance_number', sa.Integer(), nullable=True),
    sa.Column('apartment_id', sa.UUID(), nullable=True),
    sa.CheckConstraint('(entrance_number IS NOT NULL) <> (apartment_id IS NOT NULL)', name=op.f('ck_targets_one_target_kind')),
    sa.CheckConstraint('entrance_number IS NULL OR entrance_number > 0', name=op.f('ck_targets_entrance_number_positive')),
    sa.ForeignKeyConstraint(['apartment_id'], ['housing.apartments.id'], name=op.f('fk_targets_apartment_id_apartments')),
    sa.ForeignKeyConstraint(['card_id'], ['issues.cards.id'], name=op.f('fk_targets_card_id_cards')),
    sa.ForeignKeyConstraint(['house_id'], ['housing.houses.id'], name=op.f('fk_targets_house_id_houses')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_targets')),
    schema='issues'
    )
    op.create_index('uq_targets_card_apartment', 'targets', ['card_id', 'apartment_id'], unique=True, schema='issues', postgresql_where=sa.text('apartment_id IS NOT NULL'))
    op.create_index('ix_targets_house', 'targets', ['house_id'], unique=False, schema='issues')
    op.create_index('uq_targets_card_entrance', 'targets', ['card_id', 'entrance_number'], unique=True, schema='issues', postgresql_where=sa.text('entrance_number IS NOT NULL'))
    op.create_table('resident_grants',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('apartment_id', sa.UUID(), nullable=False),
    sa.Column('valid_from', sa.DateTime(timezone=True), nullable=False),
    sa.Column('valid_to', sa.DateTime(timezone=True), nullable=True),
    sa.Column('source_request_id', sa.UUID(), nullable=True),
    sa.Column('source_offer_id', sa.UUID(), nullable=True),
    sa.Column('granted_by', sa.UUID(), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_by', sa.UUID(), nullable=True),
    sa.Column('revoke_reason', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    postgresql.ExcludeConstraint((sa.column('user_id'), '='), (sa.column('apartment_id'), '='), (sa.text("tstzrange(valid_from, coalesce(valid_to, 'infinity'::timestamptz), '[)')"), '&&'), using='gist', name='ex_resident_grants_user_apartment_period'),
    sa.CheckConstraint('(source_request_id IS NOT NULL) <> (source_offer_id IS NOT NULL)', name=op.f('ck_resident_grants_one_source')),
    sa.CheckConstraint('valid_to IS NULL OR valid_to > valid_from', name=op.f('ck_resident_grants_valid_period')),
    sa.ForeignKeyConstraint(['apartment_id'], ['housing.apartments.id'], name=op.f('fk_resident_grants_apartment_id_apartments')),
    sa.ForeignKeyConstraint(['granted_by'], ['identity.users.id'], name=op.f('fk_resident_grants_granted_by_users')),
    sa.ForeignKeyConstraint(['revoked_by'], ['identity.users.id'], name=op.f('fk_resident_grants_revoked_by_users')),
    sa.ForeignKeyConstraint(['source_offer_id'], ['access.resident_offers.id'], name=op.f('fk_resident_grants_source_offer_id_resident_offers')),
    sa.ForeignKeyConstraint(['source_request_id'], ['access.resident_requests.id'], name=op.f('fk_resident_grants_source_request_id_resident_requests')),
    sa.ForeignKeyConstraint(['user_id'], ['identity.users.id'], name=op.f('fk_resident_grants_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_resident_grants')),
    sa.UniqueConstraint('source_offer_id', name='uq_resident_grants_source_offer'),
    sa.UniqueConstraint('source_request_id', name='uq_resident_grants_source_request'),
    schema='access'
    )
    op.create_index('ix_resident_grants_user_apartment', 'resident_grants', ['user_id', 'apartment_id'], unique=False, schema='access')
    op.create_table('bot_mutes',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('issue_id', sa.UUID(), nullable=True),
    sa.Column('resident_request_id', sa.UUID(), nullable=True),
    sa.Column('is_muted', sa.Boolean(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('(issue_id IS NOT NULL) <> (resident_request_id IS NOT NULL)', name=op.f('ck_bot_mutes_one_subject')),
    sa.ForeignKeyConstraint(['issue_id'], ['issues.cards.id'], name=op.f('fk_bot_mutes_issue_id_cards')),
    sa.ForeignKeyConstraint(['resident_request_id'], ['access.resident_requests.id'], name=op.f('fk_bot_mutes_resident_request_id_resident_requests')),
    sa.ForeignKeyConstraint(['user_id'], ['identity.users.id'], name=op.f('fk_bot_mutes_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_bot_mutes')),
    schema='system'
    )
    op.create_index('uq_bot_mutes_issue', 'bot_mutes', ['user_id', 'issue_id'], unique=True, schema='system', postgresql_where=sa.text('issue_id IS NOT NULL'))
    op.create_index('uq_bot_mutes_resident_request', 'bot_mutes', ['user_id', 'resident_request_id'], unique=True, schema='system', postgresql_where=sa.text('resident_request_id IS NOT NULL'))
    op.create_table('files',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('storage_key', sa.Text(), nullable=False),
    sa.Column('uploader_user_id', sa.UUID(), nullable=False),
    sa.Column('draft_id', sa.UUID(), nullable=True),
    sa.Column('issue_report_id', sa.UUID(), nullable=True),
    sa.Column('issue_message_id', sa.UUID(), nullable=True),
    sa.Column('company_registration_request_id', sa.UUID(), nullable=True),
    sa.Column('house_addition_request_id', sa.UUID(), nullable=True),
    sa.Column('original_name', sa.Text(), nullable=False),
    sa.Column('mime_type', sa.Text(), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('sha256', sa.Text(), nullable=False),
    sa.Column('state', sa.Text(), server_default=sa.text("'staged'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('ready_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("state <> 'ready' OR (draft_id IS NULL AND ((issue_report_id IS NOT NULL)::integer + (issue_message_id IS NOT NULL)::integer + (company_registration_request_id IS NOT NULL)::integer + (house_addition_request_id IS NOT NULL)::integer) = 1)", name=op.f('ck_files_ready_has_one_parent')),
    sa.CheckConstraint("state <> 'staged' OR (draft_id IS NOT NULL AND issue_report_id IS NULL AND issue_message_id IS NULL AND company_registration_request_id IS NULL AND house_addition_request_id IS NULL)", name=op.f('ck_files_staged_has_only_draft')),
    sa.CheckConstraint("state IN ('staged', 'ready', 'rejected')", name=op.f('ck_files_state_allowed')),
    sa.CheckConstraint('size_bytes >= 0', name=op.f('ck_files_size_nonnegative')),
    sa.ForeignKeyConstraint(['company_registration_request_id'], ['access.company_registration_requests.id'], name=op.f('fk_files_company_registration_request_id_company_registration_requests')),
    sa.ForeignKeyConstraint(['draft_id'], ['system.drafts.id'], name=op.f('fk_files_draft_id_drafts')),
    sa.ForeignKeyConstraint(['house_addition_request_id'], ['access.house_addition_requests.id'], name=op.f('fk_files_house_addition_request_id_house_addition_requests')),
    sa.ForeignKeyConstraint(['issue_message_id'], ['issues.messages.id'], name=op.f('fk_files_issue_message_id_messages')),
    sa.ForeignKeyConstraint(['issue_report_id'], ['issues.reports.id'], name=op.f('fk_files_issue_report_id_reports')),
    sa.ForeignKeyConstraint(['uploader_user_id'], ['identity.users.id'], name=op.f('fk_files_uploader_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_files')),
    sa.UniqueConstraint('storage_key', name=op.f('uq_files_storage_key')),
    schema='system'
    )
    # ### end Alembic commands ###

    op.execute(
        """
        INSERT INTO issues.categories (id, code, name, is_active, sort_order)
        VALUES
          (gen_random_uuid(), 'elevator', 'Лифт', true, 10),
          (gen_random_uuid(), 'water', 'Водоснабжение', true, 20),
          (gen_random_uuid(), 'electricity', 'Электричество', true, 30),
          (gen_random_uuid(), 'other', 'Другое', true, 100)
        """
    )
    op.execute(
        """
        CREATE FUNCTION system.block_audit_change() RETURNS trigger
        LANGUAGE plpgsql AS $body$
        BEGIN
            RAISE EXCEPTION 'audit events are append-only';
        END;
        $body$
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_append_only
        BEFORE UPDATE OR DELETE ON system.audit_events
        FOR EACH ROW EXECUTE FUNCTION system.block_audit_change()
        """
    )
    op.execute(
        """
        CREATE FUNCTION system.protect_issue_report() RETURNS trigger
        LANGUAGE plpgsql AS $body$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'issue reports cannot be deleted';
            END IF;
            IF ROW(NEW.id, NEW.origin_card_id, NEW.author_user_id,
                   NEW.raw_description, NEW.created_at)
               IS DISTINCT FROM
               ROW(OLD.id, OLD.origin_card_id, OLD.author_user_id,
                   OLD.raw_description, OLD.created_at) THEN
                RAISE EXCEPTION 'original issue report cannot be changed';
            END IF;
            RETURN NEW;
        END;
        $body$
        """
    )
    op.execute(
        """
        CREATE TRIGGER issue_reports_preserve_original
        BEFORE UPDATE OR DELETE ON issues.reports
        FOR EACH ROW EXECUTE FUNCTION system.protect_issue_report()
        """
    )
    op.execute(
        """
        CREATE FUNCTION system.protect_issue_message() RETURNS trigger
        LANGUAGE plpgsql AS $body$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'issue messages cannot be deleted';
            END IF;
            IF ROW(NEW.id, NEW.origin_card_id, NEW.author_user_id,
                   NEW.kind, NEW.body, NEW.created_at)
               IS DISTINCT FROM
               ROW(OLD.id, OLD.origin_card_id, OLD.author_user_id,
                   OLD.kind, OLD.body, OLD.created_at) THEN
                RAISE EXCEPTION 'original issue message cannot be changed';
            END IF;
            RETURN NEW;
        END;
        $body$
        """
    )
    op.execute(
        """
        CREATE TRIGGER issue_messages_preserve_original
        BEFORE UPDATE OR DELETE ON issues.messages
        FOR EACH ROW EXECUTE FUNCTION system.protect_issue_message()
        """
    )
    op.execute(
        """
        CREATE FUNCTION system.protect_ready_file() RETURNS trigger
        LANGUAGE plpgsql AS $body$
        BEGIN
            IF OLD.state = 'ready' THEN
                RAISE EXCEPTION 'ready files cannot be changed or deleted';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $body$
        """
    )
    op.execute(
        """
        CREATE TRIGGER files_preserve_ready
        BEFORE UPDATE OR DELETE ON system.files
        FOR EACH ROW EXECUTE FUNCTION system.protect_ready_file()
        """
    )


def downgrade() -> None:
    op.drop_table("files", schema="system")
    op.drop_table("bot_mutes", schema="system")
    op.drop_table("resident_grants", schema="access")
    op.drop_table("targets", schema="issues")
    op.drop_table("supports", schema="issues")
    op.drop_table("reports", schema="issues")
    op.drop_table("messages", schema="issues")
    op.drop_table("resident_requests", schema="access")
    op.drop_table("resident_offers", schema="access")
    op.drop_table("cards", schema="issues")
    op.drop_table("apartments", schema="housing")
    op.drop_table("house_addition_requests", schema="access")
    op.drop_table("staff_assignments", schema="identity")
    op.drop_table("houses", schema="housing")
    op.drop_table("companies", schema="housing")
    op.drop_table("notifications", schema="system")
    op.drop_table("drafts", schema="system")
    op.drop_table("command_receipts", schema="system")
    op.drop_table("audit_events", schema="system")
    op.drop_table("company_registration_requests", schema="access")
    op.drop_table("outbox_events", schema="system")
    op.drop_table("categories", schema="issues")
    op.drop_table("users", schema="identity")

    for function in (
        "system.protect_ready_file",
        "system.protect_issue_message",
        "system.protect_issue_report",
        "system.block_audit_change",
    ):
        op.execute(f"DROP FUNCTION {function}()")
    for schema in ("system", "issues", "access", "housing", "identity"):
        op.execute(f"DROP SCHEMA {schema}")

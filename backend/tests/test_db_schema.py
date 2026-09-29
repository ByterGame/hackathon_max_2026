import unittest

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from src.db.models import AdminOperation, Base, BotDialog, ResidentRequest, SupportInvitation, User


class DatabaseSchemaTests(unittest.TestCase):
    def test_all_proposed_tables_have_orm_models(self):
        self.assertEqual(len(Base.metadata.tables), 26)
        self.assertEqual(
            {table.schema for table in Base.metadata.tables.values()},
            {"identity", "housing", "access", "issues", "system"},
        )
        self.assertIn("system.admin_operations", Base.metadata.tables)
        self.assertEqual(AdminOperation.__table__.schema, "system")

    def test_active_resident_request_is_unique_per_location(self):
        index = next(
            index
            for index in ResidentRequest.__table__.indexes
            if index.name == "uq_resident_requests_active_location"
        )
        self.assertTrue(index.unique)
        self.assertEqual(
            [column.name for column in index.columns],
            [
                "applicant_user_id",
                "house_id",
                "submitted_entrance_number",
                "submitted_apartment_number",
            ],
        )
        sql = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        self.assertIn("WHERE status IN ('open', 'reviewing', 'needs_info')", sql)

    def test_bot_dialog_tracks_one_flow_per_user(self):
        table = BotDialog.__table__
        self.assertEqual(table.schema, "system")
        self.assertEqual({column.name for column in table.primary_key}, {"user_id"})
        self.assertEqual(
            {str(key.target_fullname) for key in table.foreign_keys},
            {"identity.users.id", "system.drafts.id"},
        )
        self.assertFalse(table.c.flow_kind.nullable)
        self.assertFalse(table.c.step.nullable)
        self.assertFalse(table.c.data.nullable)
        self.assertTrue(table.c.draft_id.nullable)
        self.assertEqual(str(table.c.data.server_default.arg), "'{}'::jsonb")

    def test_support_invitation_is_unique_while_pending(self):
        table = SupportInvitation.__table__
        self.assertEqual(table.schema, "identity")
        self.assertEqual(
            {key.target_fullname for key in table.foreign_keys},
            {"identity.users.id"},
        )
        index = next(
            index
            for index in table.indexes
            if index.name == "uq_support_invitations_pending_phone"
        )
        self.assertTrue(index.unique)
        self.assertEqual([column.name for column in index.columns], ["phone_number"])
        sql = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        self.assertIn("WHERE accepted_at IS NULL AND revoked_at IS NULL", sql)
        kind_check = next(
            constraint
            for constraint in User.__table__.constraints
            if constraint.name == "ck_users_user_kind"
        )
        self.assertIn("'admin'", str(kind_check.sqltext))


if __name__ == "__main__":
    unittest.main()

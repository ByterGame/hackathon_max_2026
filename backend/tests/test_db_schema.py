import unittest

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from src.db.models import Base, BotDialog, ResidentRequest


class DatabaseSchemaTests(unittest.TestCase):
    def test_all_proposed_tables_have_orm_models(self):
        self.assertEqual(len(Base.metadata.tables), 24)
        self.assertEqual(
            {table.schema for table in Base.metadata.tables.values()},
            {"identity", "housing", "access", "issues", "system"},
        )

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


if __name__ == "__main__":
    unittest.main()

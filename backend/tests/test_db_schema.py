import unittest

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from src.db.models import Base, ResidentRequest


class DatabaseSchemaTests(unittest.TestCase):
    def test_all_proposed_tables_have_orm_models(self):
        self.assertEqual(len(Base.metadata.tables), 23)
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


if __name__ == "__main__":
    unittest.main()

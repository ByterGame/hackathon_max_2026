import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from src.domain.files.service import get_file, upload
from src.domain.files.storage import FileError, path_for_key
from src.domain.issues.service import IssueError


async def unused_stream():
    yield b"not used"


class FileAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_ready_file_requires_current_parent_visibility(self) -> None:
        actor = SimpleNamespace(id=uuid4())
        report_id = uuid4()
        key = "a" * 64
        file = SimpleNamespace(
            state="ready", storage_key=key, issue_report_id=report_id,
            issue_message_id=None, company_registration_request_id=None,
            house_addition_request_id=None,
        )
        report = SimpleNamespace(card_id=uuid4())
        session = SimpleNamespace(get=AsyncMock(side_effect=[file, report]))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = path_for_key(root, key)
            path.parent.mkdir()
            path.write_bytes(b"private")
            with patch(
                "src.domain.files.service.get_visible_card",
                side_effect=IssueError("issue_not_found", "hidden", 404),
            ):
                with self.assertRaises(FileError) as denied:
                    await get_file(session, actor, file_id=uuid4(), root=root)
            self.assertEqual(denied.exception.status_code, 404)

            session.get = AsyncMock(side_effect=[file, report])
            with patch(
                "src.domain.files.service.get_visible_card",
                return_value=SimpleNamespace(id=report.card_id),
            ):
                found, found_path = await get_file(
                    session, actor, file_id=uuid4(), root=root
                )
            self.assertIs(found, file)
            self.assertEqual(found_path, path)

    async def test_upload_checks_parent_before_storing_bytes(self) -> None:
        actor = SimpleNamespace(id=uuid4())
        report = SimpleNamespace(card_id=uuid4(), author_user_id=actor.id)
        session = SimpleNamespace(get=AsyncMock(return_value=report))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch(
                "src.domain.files.service.get_visible_card",
                side_effect=IssueError("issue_not_found", "hidden", 404),
            ):
                with self.assertRaises(FileError):
                    await upload(
                        session, actor, root=root, chunks=unused_stream(),
                        filename="proof.png", content_type="image/png",
                        draft_id=None, parent_kind="issue_report",
                        parent_id=uuid4(),
                    )
            self.assertEqual(list(root.iterdir()), [])

    async def test_staged_file_is_owner_only(self) -> None:
        owner_id = uuid4()
        file = SimpleNamespace(
            state="staged", draft_id=uuid4(), uploader_user_id=owner_id,
        )
        session = SimpleNamespace(get=AsyncMock(return_value=file))
        actor = SimpleNamespace(id=uuid4())
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileError) as denied:
                await get_file(session, actor, file_id=uuid4(), root=Path(directory))
            self.assertEqual(denied.exception.status_code, 404)
            self.assertEqual(session.get.await_count, 1)

    async def test_admin_can_read_staged_file_without_changing_ownership(self) -> None:
        key = "b" * 64
        draft_id = uuid4()
        owner_id = uuid4()
        file = SimpleNamespace(
            state="staged",
            draft_id=draft_id,
            uploader_user_id=owner_id,
            storage_key=key,
        )
        draft = SimpleNamespace(
            id=draft_id,
            owner_user_id=owner_id,
            expires_at=None,
            flow_kind="issue_card",
        )
        session = SimpleNamespace(get=AsyncMock(side_effect=[file, draft]))
        actor = SimpleNamespace(id=uuid4(), kind="admin")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = path_for_key(root, key)
            path.parent.mkdir()
            path.write_bytes(b"private")
            found, found_path = await get_file(session, actor, file_id=uuid4(), root=root)
        self.assertIs(found, file)
        self.assertEqual(found_path, path)
        self.assertEqual(file.uploader_user_id, owner_id)


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from maxapi.types.attachments import File as MaxFile
from maxapi.types.attachments import Image, Video
from maxapi.types.attachments.attachment import (
    OtherAttachmentPayload,
    PhotoAttachmentPayload,
)
from maxapi.types.attachments.video import VideoUrl
from maxapi.types.input_media import InputMediaBuffer

from src.bot.handlers import media_text
from src.domain.files.storage import FileError


class MaxMediaSourceTests(unittest.IsolatedAsyncioTestCase):
    def test_rejects_non_max_or_unsafe_urls(self) -> None:
        for url in (
            "http://i.oneme.ru/i",
            "https://i.oneme.ru.evil.test/i",
            "https://127.0.0.1/i",
            "https://user@i.oneme.ru/i",
            "https://i.oneme.ru:8443/i",
            "https://i.oneme.ru/i#fragment",
        ):
            with self.subTest(url=url), self.assertRaises(FileError):
                media_text._safe_max_url(url)

    def test_accepts_only_explicit_max_media_hosts(self) -> None:
        self.assertEqual(
            media_text._safe_max_url("https://i.oneme.ru/i?r=token"),
            "https://i.oneme.ru/i?r=token",
        )

    async def test_extracts_photo_and_file_urls(self) -> None:
        image = Image(
            type="image",
            payload=PhotoAttachmentPayload(
                photo_id=1, token="t", url="https://i.oneme.ru/i?r=1"
            ),
        )
        file = MaxFile(
            type="file",
            filename="notice.pdf",
            size=123,
            payload=OtherAttachmentPayload(url="https://fu.oneme.ru/api/file"),
        )
        self.assertEqual(
            await media_text._source(image, bot=None),
            ("https://i.oneme.ru/i?r=1", None, "image"),
        )
        self.assertEqual(
            await media_text._source(file, bot=None),
            ("https://fu.oneme.ru/api/file", "notice.pdf", "file"),
        )

    async def test_fetches_video_url_by_token_when_missing(self) -> None:
        video = Video(type="video", token="video-token")
        bot = SimpleNamespace(
            get_video=AsyncMock(
                return_value=Video(
                    type="video",
                    token="video-token",
                    urls=VideoUrl(mp4_360="https://vu.okcdn.ru/movie.mp4"),
                )
            )
        )
        self.assertEqual(
            await media_text._source(video, bot=bot),
            ("https://vu.okcdn.ru/movie.mp4", None, "video"),
        )
        bot.get_video.assert_awaited_once_with("video-token")

    async def test_rejects_large_file_before_download(self) -> None:
        file = MaxFile(
            type="file",
            filename="notice.pdf",
            size=media_text.MAX_FILE_BYTES + 1,
            payload=OtherAttachmentPayload(url="https://fu.oneme.ru/api/file"),
        )
        with self.assertRaises(FileError) as context:
            await media_text._source(file, bot=None)
        self.assertEqual(context.exception.status_code, 413)


class MediaCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_requires_attached_media(self) -> None:
        result = await media_text.handle_media_text(
            None, None, f"/file draft {uuid4()}", [], bot=None
        )
        self.assertIn("Прикрепите", result.reply)

    async def test_uploads_to_owned_draft(self) -> None:
        draft_id = uuid4()
        image = Image(
            type="image",
            payload=PhotoAttachmentPayload(
                photo_id=1, token="t", url="https://i.oneme.ru/i?r=1"
            ),
        )
        stored = SimpleNamespace(id=uuid4(), storage_key="a" * 64)
        with (
            patch.object(
                media_text,
                "get_draft",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        flow_kind="issue_card", submitted_at=None
                    )
                ),
            ),
            patch.object(
                media_text, "_download_and_store", new=AsyncMock(return_value=stored)
            ) as upload,
        ):
            result = await media_text.handle_media_text(
                None, None, f"/file draft {draft_id}", [image], bot=None
            )
        self.assertEqual(result.storage_key, stored.storage_key)
        self.assertEqual(upload.await_args.kwargs["draft_id"], draft_id)
        self.assertIsNone(upload.await_args.kwargs["parent_id"])

    async def test_uploads_to_new_comment_on_visible_card(self) -> None:
        card_id = uuid4()
        message_id = uuid4()
        file = MaxFile(
            type="file",
            filename="notice.pdf",
            size=123,
            payload=OtherAttachmentPayload(url="https://fu.oneme.ru/api/file"),
        )
        stored = SimpleNamespace(id=uuid4(), storage_key="b" * 64)
        with (
            patch.object(
                media_text,
                "get_visible_card",
                new=AsyncMock(return_value=SimpleNamespace(id=card_id, status="open")),
            ),
            patch.object(
                media_text,
                "add_comment",
                new=AsyncMock(return_value=SimpleNamespace(id=message_id)),
            ) as comment,
            patch.object(
                media_text, "_download_and_store", new=AsyncMock(return_value=stored)
            ) as upload,
        ):
            result = await media_text.handle_media_text(
                None,
                None,
                f"/file card {card_id} | Дополнительное фото",
                [file],
                bot=None,
            )
        self.assertEqual(result.storage_key, stored.storage_key)
        comment.assert_awaited_once_with(None, None, card_id, "Дополнительное фото")
        self.assertEqual(upload.await_args.kwargs["parent_id"], message_id)


class CardAttachmentTests(unittest.IsolatedAsyncioTestCase):
    def test_private_file_reader_rejects_changed_size_and_symlink(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "stored"
            path.write_bytes(b"%PDF")
            self.assertEqual(media_text._read_private_file(path, 4), b"%PDF")
            with self.assertRaises(FileError):
                media_text._read_private_file(path, 3)
            link = Path(directory) / "link"
            link.symlink_to(path)
            with self.assertRaises(FileError):
                media_text._read_private_file(link, 4)

    async def test_list_shows_report_and_discussion_files_only_after_card_check(self):
        card_id = uuid4()
        files = [
            SimpleNamespace(
                id=uuid4(), issue_report_id=uuid4(), original_name="lift.jpg"
            ),
            SimpleNamespace(
                id=uuid4(), issue_report_id=None, original_name="repair.pdf"
            ),
        ]
        session = SimpleNamespace(
            scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: files))
        )
        with patch.object(
            media_text,
            "get_visible_card",
            new=AsyncMock(return_value=SimpleNamespace(id=card_id, title="Лифт")),
        ) as visible:
            reply = await media_text.list_card_attachments(session, object(), card_id)
        visible.assert_awaited_once()
        payloads = [button.payload for row in reply.buttons for button in row]
        self.assertIn(f"f:get:{card_id}:{files[0].id}", payloads)
        self.assertIn(f"f:get:{card_id}:{files[1].id}", payloads)
        self.assertIn(f"i:card:{card_id}", payloads)
        self.assertIn("Описание", reply.buttons[0][0].text)
        self.assertIn("Обсуждение", reply.buttons[1][0].text)
        query = str(session.scalars.await_args.args[0])
        self.assertIn("issues.reports", query)
        self.assertIn("issues.messages", query)
        self.assertIn("system.files.state", query)

    async def test_list_paginates_without_truncating_access_to_later_files(self):
        card_id = uuid4()
        files = [
            SimpleNamespace(
                id=uuid4(), issue_report_id=uuid4(), original_name=f"{index}.jpg"
            )
            for index in range(11)
        ]
        session = SimpleNamespace(
            scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: files))
        )
        with patch.object(
            media_text,
            "get_visible_card",
            new=AsyncMock(return_value=SimpleNamespace(id=card_id, title="Лифт")),
        ):
            reply = await media_text.list_card_attachments(
                session, object(), card_id, page=2
            )
        payloads = [button.payload for row in reply.buttons for button in row]
        self.assertEqual(sum(item.startswith("f:get:") for item in payloads), 10)
        self.assertIn(f"f:card:{card_id}:1", payloads)
        self.assertIn(f"f:card:{card_id}:3", payloads)

    async def test_download_reads_private_bytes_only_after_both_access_checks(self):
        card_id, file_id, report_id = uuid4(), uuid4(), uuid4()
        file = SimpleNamespace(
            issue_report_id=report_id,
            issue_message_id=None,
            size_bytes=4,
            original_name="lift.pdf",
        )
        session = SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(card_id=card_id))
        )
        with (
            patch.object(
                media_text,
                "get_visible_card",
                new=AsyncMock(return_value=SimpleNamespace(id=card_id)),
            ) as visible,
            patch.object(
                media_text,
                "get_file",
                new=AsyncMock(return_value=(file, Path("/private/file"))),
            ) as get_file,
            patch.object(media_text, "storage_root", return_value=Path("/private")),
            patch.object(
                media_text, "_read_private_file", return_value=b"%PDF"
            ) as read,
        ):
            reply = await media_text.get_card_attachment(
                session, object(), card_id, file_id
            )
        visible.assert_awaited_once()
        get_file.assert_awaited_once()
        read.assert_called_once_with(Path("/private/file"), 4)
        self.assertIsInstance(reply.media, InputMediaBuffer)
        self.assertEqual(reply.media.buffer, b"%PDF")
        self.assertEqual(reply.media.filename, "lift")

    async def test_foreign_file_callback_cannot_read_bytes(self):
        card_id, other_card_id, file_id, message_id = (uuid4() for _ in range(4))
        file = SimpleNamespace(
            issue_report_id=None,
            issue_message_id=message_id,
            size_bytes=4,
            original_name="note.pdf",
        )
        session = SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(card_id=other_card_id))
        )
        with (
            patch.object(
                media_text,
                "get_visible_card",
                new=AsyncMock(return_value=SimpleNamespace(id=card_id)),
            ),
            patch.object(
                media_text,
                "get_file",
                new=AsyncMock(return_value=(file, Path("/private/file"))),
            ),
            patch.object(media_text, "storage_root", return_value=Path("/private")),
            patch.object(media_text, "_read_private_file") as read,
        ):
            with self.assertRaises(FileError) as context:
                await media_text.get_card_attachment(
                    session, object(), card_id, file_id
                )
        self.assertEqual(context.exception.status_code, 404)
        read.assert_not_called()

    async def test_revoked_access_cannot_reach_file_or_bytes(self):
        card_id, file_id = uuid4(), uuid4()
        session = SimpleNamespace()
        with (
            patch.object(
                media_text,
                "get_visible_card",
                new=AsyncMock(side_effect=FileError(404, "no_access", "Нет доступа")),
            ),
            patch.object(media_text, "get_file", new=AsyncMock()) as get_file,
            patch.object(media_text, "_read_private_file") as read,
        ):
            with self.assertRaises(FileError):
                await media_text.get_card_attachment(
                    session, object(), card_id, file_id
                )
        get_file.assert_not_awaited()
        read.assert_not_called()


class MediaDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_redirect_to_untrusted_host(self) -> None:
        class Response:
            status = 302
            headers = {"Location": "https://127.0.0.1/private"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            def get(self, *_args, **_kwargs):
                return Response()

        with (
            patch.object(media_text, "ClientSession", return_value=Client()),
            patch.object(media_text, "create_default_connector", return_value=None),
            patch.object(media_text, "upload", new=AsyncMock()) as upload,
        ):
            with self.assertRaises(FileError) as context:
                await media_text._download_and_store(
                    None,
                    None,
                    url="https://i.oneme.ru/i?r=1",
                    original_name=None,
                    kind="image",
                    draft_id=uuid4(),
                    parent_id=None,
                )
        self.assertEqual(context.exception.code, "invalid_max_url")
        upload.assert_not_awaited()

    async def test_streams_valid_photo_without_buffering_entire_response(self) -> None:
        data = b"\xff\xd8\xff" + b"x" * 80

        class Content:
            def iter_chunked(self, _size):
                async def chunks():
                    yield data[:17]
                    yield data[17:]

                return chunks()

        class Response:
            status = 200
            headers = {}
            content_length = len(data)
            content = Content()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            def get(self, *_args, **_kwargs):
                return Response()

        async def upload(_session, _actor, **kwargs):
            self.assertEqual(kwargs["filename"], "max_image.jpg")
            self.assertEqual(kwargs["content_type"], "image/jpeg")
            received = bytearray()
            async for chunk in kwargs["chunks"]:
                received.extend(chunk)
            self.assertEqual(bytes(received), data)
            return SimpleNamespace(id=uuid4(), storage_key="c" * 64)

        with (
            patch.object(media_text, "ClientSession", return_value=Client()),
            patch.object(media_text, "create_default_connector", return_value=None),
            patch.object(
                media_text, "storage_root", return_value=Path("/tmp/media-test")
            ),
            patch.object(media_text, "upload", side_effect=upload),
        ):
            stored = await media_text._download_and_store(
                None,
                None,
                url="https://i.oneme.ru/i?r=1",
                original_name=None,
                kind="image",
                draft_id=uuid4(),
                parent_id=None,
            )
        self.assertEqual(stored.storage_key, "c" * 64)


if __name__ == "__main__":
    unittest.main()

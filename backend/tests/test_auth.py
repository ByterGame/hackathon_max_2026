import hashlib
import hmac
import json
import unittest
from urllib.parse import urlencode

from src.common.auth import (
    normalize_phone_number,
    validate_bot_contact,
    validate_contact,
    validate_init_data,
)


class MaxAuthenticationTests(unittest.TestCase):
    TOKEN = "test-bot-token"
    NOW = 1_800_000_000

    def make_init_data(self, **overrides: str) -> str:
        fields = {
            "auth_date": str(self.NOW),
            "query_id": "session-1",
            "user": json.dumps({"id": 123, "first_name": "Иван"}, separators=(",", ":")),
        }
        fields.update(overrides)
        signed_text = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
        secret = hmac.new(b"WebAppData", self.TOKEN.encode(), hashlib.sha256).digest()
        fields["hash"] = hmac.new(secret, signed_text.encode(), hashlib.sha256).hexdigest()
        return urlencode(fields)

    def test_signed_launch_data(self) -> None:
        profile = validate_init_data(self.make_init_data(), self.TOKEN, now=self.NOW)
        self.assertEqual(profile["id"], 123)

    def test_rejects_tampering_and_duplicate_keys(self) -> None:
        raw = self.make_init_data()
        with self.assertRaises(ValueError):
            validate_init_data(raw.replace("session-1", "session-2"), self.TOKEN, now=self.NOW)
        with self.assertRaises(ValueError):
            validate_init_data(raw + "&auth_date=1", self.TOKEN, now=self.NOW)

    def test_rejects_expired_data(self) -> None:
        with self.assertRaises(ValueError):
            validate_init_data(self.make_init_data(), self.TOKEN, now=self.NOW + 3601)

    def test_contact_signature_and_normalization(self) -> None:
        self.assertEqual(normalize_phone_number("8 (999) 123-45-67"), "79991234567")
        text = "authDate=1800000000\nphone=79991234567\nuserId=123"
        signature = hmac.new(self.TOKEN.encode(), text.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(
            validate_contact(
                phone="+7 (999) 123-45-67",
                auth_date=str(self.NOW),
                signature=signature,
                max_user_id="123",
                bot_token=self.TOKEN,
                now=self.NOW,
            ),
            "79991234567",
        )

    def test_bot_contact_requires_signed_own_vcard(self) -> None:
        vcard = "BEGIN:VCARD\r\nTEL;TYPE=cell:79991234567\r\nEND:VCARD\r\n"
        signature = hmac.new(
            self.TOKEN.encode(), vcard.encode(), hashlib.sha256
        ).hexdigest()
        kwargs = dict(
            vcf_info=vcard,
            signature=signature,
            sender_user_id="123",
            contact_user_id="123",
            bot_token=self.TOKEN,
        )
        self.assertEqual(validate_bot_contact(**kwargs), "79991234567")
        with self.assertRaises(ValueError):
            validate_bot_contact(**(kwargs | {"contact_user_id": "124"}))
        with self.assertRaises(ValueError):
            validate_bot_contact(**(kwargs | {"signature": "0" * 64}))


if __name__ == "__main__":
    unittest.main()

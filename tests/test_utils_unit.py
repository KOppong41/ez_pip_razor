import types
import unittest
from unittest import mock
from decimal import Decimal

from core import utils


class AuditLogTests(unittest.TestCase):
    def test_audit_log_merges_extra_and_payload(self):
        created = []

        class DummyManager:
            def create(self, **kwargs):
                created.append(kwargs)

        dummy_audit = types.SimpleNamespace(objects=DummyManager())
        with mock.patch.object(utils, "Audit", dummy_audit):
            utils.audit_log(
                "test.action",
                "Entity",
                123,
                payload={"a": 1},
                extra={"b": 2},
                actor=None,
            )

        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["payload"], {"a": 1, "b": 2})

    def test_audit_log_serializes_decimals(self):
        created = []

        class DummyManager:
            def create(self, **kwargs):
                created.append(kwargs)

        dummy_audit = types.SimpleNamespace(objects=DummyManager())
        payload = {
            "price": Decimal("1.2350"),
            "position": {"qty": Decimal("0E-8")},
            "legs": (Decimal("2.5"), Decimal("3")),
        }
        with mock.patch.object(utils, "Audit", dummy_audit):
            utils.audit_log("test.decimals", "Entity", 1, payload=payload)

        self.assertEqual(
            created[0]["payload"],
            {
                "price": "1.2350",
                "position": {"qty": "0E-8"},
                "legs": ["2.5", "3"],
            },
        )


if __name__ == "__main__":
    unittest.main()

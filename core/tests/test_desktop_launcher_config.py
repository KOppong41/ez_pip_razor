from unittest import TestCase

from desktop.backend_launcher import _merge_missing_config, validate_runtime_config


class DesktopLauncherConfigTests(TestCase):
    def test_missing_new_service_config_is_backfilled(self):
        legacy = {
            "django": {"port": 4000},
            "celery": {"worker": {"args": "custom-worker"}},
        }
        defaults = {
            "django": {"host": "127.0.0.1", "port": 8000},
            "celery": {
                "worker": {"args": "default-worker"},
                "mt5_worker": {"args": "default-mt5-worker"},
                "beat": {"args": "default-beat"},
            },
        }

        merged = _merge_missing_config(legacy, defaults)

        self.assertEqual(merged["django"]["port"], 4000)
        self.assertEqual(merged["django"]["host"], "127.0.0.1")
        self.assertEqual(merged["celery"]["worker"]["args"], "custom-worker")
        self.assertEqual(
            merged["celery"]["mt5_worker"]["args"], "default-mt5-worker"
        )
        validate_runtime_config(merged)

    def test_runtime_validation_rejects_a_missing_mt5_worker(self):
        config = {
            "celery": {
                "worker": {"args": "worker"},
                "beat": {"args": "beat"},
            }
        }

        with self.assertRaisesRegex(RuntimeError, "celery.mt5_worker.args"):
            validate_runtime_config(config)

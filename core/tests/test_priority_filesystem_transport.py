import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from kombu import Connection

from config.celery_priority_filesystem import register_transport


class PriorityFilesystemTransportTests(TestCase):
    def test_desktop_settings_keep_all_transport_files_under_desktop_root(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            env_file = root / ".env"
            env_file.write_text("", encoding="utf-8")
            environment = os.environ.copy()
            environment.update(
                {
                    "DJANGO_SETTINGS_MODULE": "config.settings_desktop",
                    "ENV_FILE": str(env_file),
                    "EZTRADE_DESKTOP_ROOT": str(root),
                }
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "from django.conf import settings; "
                        "print(settings.CELERY_BROKER_URL); "
                        "options = settings.CELERY_BROKER_TRANSPORT_OPTIONS; "
                        "print(options['data_folder_in']); "
                        "print(options['processed_folder']); "
                        "print(options['control_folder'])"
                    ),
                ],
                cwd=Path(__file__).resolve().parents[2],
                env=environment,
                capture_output=True,
                text=True,
                check=True,
            )

            output_lines = result.stdout.splitlines()
            self.assertIn("priorityfilesystem://", output_lines)
            for folder in ("queue", "processed", "control"):
                self.assertIn(str(root / "celery" / folder), output_lines)

    def test_lower_numeric_priority_is_consumed_first(self):
        register_transport()
        with TemporaryDirectory() as directory:
            queue_dir = Path(directory) / "queue"
            processed_dir = Path(directory) / "processed"
            control_dir = Path(directory) / "control"
            queue_dir.mkdir()
            processed_dir.mkdir()
            options = {
                "data_folder_in": str(queue_dir),
                "data_folder_out": str(queue_dir),
                "processed_folder": str(processed_dir),
                "control_folder": str(control_dir),
            }
            with Connection(
                "priorityfilesystem://",
                transport_options=options,
            ) as connection:
                queue = connection.SimpleQueue("mt5_execution")
                queue.put({"kind": "entry"}, priority=6)
                queue.put({"kind": "reconcile"}, priority=9)
                queue.put({"kind": "emergency"}, priority=0)

                emergency = queue.get(block=False)
                entry = queue.get(block=False)
                reconcile = queue.get(block=False)
                self.assertEqual(emergency.payload["kind"], "emergency")
                self.assertEqual(entry.payload["kind"], "entry")
                self.assertEqual(reconcile.payload["kind"], "reconcile")
                emergency.ack()
                entry.ack()
                reconcile.ack()
                queue.close()

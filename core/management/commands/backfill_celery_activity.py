import datetime
import json
import re
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

from core.models import CeleryActivity


LINE_RE = re.compile(
    r"""
    ^\[
    (?P<ts>\d{4}-\d{2}-\d{2}\ \d{2}:\d{2}:\d{2},\d{3})
    :\s+
    (?P<level>[A-Z]+)
    /
    (?P<process>[^\]]+)
    \]\s+
    (?P<body>.+)
    $
    """,
    re.X,
)


def _parse_ts(ts_raw: str):
    try:
        dt = datetime.datetime.strptime(ts_raw, "%Y-%m-%d %H:%M:%S,%f")
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    except Exception:
        return None


def _parse_line(line: str):
    line = line.strip()
    if not line.startswith("["):
        return None

    match = LINE_RE.match(line)
    if not match:
        return None

    ts = _parse_ts(match.group("ts"))
    level = match.group("level")
    body = match.group("body")

    component = "worker"
    message = body
    task_name = ""
    task_id = ""
    payload = {}

    if body.startswith("Task "):
        component = "task"
        remainder = body[len("Task ") :]
        # Example: execution.tasks.kill_switch_monitor_task[uuid] succeeded in 0.3s: {...}
        if "[" in remainder and "]" in remainder:
            name_part, rest = remainder.split("[", 1)
            task_name = name_part.strip()
            task_id = rest.split("]", 1)[0]
            message = rest.split("]", 1)[1].strip()
        payload = {"raw": body}
    elif body.startswith("{"):
        try:
            data = json.loads(body)
            component = data.get("action", "worker")
            task_name = data.get("action", "")
            payload = data
            message = data.get("action", body)
            task_id = str(data.get("task_id", "")) if data.get("task_id") else ""
        except Exception:
            payload = {"raw": body}
    elif body.startswith("[KillSwitch]"):
        component = "kill_switch"
        payload = {"raw": body}
    elif "Task handler raised error" in body:
        component = "task"

    return {
        "ts": ts,
        "level": level,
        "component": component,
        "message": message,
        "task_name": task_name,
        "task_id": task_id,
        "payload": payload,
    }


class Command(BaseCommand):
    help = "Backfill CeleryActivity rows from a Celery log file."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("logfile", type=str, help="Path to celery log file to parse.")
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum lines to read (from the end) for quick runs.",
        )

    def handle(self, *args, **options):
        logfile = options["logfile"]
        limit = options.get("limit")

        lines = None
        for enc in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
            try:
                with open(logfile, "r", encoding=enc) as fh:
                    lines = fh.readlines()
                    break
            except FileNotFoundError:
                self.stderr.write(self.style.ERROR(f"Log file not found: {logfile}"))
                return
            except UnicodeDecodeError:
                continue
        if lines is None:
            self.stderr.write(self.style.ERROR("Could not decode log file with utf-8/utf-16 variants."))
            return

        if limit:
            lines = lines[-limit:]

        created = 0
        skipped = 0
        last_task_name = ""
        last_task_id = ""

        i = 0
        while i < len(lines):
            line = lines[i]
            parsed = _parse_line(line)
            if not parsed or not parsed["ts"]:
                skipped += 1
                i += 1
                continue

            # Capture traceback lines following an error marker.
            if "Task handler raised error" in parsed["message"]:
                traceback_lines = []
                j = i + 1
                while j < len(lines) and not lines[j].lstrip().startswith("["):
                    traceback_lines.append(lines[j].rstrip("\n"))
                    j += 1
                if traceback_lines:
                    payload = parsed.get("payload", {}) or {}
                    payload["traceback"] = "\n".join(traceback_lines)
                    parsed["payload"] = payload
                if not parsed.get("task_name") and last_task_name:
                    parsed["task_name"] = last_task_name
                    parsed["task_id"] = last_task_id
                i = j  # jump past the traceback
            else:
                i += 1

            # Track last task context for attaching to subsequent errors.
            if parsed["component"] == "task" and parsed["task_name"]:
                last_task_name = parsed["task_name"]
                last_task_id = parsed["task_id"]

            exists = CeleryActivity.objects.filter(
                ts=parsed["ts"],
                message=parsed["message"],
                task_id=parsed["task_id"],
            ).exists()
            if exists:
                skipped += 1
                continue

            CeleryActivity.objects.create(**parsed)
            created += 1

        self.stdout.write(
            self.style.SUCCESS(f"Backfill complete. created={created} skipped={skipped}")
        )

"""Priority-aware variant of Kombu's self-contained filesystem transport."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import uuid
from queue import Empty
from time import monotonic

from kombu.exceptions import ChannelError
from kombu.transport import TRANSPORT_ALIASES
from kombu.transport import filesystem
from kombu.utils.encoding import bytes_to_str, str_to_bytes
from kombu.utils.json import dumps, loads


_PRIORITY_FILE = re.compile(r"^p(?P<priority>\d{2})_(?P<sequence>\d+)_")
_DEFAULT_PRIORITY = 6


def _message_priority(payload) -> int:
    try:
        priority = int((payload.get("properties") or {}).get("priority", _DEFAULT_PRIORITY))
    except (AttributeError, TypeError, ValueError):
        priority = _DEFAULT_PRIORITY
    return max(0, min(9, priority))


def _file_priority(filename: str) -> tuple[int, str]:
    match = _PRIORITY_FILE.match(filename)
    if match:
        return int(match.group("priority")), match.group("sequence")
    # Messages produced before this transport was introduced remain readable
    # and are treated as normal priority.
    return _DEFAULT_PRIORITY, filename


class PriorityChannel(filesystem.Channel):
    supports_priority = True

    def _put(self, queue, payload, **kwargs):
        priority = _message_priority(payload)
        filename = "p{:02d}_{}_{}.{}.msg".format(
            priority,
            int(round(monotonic() * 1000)),
            uuid.uuid4(),
            queue,
        )
        path = os.path.join(self.data_folder_out, filename)
        stream = None
        try:
            stream = open(path, "wb", buffering=0)
            filesystem.lock(stream, filesystem.LOCK_EX)
            stream.write(str_to_bytes(dumps(payload)))
        except OSError as exc:
            raise ChannelError(f"Cannot add file {path!r} to directory") from exc
        finally:
            if stream is not None:
                filesystem.unlock(stream)
                stream.close()

    def _get(self, queue):
        queue_marker = f".{queue}.msg"
        filenames = sorted(os.listdir(self.data_folder_in), key=_file_priority)
        for filename in filenames:
            if queue_marker not in filename:
                continue
            processed_folder = self.processed_folder if self.store_processed else tempfile.gettempdir()
            source = os.path.join(self.data_folder_in, filename)
            try:
                destination = shutil.move(source, processed_folder)
            except OSError:
                # Another worker may have claimed the message first.
                continue
            try:
                with open(destination, "rb") as stream:
                    payload = stream.read()
                if not self.store_processed:
                    os.remove(destination)
            except OSError as exc:
                raise ChannelError(f"Cannot read file {destination!r} from queue") from exc
            return loads(bytes_to_str(payload))
        raise Empty()


class Transport(filesystem.Transport):
    Channel = PriorityChannel
    driver_type = "priorityfilesystem"
    driver_name = "priorityfilesystem"


def register_transport() -> None:
    TRANSPORT_ALIASES["priorityfilesystem"] = (
        "config.celery_priority_filesystem:Transport"
    )

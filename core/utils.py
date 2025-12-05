import json
import logging


structured_logger = logging.getLogger("structured")


def structured_log(action: str, **fields):
    """
    Emit a JSON log line with a consistent 'action' field for downstream ingestion.
    """
    record = {"action": action, **fields}
    try:
        structured_logger.info(json.dumps(record, default=str))
    except Exception:
        # Fallback to plain logging if JSON serialization fails
        try:
            structured_logger.info({"action": action, **fields})
        except Exception:
            # last resort: print
            print(record)

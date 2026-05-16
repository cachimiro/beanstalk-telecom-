"""Parse 3CX recording filenames from GCS object paths.

Expected format:
  recordings/{folder_extension}/[{user_name}]_{filename_extension}-{phone_number}_{timestamp}({call_id}).{file_extension}

Example:
  recordings/4166/[Celia Perez]_4166-01553888553_20260514131342(3644).wav
"""
import re
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

_PATTERN = re.compile(
    r"recordings/"
    r"(?P<folder_extension>\d+)/"
    r"\[(?P<user_name>[^\]]+)\]_"
    r"(?P<filename_extension>\d+)-"
    r"(?P<phone_number>\d+)_"
    r"(?P<timestamp>\d+)"
    r"\((?P<call_id>[^)]+)\)"
    r"\.(?P<file_extension>\w+)$"
)


@dataclass
class ParsedRecording:
    folder_extension: str
    user_name: str
    filename_extension: str
    phone_number: str
    timestamp: str
    call_id: str
    file_extension: str

    def formatted_date(self) -> str:
        """Convert timestamp 20260514131342 → '14 May 2026'."""
        try:
            from datetime import datetime
            dt = datetime.strptime(self.timestamp[:8], "%Y%m%d")
            return dt.strftime("%-d %B %Y")
        except Exception:
            return self.timestamp


def parse_recording_path(object_name: str) -> Optional[ParsedRecording]:
    """Return ParsedRecording or None if the path doesn't match."""
    match = _PATTERN.search(object_name)
    if not match:
        logger.warning("Failed to parse recording path: %s", object_name)
        return None
    g = match.groupdict()
    return ParsedRecording(
        folder_extension=g["folder_extension"],
        user_name=g["user_name"].strip(),
        filename_extension=g["filename_extension"],
        phone_number=g["phone_number"],
        timestamp=g["timestamp"],
        call_id=g["call_id"],
        file_extension=g["file_extension"].lower(),
    )

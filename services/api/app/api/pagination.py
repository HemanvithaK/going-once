import base64
import binascii
import uuid
from datetime import datetime


def encode_cursor(ends_at: datetime, listing_id: uuid.UUID) -> str:
    """Pack the sort key of the last row on a page into one opaque string."""
    raw = f"{ends_at.isoformat()}|{listing_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Unpack a cursor. Raises ValueError if it is not one we made."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ends_at_text, id_text = raw.split("|", 1)
        return datetime.fromisoformat(ends_at_text), uuid.UUID(id_text)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("malformed cursor") from exc
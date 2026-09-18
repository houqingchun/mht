from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    """Row bookkeeping timestamps.

    These two are the only timestamps in the schema the *database* writes
    (`server_default=func.now()`); every other one — `started_at`,
    `submitted_at`, `answered_at`, `calculated_at` — is written by Python from
    `assessment_service.now_utc_naive()`, i.e. UTC. On MySQL `func.now()` is
    `CURRENT_TIMESTAMP` evaluated in the server's session time zone, which is
    `SYSTEM` (here UTC+8), so `created_at` lands 8 hours ahead of the
    Python-written column on the very same row. Comparing the two without
    accounting for that reads as an 8-hour gap that has no cause in the code —
    check it against `SELECT now()` before hunting a bug. `DateTime(timezone=True)`
    does not help: it is a no-op on MySQL, where the column is a plain DATETIME.
    """

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


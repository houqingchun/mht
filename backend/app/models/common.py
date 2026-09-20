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

    Since 2026-09-19 the test suite runs on real MySQL too, so this gap is now
    reachable from a test: any assertion that compares a `created_at` against
    `now_utc_naive()` is 8 hours out. Fix the assertion's *basis* — do not
    "correct" the offset here, the two writers really do disagree in production.
    """

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


def now_local_naive() -> datetime:
    """The second clock — the one the database uses — for Python-written columns.

    `assessment_service.now_utc_naive()` is the first clock: it stamps the
    moments a *sitting* happened (`started_at` / `submitted_at` / `answered_at` /
    `tested_at`). This one stamps the moments a *document* lives through:
    `auth_session.issued_at` / `expires_at` / `last_seen_at` / `revoked_at` and
    `export_job.expires_at` / `downloaded_at` / `revoked_at`.

    Why local rather than UTC — three reasons, in order of weight:

    1. Each of those rows also carries a `created_at` written by `func.now()`,
       i.e. the server's wall clock (UTC+8 here). The SPA renders naive ISO
       strings verbatim (`{{ log.created_at }}`), with no time-zone conversion,
       so a UTC `expires_at` next to a local `created_at` on one screen would
       show the same instant twice, eight hours apart, with nothing on screen
       explaining why. Same class of problem as `student.masked_name` being read
       as a privacy control: one field, two meanings.
    2. `task_service.effective_task_status()` already compares
       `datetime.now()` (local) against `start_at` / `end_at`, because those are
       wall-clock times the operator typed. An export that is "valid for 7 days"
       is the same kind of promise, made to the same reader.
    3. Every comparison involving these columns happens server-side, so a single
       consistent basis is all that correctness needs.

    The cost, stated rather than hidden: moving the MySQL server's time zone
    shifts the meaning of already-stored values. That is the same property
    `assessment_task.end_at` has, and it is why the JWT `exp` keeps using UTC —
    it is compared against another UTC instant, never against these columns.
    """
    return datetime.now().replace(tzinfo=None)


from typing import Any

from pydantic import BaseModel, Field


class SettingsUpdateRequest(BaseModel):
    """Partial update: only the keys sent are touched.

    Values are validated against `DEFAULTS` in the service layer, which also
    enforces the type by comparing against the shipped default.
    """

    values: dict[str, Any] = Field(min_length=1)

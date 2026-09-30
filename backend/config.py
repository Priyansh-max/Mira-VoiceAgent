from __future__ import annotations

import os
from typing import Literal, cast


ResponseMode = Literal["llm", "speech_directive"]
VALID_RESPONSE_MODES = {"llm", "speech_directive"}


def get_response_mode() -> ResponseMode:
    value = os.environ.get("RESPONSE_MODE", "llm").strip().lower()
    if value not in VALID_RESPONSE_MODES:
        choices = ", ".join(sorted(VALID_RESPONSE_MODES))
        raise ValueError(f"RESPONSE_MODE must be one of: {choices}")
    return cast(ResponseMode, value)

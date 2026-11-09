"""Deterministic plain-text record quotation, not medical advice or interpretation."""

import json
from datetime import UTC
from decimal import Decimal

from app.schemas.consistency import FactVersion

RENDERER_VERSION = "record-restatement-text-v1"


def decimal_text(value: Decimal) -> str:
    if not value:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def render_restatement(row: FactVersion) -> str:
    """Call only on authoritative matched records. Rendering alone grants no trust.

    JSON string quoting keeps free-text fields literal; escaping HTML delimiters
    avoids treating stored descriptions as markup. Consumers must use plain text.
    """
    payload = row.fact.model_dump(mode="json")
    if row.fact.kind == "laboratory":
        payload["value"] = decimal_text(row.fact.value)
    elif row.fact.kind == "medication" and row.fact.dose_value is not None:
        payload["dose_value"] = decimal_text(row.fact.dose_value)
    quoted = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    quoted = quoted.replace("<", r"\u003c").replace(">", r"\u003e").replace("&", r"\u0026")
    observed = (
        row.observed_at.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )
    return (
        f"Recorded {row.attribution} {row.fact.kind} fact "
        f"(author {row.author_role} {row.author_id}; version {row.version_id}; "
        f"observed {observed}): {quoted}"
    )

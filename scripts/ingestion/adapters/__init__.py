"""Ingestion adapters package for operator-specific translations."""

from scripts.ingestion.adapters.arcride import (
    adapt_arcride,
    fetch_arcride_cabinets,
    make_cabinet_token,
    redact_arcride_payload,
    redact_arcride_record,
)
from scripts.ingestion.adapters.spiro import adapt_spiro

__all__ = [
    "adapt_arcride",
    "adapt_spiro",
    "fetch_arcride_cabinets",
    "make_cabinet_token",
    "redact_arcride_payload",
    "redact_arcride_record",
]

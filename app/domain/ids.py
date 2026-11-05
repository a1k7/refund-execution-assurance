"""Identifier generation. All IDs are opaque strings with a type prefix."""
from __future__ import annotations

import uuid


def new_business_operation_id() -> str:
    return f"bo_{uuid.uuid4().hex}"


def new_release_id() -> str:
    return f"rel_{uuid.uuid4().hex}"


def new_dispatch_attempt_id() -> str:
    return f"att_{uuid.uuid4().hex}"


def new_idempotency_key() -> str:
    return f"idem_{uuid.uuid4().hex}"


def new_correlation_id() -> str:
    return f"cor_{uuid.uuid4().hex}"


def new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex}"


def new_receiver_refund_reference() -> str:
    return f"rr_{uuid.uuid4().hex}"
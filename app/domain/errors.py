"""Typed exceptions. Keep all user-facing error types here."""
from __future__ import annotations


class DomainError(Exception):
    """Base class for all domain-level errors."""


class IllegalTransition(DomainError):
    pass


class ReleaseVerificationError(DomainError):
    pass


class ReleaseConsumptionError(DomainError):
    pass


class DuplicateOperationError(DomainError):
    pass


class AuthorityRevokedError(DomainError):
    pass


class ReceiverNotConfiguredError(DomainError):
    pass


class ReceiverCapabilityError(DomainError):
    pass


class EvidenceIntegrityError(DomainError):
    pass


class NotFoundError(DomainError):
    pass


class ConflictError(DomainError):
    pass


class BadRequestError(DomainError):
    pass
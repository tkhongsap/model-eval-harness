"""Custom exceptions for the RTR fraud validation pipeline.

Each service-specific exception provides meaningful context for tracking
and debugging purposes.
"""
from __future__ import annotations


class ServiceError(Exception):
    """Base exception for all service-related errors."""

    def __init__(self, message: str, details: dict | None = None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)


class EmailServiceError(ServiceError):
    """Raised when email sending fails via Microsoft Graph API."""

    def __init__(self, message: str = "Failed to send email", details: dict | None = None):
        super().__init__(message, details)


class GCSServiceError(ServiceError):
    """Raised when GCS operations fail."""

    def __init__(self, message: str = "GCS operation failed", details: dict | None = None):
        super().__init__(message, details)


class GeminiServiceError(ServiceError):
    """Raised when Gemini API operations fail."""

    def __init__(self, message: str = "Gemini API operation failed", details: dict | None = None):
        super().__init__(message, details)


class S3ServiceError(ServiceError):
    """Raised when S3 operations fail."""

    def __init__(self, message: str = "S3 operation failed", details: dict | None = None):
        super().__init__(message, details)


class SecretServiceError(ServiceError):
    """Raised when secret retrieval fails."""

    def __init__(self, message: str = "Failed to retrieve secret", details: dict | None = None):
        super().__init__(message, details)


class SharePointServiceError(ServiceError):
    """Raised when SharePoint operations fail."""

    def __init__(self, message: str = "SharePoint operation failed", details: dict | None = None):
        super().__init__(message, details)
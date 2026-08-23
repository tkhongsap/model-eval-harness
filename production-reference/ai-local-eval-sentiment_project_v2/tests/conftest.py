"""Shared fixtures.

The logger config is process-global and idempotent, so without a reset the first test to
configure it wins for the whole session and every later test's records carry the wrong service
identity.
"""

import pytest

from src.logger import Logger


@pytest.fixture(autouse=True)
def reset_logger():
    """Break the logger's configure-once invariant between tests."""
    Logger.reset_for_testing()
    yield
    Logger.reset_for_testing()

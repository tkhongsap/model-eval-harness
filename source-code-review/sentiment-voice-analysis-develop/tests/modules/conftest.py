"""Shared fixtures for module-level tests."""

import pytest


@pytest.fixture(autouse=True)
def _route_secure_session_to_requests(mocker):
    """Point the TLS-pinned session factory back at the module-level ``requests``.

    ``MSGraphModule`` and ``SharePointModule`` now perform HTTP calls through a
    ``TlsPolicy().session()`` instance (a ``requests.Session`` with a TLS 1.2 floor).
    The existing tests patch ``<module>.requests`` (whole module) or
    ``<module>.requests.<verb>``. Returning the *live* ``requests`` attribute at call
    time keeps those patches effective: ``self._session.<verb>`` then resolves to the
    same (patched) object the tests configure. Patching here is a no-op for modules
    that do not import ``TlsPolicy``.
    """
    import src.modules.microsoft.msgraph as msgraph_module
    import src.modules.microsoft.sharepoint as sharepoint_module

    sharepoint_policy = mocker.patch.object(sharepoint_module, "TlsPolicy")
    sharepoint_policy.return_value.session.side_effect = lambda: sharepoint_module.requests

    msgraph_policy = mocker.patch.object(msgraph_module, "TlsPolicy")
    msgraph_policy.return_value.session.side_effect = lambda: msgraph_module.requests

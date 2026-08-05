# Library imports
import ssl

import requests
from requests.adapters import HTTPAdapter


class TLSAdapter(HTTPAdapter):
    """A ``requests`` transport adapter that pins a given TLS ``SSLContext``.

    Injects the supplied context into both direct and proxied connection pools, so
    every request routed through a session using this adapter negotiates with the
    pinned TLS floor.
    """

    def __init__(self, ssl_context: ssl.SSLContext, **kwargs) -> None:
        """Store the context before ``HTTPAdapter`` builds its pool manager.

        Args:
            ssl_context: The SSL context to apply to all connection pools.
            **kwargs: Forwarded to ``HTTPAdapter.__init__``.
        """
        # Must be set before super().__init__(), which calls init_poolmanager().
        self._ssl_context = ssl_context
        super().__init__(**kwargs)

    def init_poolmanager(self, *args, **kwargs):
        """Attach the pinned context to the connection pool manager."""
        kwargs["ssl_context"] = self._ssl_context
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        """Attach the pinned context to the proxy connection manager."""
        kwargs["ssl_context"] = self._ssl_context
        return super().proxy_manager_for(*args, **kwargs)


class TlsPolicy:
    """Produces TLS-pinned ssl contexts, ``requests`` adapters, and sessions.

    Holds the minimum negotiated TLS version as state. Defaults to TLS 1.2 (not 1.3)
    so endpoints that do not yet offer 1.3 keep working; TLS 1.3 is still negotiated
    when both sides support it.
    """

    DEFAULT_MIN_VERSION = ssl.TLSVersion.TLSv1_2

    def __init__(self, min_version: ssl.TLSVersion | None = None) -> None:
        """Initialize the policy.

        Args:
            min_version: Minimum TLS protocol version to enforce. Defaults to
                :attr:`DEFAULT_MIN_VERSION` (TLS 1.2).
        """
        self._min_version = min_version or self.DEFAULT_MIN_VERSION

    def context(self) -> ssl.SSLContext:
        """Build an SSL context that refuses anything below the policy minimum.

        Starts from the standard secure default context (certificate verification
        and the system CA bundle stay enabled) and only raises the protocol floor.

        Returns:
            An ``ssl.SSLContext`` with ``minimum_version`` pinned to the policy.
        """
        context = ssl.create_default_context()
        context.minimum_version = self._min_version
        return context

    def adapter(self) -> TLSAdapter:
        """Return a ``requests`` adapter bound to this policy's TLS context."""
        return TLSAdapter(ssl_context=self.context())

    def session(self) -> requests.Session:
        """Create a ``requests.Session`` that enforces this policy's TLS floor.

        Mounts a :class:`TLSAdapter` for ``https://`` so every request made through
        the session uses the pinned TLS context.

        Returns:
            A configured ``requests.Session`` instance.
        """
        session = requests.Session()
        adapter = self.adapter()
        session.mount("https://", adapter)
        return session

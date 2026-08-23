"""Process-global structlog configurator.

The :class:`Logger` class is a thin, idempotent facade over structlog. It owns:

  * the structlog processor chain (which fields to add, in what order),
  * the renderer choice (JSON in prod / piped output, pretty console in a TTY),
  * the stdlib :mod:`logging` bridge so third-party libraries (google-cloud-*,
    sqlalchemy, urllib3, …) flow through the same renderer and pick up the
    same service / trace fields.

Configuration is applied exactly once per process; subsequent calls to
:meth:`Logger.configure` are no-ops. :meth:`Logger.reset_for_testing` is the
escape hatch tests use to reconfigure between cases.
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Any, cast

import structlog
from structlog.typing import Processor

from src.logger.config import LoggerConfig, Provider
from src.logger.processors import (
  ConsoleFieldFilterProcessor,
  GCPFormatterProcessor,
  OpenTelemetryProcessor,
  ServiceContextProcessor,
)


class Logger:
  """Process-global structlog facade.

  Idempotent: :meth:`configure` may be called multiple times safely; only the
  first call wins. Use :meth:`reset_for_testing` to break that invariant
  inside a test fixture.

  Attributes:
    _configured: Set once the global structlog config has been applied.
    _lock: Guards the configure / reset critical sections.
    _config: The :class:`LoggerConfig` the global was configured with.
      ``None`` before the first configure call.
    _is_time_utc: Emit timestamps in UTC (the only sane choice for a
      multi-region service).
    _time_format: ISO-8601 format string passed to structlog's ``TimeStamper``.
  """

  _configured: bool = False
  _lock: threading.Lock = threading.Lock()
  _config: LoggerConfig | None = None
  _is_time_utc: bool = True
  _time_format: str = "iso"

  @classmethod
  def configure(cls, config: LoggerConfig) -> None:
    """Apply the structlog + stdlib bridge configuration for this process.

    Idempotent. The first call installs the processor chain, picks the
    renderer, and replaces the root stdlib logger's handlers so third-party
    library logs flow through the same pipeline. Subsequent calls return
    immediately without touching global state.

    Args:
      config: Frozen :class:`LoggerConfig` describing service identity,
        environment, level, and provider-specific behaviour.
    """
    with cls._lock:
      if cls._configured:
        return

      processors = cls._build_processor_chain(config)
      cls._apply_structlog_config(processors, config)
      cls._configure_stdlib_bridge(config)

      cls._config = config
      cls._configured = True

  @classmethod
  def get_logger(cls, name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog bound logger, auto-configuring with a fallback if needed.

    The fallback config (service name ``"unconfigured"``) exists so library
    code can call ``get_logger`` at import time without crashing in a misuse
    scenario. Application code should call :meth:`configure` explicitly at
    startup.

    Args:
      name: Logger name, typically ``__name__``. ``None`` returns the root
        bound logger.

    Returns:
      A structlog :class:`~structlog.stdlib.BoundLogger`.
    """
    if not cls._configured:
      cls.configure(LoggerConfig(service_name="unconfigured"))
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))

  @classmethod
  def reset_for_testing(cls) -> None:
    """Tear down all global logger state so tests can reconfigure.

    Clears the configured-flag, drops the cached config, resets structlog's
    defaults, and removes every handler from the root stdlib logger.
    Forgetting that last step caused duplicate handlers (and duplicate
    output) across test runs, which is why the reset is comprehensive rather
    than minimal.
    """
    with cls._lock:
      cls._configured = False
      cls._config = None
      structlog.reset_defaults()
      root = logging.getLogger()
      for handler in list(root.handlers):
        root.removeHandler(handler)

  @classmethod
  def _build_processor_chain(cls, config: LoggerConfig) -> list[Processor]:
    """Compose the ordered processor chain used by both structlog and stdlib.

    Order matters:
      1. ``merge_contextvars`` first so request-scoped fields (set by
         ``bind_contextvars``) are visible to everything downstream.
      2. ``add_log_level`` + ``TimeStamper`` so subsequent processors and
         the renderer see the canonical ``level`` / ``timestamp`` fields.
      3. :class:`ServiceContextProcessor` to stamp service identity.
      4. :class:`OpenTelemetryProcessor` to inject trace correlation when a
         span is active.
      5. ``format_exc_info`` + ``StackInfoRenderer`` to render any
         ``exc_info=`` / ``stack_info=`` kwargs into strings — without these,
         exceptions are silently dropped from JSON output.
      6. :class:`GCPFormatterProcessor` (provider-gated) renames the
         canonical fields into the GCP Cloud Logging shape — must run after
         the OTel processor so ``trace_id`` is present to rewrite.
      7. Dev-only callsite info (filename/func/lineno) — too expensive to
         enable in production, and skipped outright when the console filter
         would only discard it again.
      8. :class:`ConsoleFieldFilterProcessor` (config-gated) strips
         process-constant noise from console output — must run last so it
         sees every field the processors above added.
      9. The renderer (JSON or console).

    Args:
      config: Active logger configuration.

    Returns:
      Ordered list of processors ready to hand to ``structlog.configure``.
    """
    chain: list[Processor] = [
      structlog.contextvars.merge_contextvars,
      structlog.processors.add_log_level,
      structlog.processors.TimeStamper(fmt=cls._time_format, utc=cls._is_time_utc),
      ServiceContextProcessor(
        service_name=config.service_name,
        service_version=config.service_version,
        environment=config.environment,
        static_fields=config.static_fields,
      ),
    ]

    if config.enable_otel:
      chain.append(OpenTelemetryProcessor())

    chain.extend(
      [
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
      ]
    )

    if config.provider == Provider.GCP:
      chain.append(GCPFormatterProcessor())

    # CallsiteParameterAdder walks the stack on every record, so skip it
    # entirely when the console filter downstream would just discard the
    # result — paying for stack introspection to then throw it away is the
    # worst of both worlds.
    if not config.is_production and not config.hide_console_fields:
      chain.append(
        structlog.processors.CallsiteParameterAdder(
          parameters=[
            structlog.processors.CallsiteParameter.FILENAME,
            structlog.processors.CallsiteParameter.FUNC_NAME,
            structlog.processors.CallsiteParameter.LINENO,
          ],
        )
      )

    if config.hide_console_fields:
      chain.append(ConsoleFieldFilterProcessor())

    chain.append(cls._build_renderer(config))
    return chain

  @classmethod
  def _build_renderer(cls, config: LoggerConfig, *, str_output: bool = False) -> Any:
    """Pick the terminal renderer for the processor chain.

    Args:
      config: Active logger configuration.
      str_output: When ``True``, force a renderer that returns ``str``
        (required by :class:`structlog.stdlib.ProcessorFormatter`, which
        rejects bytes). When ``False``, prefer the faster bytes-emitting
        ``orjson`` JSON renderer.

    Returns:
      A structlog renderer callable.
    """
    if config.use_json_renderer:
      if not str_output:
        try:
          import orjson

          return structlog.processors.JSONRenderer(serializer=orjson.dumps)
        except ImportError:
          pass
      return structlog.processors.JSONRenderer()

    return structlog.dev.ConsoleRenderer(colors=True)

  @classmethod
  def _apply_structlog_config(
    cls,
    processors: list[Processor],
    config: LoggerConfig,
  ) -> None:
    """Wire the chain into structlog's process-global config.

    Args:
      processors: Processor chain built by :meth:`_build_processor_chain`.
      config: Active logger configuration. Used to pick the logger factory:
        a :class:`structlog.BytesLoggerFactory` when orjson + JSON output is
        active (the JSON renderer emits bytes), otherwise a
        :class:`structlog.WriteLoggerFactory` over stderr.
    """
    use_bytes_factory = False
    if config.use_json_renderer:
      try:
        import orjson  # noqa: F401

        use_bytes_factory = True
      except ImportError:
        pass

    logger_factory: Any
    if use_bytes_factory:
      logger_factory = structlog.BytesLoggerFactory()
    else:
      logger_factory = structlog.WriteLoggerFactory(file=sys.stderr)

    structlog.configure(
      processors=processors,
      wrapper_class=structlog.make_filtering_bound_logger(config.log_level),
      logger_factory=logger_factory,
      cache_logger_on_first_use=True,
    )

  @classmethod
  def _configure_stdlib_bridge(cls, config: LoggerConfig) -> None:
    """Route third-party stdlib :mod:`logging` records through structlog.

    Without this bridge, libraries that emit via ``logging.getLogger(...)``
    (google-cloud-*, sqlalchemy, urllib3, …) would bypass the structlog
    chain entirely — losing JSON formatting, service identity, and trace
    correlation. The fix is :class:`structlog.stdlib.ProcessorFormatter`:
    its ``foreign_pre_chain`` runs structlog processors over the foreign
    ``LogRecord``'s fields before the shared renderer turns the dict into
    output. The ``processor`` arg is the same renderer the native chain
    ends with, so both code paths produce identical wire format.

    The bridge always writes to ``sys.stderr`` so application stdout stays
    clean for tools that parse it.

    Args:
      config: Active logger configuration. Drives renderer choice and the
        level threshold installed on the handler and root logger.
    """
    foreign_pre_chain: list[Processor] = [
      structlog.processors.add_log_level,
      structlog.processors.TimeStamper(fmt=cls._time_format, utc=cls._is_time_utc),
      ServiceContextProcessor(
        service_name=config.service_name,
        service_version=config.service_version,
        environment=config.environment,
        static_fields=config.static_fields,
      ),
    ]
    if config.enable_otel:
      foreign_pre_chain.append(OpenTelemetryProcessor())
    foreign_pre_chain.extend(
      [
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
      ]
    )
    if config.provider == Provider.GCP:
      foreign_pre_chain.append(GCPFormatterProcessor())
    if config.hide_console_fields:
      foreign_pre_chain.append(ConsoleFieldFilterProcessor())

    formatter = structlog.stdlib.ProcessorFormatter(
      processor=cls._build_renderer(config, str_output=True),
      foreign_pre_chain=foreign_pre_chain,
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(config.log_level)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in list(root.handlers):
      root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(config.log_level)

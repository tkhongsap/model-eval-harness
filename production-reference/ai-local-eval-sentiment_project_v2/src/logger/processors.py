from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import MutableMapping
from typing import Any

from src.logger.config import Environment

EventDict = MutableMapping[str, Any]


class LoggerProcessor(ABC):
  """Abstract base for structlog processors.

  Subclasses implement ``processor()``. ``__call__`` adapts the structlog
  protocol so subclasses don't have to remember the
  ``(logger, method_name, event_dict)`` signature on every override.
  """

  def __call__(
    self,
    logger: Any,
    method_name: str,
    event_dict: EventDict,
  ) -> EventDict:
    """Adapt the structlog protocol to ``self.processor()``.

    Args:
        logger: The structlog bound logger producing the record.
        method_name: The log level method invoked (e.g. ``"info"``, ``"error"``).
        event_dict: Mutable mapping of fields for the current log record.

    Returns:
        The transformed event dict, ready for the next processor in the chain.
    """
    return self.processor(logger, method_name, event_dict)

  @abstractmethod
  def processor(
    self,
    logger: Any,
    method_name: str,
    event_dict: EventDict,
  ) -> EventDict:
    """Transform and return the event dict.

    Args:
        logger: The structlog bound logger producing the record.
        method_name: The log level method invoked (e.g. ``"info"``, ``"error"``).
        event_dict: Mutable mapping of fields for the current log record.

    Returns:
        The same (or a new) event dict to pass to the next processor.

    Raises:
        Implementations must not raise on bad input; degrade gracefully so
        a malformed log record never crashes the calling code.
    """


class ServiceContextProcessor(LoggerProcessor):
  """Stamp every log record with service identity.

  Adds ``service.name``, ``service.version``, ``deployment.environment``
  (OpenTelemetry semantic convention names) plus any static fields configured
  for this deployment (e.g. k8s pod name, region). These never change for the
  lifetime of the process so it's safe to bind them once at startup.

  The merge uses ``dict.setdefault`` so caller-supplied fields on a specific
  log call are never overwritten by the static service context.

  Attributes:
      _fields: Pre-computed dict of service-context fields merged into every
          event. Built once in ``__init__`` because the values are static for
          the process lifetime.
  """

  def __init__(
    self,
    *,
    service_name: str,
    service_version: str,
    environment: Environment | str,
    static_fields: dict[str, str] | None = None,
  ) -> None:
    """Pre-compute the service-context field dict.

    Args:
        service_name: Value for the ``service.name`` OTel semantic field.
        service_version: Value for the ``service.version`` OTel semantic field.
        environment: Value for the ``deployment.environment`` OTel semantic field.
            Accepts the :class:`Environment` enum or a raw string; the value
            is normalised to a string so JSON output is stable regardless of
            input.
        static_fields: Optional extra fields to stamp on every record (e.g.
            ``{"k8s.pod.name": "...", "cloud.region": "..."}``). ``None`` is
            treated as an empty dict.
    """
    env_value = environment.value if isinstance(environment, Environment) else environment
    self._fields: dict[str, Any] = {
      "service.name": service_name,
      "service.version": service_version,
      "deployment.environment": env_value,
      **(static_fields or {}),
    }

  def processor(self, logger: Any, method_name: str, event_dict: EventDict) -> EventDict:  # noqa: ARG002
    """Merge pre-computed service fields without overwriting caller values.

    Args:
        logger: The structlog bound logger producing the record (unused).
        method_name: The log level method invoked (unused).
        event_dict: Mutable mapping of fields for the current log record.

    Returns:
        ``event_dict`` enriched with any service-context fields it did not
        already define.
    """
    for key, value in self._fields.items():
      event_dict.setdefault(key, value)
    return event_dict


class GCPFormatterProcessor(LoggerProcessor):
  """Rewrite structlog field names into GCP Cloud Logging's expected shape.

  Cloud Logging's structured-log ingestion treats specific top-level JSON
  keys as first-class fields (severity, message, time, trace, spanId) and
  falls back to a generic ``jsonPayload`` blob for everything else. This
  processor renames structlog's defaults so those keys are populated, which
  in turn unlocks log filtering by severity, log-to-trace correlation in
  Cloud Trace, and the right rendering in the Logs Explorer.

  Mappings applied:
      - ``level`` → ``severity`` (uppercased)
      - ``timestamp`` → ``time``
      - ``event`` → ``message``
      - ``trace_id`` → ``logging.googleapis.com/trace`` (prefixed with
        ``projects/<id>/traces/`` when ``GCP_PROJECT_ID`` is set so Cloud
        Trace can resolve the link)
      - ``span_id`` → ``logging.googleapis.com/spanId``

  The rewrites are unconditional (no try/except) — earlier processors are
  expected to have populated the source fields. If a field is missing the
  rewrite is skipped silently, which is what we want for partial records.
  """

  def processor(self, logger: Any, method_name: str, event_dict: EventDict) -> EventDict:  # noqa: ARG002
    """Apply GCP Cloud Logging field renames to ``event_dict``.

    Args:
        logger: The structlog bound logger producing the record (unused).
        method_name: The log level method invoked (unused).
        event_dict: Mutable mapping of fields for the current log record.

    Returns:
        ``event_dict`` with GCP-friendly field names. Returned in-place.
    """
    if "level" in event_dict:
      event_dict["severity"] = event_dict.pop("level").upper()
    if "timestamp" in event_dict:
      event_dict["time"] = event_dict.pop("timestamp")
    if "event" in event_dict:
      event_dict["message"] = event_dict.pop("event")
    if "trace_id" in event_dict:
      project_id = os.environ.get("GCP_PROJECT_ID", "")
      trace_id = event_dict.pop("trace_id")
      event_dict["logging.googleapis.com/trace"] = (
        f"projects/{project_id}/traces/{trace_id}" if project_id else trace_id
      )
    if "span_id" in event_dict:
      event_dict["logging.googleapis.com/spanId"] = event_dict.pop("span_id")
    return event_dict


class OpenTelemetryProcessor(LoggerProcessor):
  """Inject the current OpenTelemetry trace and span IDs into every log.

  Reads the active span via ``opentelemetry.trace.get_current_span()`` and
  emits W3C-formatted hex IDs (32 chars for ``trace_id``, 16 chars for
  ``span_id``) plus a ``trace_sampled`` boolean — the format APMs like
  Jaeger, Tempo, and Honeycomb expect for log-to-trace correlation.

  The processor is a silent no-op in three cases:

    1. The ``opentelemetry`` package isn't installed.
    2. No span is currently active on the context.
    3. The active span context is invalid (INVALID_SPAN sentinel or tracing
       disabled).

  It never raises. This means it can be configured unconditionally in the
  processor chain and you only pay for trace correlation when tracing is
  actually wired up.

  Attributes:
      _TRACE_MODULE: Class-level cache of the imported ``opentelemetry.trace``
          module, or ``None`` if the import failed. Shared across all
          instances.
      _import_attempted: Class-level sentinel ensuring the lazy import is
          attempted exactly once per process.
  """

  _TRACE_MODULE: Any = None
  _import_attempted: bool = False

  def __init__(self) -> None:
    """Attempt the one-shot lazy import of ``opentelemetry.trace``.

    The import is guarded by the class-level ``_import_attempted`` flag so
    it runs at most once per process across all instances. If
    ``opentelemetry`` isn't installed, the failure is cached as
    ``_TRACE_MODULE = None`` and every subsequent call becomes a cheap no-op.
    """
    if not OpenTelemetryProcessor._import_attempted:
      OpenTelemetryProcessor._import_attempted = True
      try:
        from opentelemetry import trace as _trace

        OpenTelemetryProcessor._TRACE_MODULE = _trace
      except ImportError:
        OpenTelemetryProcessor._TRACE_MODULE = None

  def processor(self, logger: Any, method_name: str, event_dict: EventDict) -> EventDict:  # noqa: ARG002
    """Inject trace/span fields when an active, valid span exists.

    Args:
        logger: The structlog bound logger producing the record (unused).
        method_name: The log level method invoked (unused).
        event_dict: Mutable mapping of fields for the current log record.

    Returns:
        ``event_dict`` with ``trace_id``, ``span_id``, and
        ``trace_sampled`` added when an active, valid span is present.
        Returned unchanged when OpenTelemetry isn't installed, no span is
        active, or the span context is invalid.
    """
    trace_mod = OpenTelemetryProcessor._TRACE_MODULE
    if trace_mod is None:
      return event_dict

    span = trace_mod.get_current_span()
    if span is None:
      return event_dict

    ctx = span.get_span_context()
    if not ctx.is_valid:
      return event_dict

    event_dict["trace_id"] = format(ctx.trace_id, "032x")
    event_dict["span_id"] = format(ctx.span_id, "016x")
    event_dict["trace_sampled"] = bool(ctx.trace_flags.sampled)
    return event_dict


class ConsoleFieldFilterProcessor(LoggerProcessor):
  """Drop process-constant fields before the dev console renderer.

  ``ConsoleRenderer`` prints every remaining key after the message. Service
  identity and callsite fields never vary within a run, so on a developer's
  terminal they are pure noise that pushes the actual message off-screen.

  These fields are deliberately *not* dropped from JSON output — log backends
  index and filter on ``service.name`` / ``deployment.environment``, so
  removing them there would break dashboards. That scope decision lives in
  :attr:`~src.logger.config.LoggerConfig.hide_console_fields`, which is what
  gates this processor into the chain.

  Attributes:
      HIDDEN_FIELDS: Curated set of keys removed when the filter is active.
  """

  HIDDEN_FIELDS: frozenset[str] = frozenset(
    {
      "service.name",
      "service.version",
      "deployment.environment",
      "filename",
      "func_name",
      "lineno",
    }
  )

  def processor(self, logger: Any, method_name: str, event_dict: EventDict) -> EventDict:  # noqa: ARG002
    """Remove the hidden fields from ``event_dict``.

    Args:
        logger: The structlog bound logger producing the record (unused).
        method_name: The log level method invoked (unused).
        event_dict: Mutable mapping of fields for the current log record.

    Returns:
        ``event_dict`` without any of :attr:`HIDDEN_FIELDS`. Missing keys are
        ignored rather than raising, so a partial record renders fine.
    """
    for key in self.HIDDEN_FIELDS:
      event_dict.pop(key, None)
    return event_dict

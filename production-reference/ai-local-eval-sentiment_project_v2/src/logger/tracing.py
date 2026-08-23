from __future__ import annotations

import time
from contextlib import AbstractContextManager
from types import TracebackType
from typing import Any, Literal

import structlog

from src.logger.logger import Logger

class TracedOperation(AbstractContextManager["TracedOperation"]):
  """Open a span and bind logging context around a block of work.

  Usage::

      with TracedOperation("checkout", order_id=order.id, user_id=user.id):
          # every log here has trace_id, span_id, order_id, user_id bound
          charge_card()
          ship_order()

  Exceptions inside the block are recorded on the span and re-raised. The
  bound contextvars are always cleared on exit.
  """

  def __init__(
    self,
    operation_name: str,
    *,
    tracer_name: str = __name__,
    attributes: dict[str, Any] | None = None,
    **context_kwargs: Any,
  ) -> None:
    """Capture span/operation metadata without touching any global state.

    The logger is intentionally not resolved here — doing so would
    trigger :meth:`Logger.get_logger`'s auto-configure fallback the
    moment a caller constructed a ``TracedOperation`` before
    :meth:`Logger.configure` had run, locking the global into the
    ``"unconfigured"`` service name. The logger is resolved on first
    use inside :meth:`__enter__` instead.

    Args:
        operation_name: Human-readable name for the span and the log
            record (e.g. ``"checkout"``, ``"db.query.orders"``).
        tracer_name: OTel tracer name. Defaults to this module so spans
            are attributed correctly when no override is supplied.
        attributes: Extra span attributes that should NOT also bleed
            into the bound logging context.
        **context_kwargs: Fields that become both span attributes and
            bound contextvars for the duration of the block.
    """
    self._operation_name = operation_name
    self._tracer_name = tracer_name
    self._attributes = attributes or {}
    self._context_kwargs = context_kwargs
    self._span_cm: Any = None
    self._span: Any = None
    self._token: Any = None
    self._start_time: float = 0.0
    self._logger: Any = None

  def __enter__(self) -> TracedOperation:
    self._start_time = time.perf_counter()
    # Resolve the logger lazily so __init__ never triggers the
    # auto-configure fallback in Logger.get_logger.
    self._logger = Logger.get_logger(self._tracer_name)

    # Open the span (if OTel is available) — falls back gracefully.
    try:
      from opentelemetry import trace

      tracer = trace.get_tracer(self._tracer_name)
      self._span_cm = tracer.start_as_current_span(self._operation_name)
      self._span = self._span_cm.__enter__()
      for key, value in {**self._attributes, **self._context_kwargs}.items():
        # OTel attributes must be primitives; stringify anything else.
        self._span.set_attribute(key, _stringify_attr(value))
    except ImportError:
      self._span_cm = None
      self._span = None

    # Bind the user-supplied kwargs into structlog's contextvars so every
    # log inside the block carries them automatically. The OTel
    # processor will add trace_id/span_id separately.
    self._token = structlog.contextvars.bind_contextvars(
      operation=self._operation_name,
      **self._context_kwargs,
    )

    self._logger.debug("operation.started", operation=self._operation_name)
    return self

  def __exit__(
    self,
    exc_type: type[BaseException] | None,
    exc_val: BaseException | None,
    exc_tb: TracebackType | None,
  ) -> Literal[False]:
    duration_ms = (time.perf_counter() - self._start_time) * 1000.0

    if exc_val is not None:
      self._logger.error(
        "operation.failed",
        operation=self._operation_name,
        duration_ms=round(duration_ms, 3),
        error_type=exc_type.__name__ if exc_type else "Unknown",
        exc_info=(exc_type, exc_val, exc_tb),
      )
      if self._span is not None:
        self._span.record_exception(exc_val)
        try:
          from opentelemetry.trace import Status, StatusCode

          self._span.set_status(Status(StatusCode.ERROR, str(exc_val)))
        except ImportError:
          pass
    else:
      self._logger.info(
        "operation.completed",
        operation=self._operation_name,
        duration_ms=round(duration_ms, 3),
      )

    # Close the span first so its end-time is accurate.
    if self._span_cm is not None:
      self._span_cm.__exit__(exc_type, exc_val, exc_tb)

    # Then clear logging context. Do this even if the span close raised
    # — leaked contextvars cause cross-request data leaks.
    structlog.contextvars.unbind_contextvars(
      "operation",
      *self._context_kwargs.keys(),
    )

    # Return False so exceptions propagate normally.
    return False

  @property
  def span(self) -> Any:
    """The active OTel span, or None if OTel isn't installed.

    Use this to set attributes or events on the span from inside the
    ``with`` block::

        with TracedOperation("query") as op:
            rows = db.execute(sql)
            if op.span is not None:
                op.span.set_attribute("db.rows_returned", len(rows))
    """
    return self._span


def _stringify_attr(value: Any) -> Any:
  """OTel only accepts str/bool/int/float and sequences thereof."""
  if isinstance(value, (str, bool, int, float)):
    return value
  if isinstance(value, (list, tuple)) and all(isinstance(v, (str, bool, int, float)) for v in value):
    return list(value)
  return str(value)

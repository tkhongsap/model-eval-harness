"""Configuration model for the structlog-based logger.

This module defines :class:`LoggerConfig` and the supporting :class:`Environment`
and :class:`Provider` enums. The config is intentionally an immutable
``@dataclass(frozen=True, slots=True)`` so it can be safely shared across
threads after :meth:`Logger.configure` has been called once.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from enum import StrEnum

from src.utils.common import (
  get_env,
  get_env_bool,
)


class Environment(StrEnum):
  """Deployment environment names used to gate behaviour.

  Members:
    DEVELOPMENT: Shared, non-production dev environment.
    STAGING: Pre-prod environment.
    PRODUCTION: Customer-facing production.
    LOCAL: Developer laptop / container. Triggers the pretty console renderer
      unless ``LoggerConfig.force_json`` is set.
  """

  DEVELOPMENT = "development"
  STAGING = "staging"
  PRODUCTION = "production"
  LOCAL = "local"


class Provider(StrEnum):
  """Cloud-platform identifier for provider-specific log field rewriting.

  Used by the processor chain to swap structlog field names into a shape the
  target log backend ingests cleanly (e.g. GCP Cloud Logging expects
  ``severity`` / ``message`` rather than ``level`` / ``event``).

  Members:
    ON_PREM: No provider-specific rewrite is applied.
    AWS: Reserved (no rewrite yet — added so the enum is stable).
    GCP: Enables :class:`GCPFormatterProcessor` in the chain.
    AZURE: Reserved (no rewrite yet).
  """

  ON_PREM = "on_prem"
  AWS = "aws"
  GCP = "gcp"
  AZURE = "azure"


@dataclass(frozen=True, slots=True)
class LoggerConfig:
  """Immutable configuration consumed by :class:`Logger.configure`.

  Frozen + slotted so the same instance can be passed around without callers
  worrying about mutation, and so attribute access is fast on the hot path.

  Attributes:
    service_name: Value emitted as the OTel ``service.name`` field on every
      record. Required — there is no useful default for a multi-service
      platform.
    service_version: Value for ``service.version`` (semver or git sha).
      ``"unknown"`` is a sentinel for "not wired up yet".
    environment: Deployment environment. Drives the JSON-vs-console renderer
      choice and the dev-only callsite processor.
    provider: Cloud provider. Drives provider-specific field renaming
      (currently only GCP).
    log_level: Standard ``logging`` integer level. Filters at the wrapper
      class level so cheaper than filtering at the renderer.
    force_json: Emit JSON even when stderr is a TTY. Useful when piping dev
      logs to a file or running inside a container whose ``isatty()`` lies.
    enable_otel: Inject ``trace_id`` / ``span_id`` from the active OTel span.
      Safe to leave on with no tracer configured — the processor degrades to
      a no-op.
    console_compact: Hide process-constant fields (service identity and
      callsite) from the pretty console renderer so the message stays
      readable. Inert whenever output is JSON — see
      :attr:`hide_console_fields`.
    static_fields: Extra fields stamped on every record. Typical use is
      Kubernetes pod name, cloud region, deployment colour — things that are
      constant for the process lifetime.
  """

  service_name: str
  service_version: str = "unknown"
  environment: Environment = Environment.PRODUCTION
  provider: Provider = Provider.ON_PREM
  log_level: int = logging.INFO

  force_json: bool = False
  enable_otel: bool = True
  console_compact: bool = False

  static_fields: dict[str, str] = field(default_factory=dict)

  @property
  def is_local(self) -> bool:
    """Return ``True`` when the deployment environment is ``LOCAL``."""
    return self.environment == Environment.LOCAL

  @property
  def is_production(self) -> bool:
    """Return ``True`` when the deployment environment is ``PRODUCTION``.

    Used to gate dev-only processors (e.g. callsite info) so prod logs stay
    cheap and clean.
    """
    return self.environment == Environment.PRODUCTION

  @property
  def use_json_renderer(self) -> bool:
    """Decide whether the structlog chain should end in a JSON renderer.

    JSON is used in every non-local environment, and in ``LOCAL`` when
    ``force_json`` is set or stderr is not a TTY (e.g. piped to a file).
    Otherwise the pretty :class:`structlog.dev.ConsoleRenderer` is used.

    Returns:
      ``True`` to render as JSON, ``False`` to use the dev console renderer.
    """
    if self.force_json or not self.is_local:
      return True
    return not sys.stderr.isatty()

  @property
  def hide_console_fields(self) -> bool:
    """Decide whether the console field filter belongs in the processor chain.

    Deliberately gated on the console renderer being active: JSON consumers
    need the complete record (backends index on ``service.name`` and
    ``deployment.environment``), so ``console_compact`` is a no-op whenever
    :attr:`use_json_renderer` is ``True``. Keeping that rule here rather than
    at each call site means the two processor chains cannot disagree about it.

    Returns:
      ``True`` to strip the hidden fields, ``False`` to emit the full record.
    """
    return self.console_compact and not self.use_json_renderer

  @classmethod
  def from_env(cls, service_name: str | None = None) -> LoggerConfig:
    """Build a :class:`LoggerConfig` from process environment variables.

    Honours the standard env vars so the same container image runs everywhere
    without code changes:

      - ``ENVIRONMENT`` / ``ENV``  — one of ``development``, ``staging``,
        ``production``, ``local``. Unknown values fall back to
        :attr:`Environment.PRODUCTION` rather than raising — a misconfigured
        env var should not crash service startup.
      - ``PROVIDER``               — one of ``on_prem``, ``aws``, ``gcp``,
        ``azure``. Unknown values fall back to :attr:`Provider.ON_PREM`.
      - ``SERVICE_NAME``           — overrides the ``service_name`` argument
        if set.
      - ``SERVICE_VERSION``        — semver or git sha. Defaults to
        ``"unknown"``.
      - ``LOG_LEVEL``              — standard name (``DEBUG`` / ``INFO`` /
        ``WARNING`` / ``ERROR`` / ``CRITICAL``). Unknown values fall back to
        ``INFO``.
      - ``LOG_FORCE_JSON``         — ``1`` / ``true`` / ``yes`` to force JSON
        output even on a TTY.
      - ``LOG_ENABLE_OTEL``        — ``0`` / ``false`` / ``no`` to disable
        OTel trace-id injection. On by default.
      - ``LOG_CONSOLE_COMPACT``    — ``1`` / ``true`` / ``yes`` to hide
        service-identity and callsite fields from the console renderer. Off
        by default; ignored when output is JSON.

    Args:
      service_name: Default service name used when ``SERVICE_NAME`` is unset.

    Returns:
      A fully-populated, frozen :class:`LoggerConfig`.

    Raises:
      ValueError: If neither the ``service_name`` argument nor the
        ``SERVICE_NAME`` env var is provided.
    """
    env_name = (get_env("ENVIRONMENT", None) or get_env("ENV", None) or "production").lower()
    provider_name = (get_env("PROVIDER", None) or "on_prem").lower()
    try:
      env = Environment(env_name)
    except ValueError:
      env = Environment.PRODUCTION

    try:
      provider = Provider(provider_name)
    except ValueError:
      provider = Provider.ON_PREM

    # get_env is typed as str | None; coalesce explicitly so mypy sees the str
    # branch on the .upper() / constructor call below.
    level_name = (get_env("LOG_LEVEL", "INFO") or "INFO").upper()
    level = logging.getLevelNamesMapping().get(level_name, logging.INFO)

    resolved_service = get_env("SERVICE_NAME", service_name)
    if not resolved_service:
      raise ValueError(
        "service_name must be provided either as an argument or via the SERVICE_NAME environment variable"
      )

    return cls(
      service_name=resolved_service,
      service_version=get_env("SERVICE_VERSION", "unknown") or "unknown",
      environment=env,
      provider=provider,
      log_level=level,
      force_json=get_env_bool("LOG_FORCE_JSON", default=False),
      enable_otel=get_env_bool("LOG_ENABLE_OTEL", default=True),
      console_compact=get_env_bool("LOG_CONSOLE_COMPACT", default=False),
    )

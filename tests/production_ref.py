"""Reach True's REAL production scorer, with no GCP credentials and no cloud SDKs.

This is test-only scaffolding. Nothing here ships in the harness.

Why it exists: the differential test is only meaningful if it runs the actual
production code rather than a copy. `fact_checker.py` imports
`from src.main import get_analysis_schema`, and `src/main.py` reads 38 environment
variables at module scope and calls `google.auth.default()`. So we supply stub env
vars, auto-stub any absent third-party package, and bypass `config_validation` via
`object.__new__`. Verified working: see the probe in the session scratchpad.

A SHA-256 pin over the scored region makes upstream drift fail loudly rather than
silently changing what we are comparing against.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import types
from pathlib import Path

# Set TRUE_SOURCE_ROOT to override; defaults to the vendored archive in this repo.
DEFAULT_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "source-code-review"
    / "sentiment-batch-retention-main"
)

# fact_checker.py lines 685-1105: pre_process through calculate_confusion_matrix.
PINNED_REGION = (685, 1105)
PINNED_SHA256 = "586194f1c689615f503f64ac3d71ce92359d3d19ec20cfcc472755c6f1f36eb4"

_ENV_STR = [
    "ARCHIVE_PREFIX", "BATCH_PREFIX", "CONTROL_SITE_BASE_ROOT", "CONTROL_SITE_CLIENT_ID",
    "CONTROL_SITE_CLIENT_SECRET", "CONTROL_SITE_CONTROL_PATH", "CONTROL_SITE_COST_PATH",
    "CONTROL_SITE_PERFORMANCE_LOG_PATH", "CONTROL_SITE_PROMPTS_ROOT",
    "CONTROL_SITE_SITE_DOMAIN", "CONTROL_SITE_SITE_PATH", "CONTROL_SITE_TENANT_ID",
    "CONTROL_SITE_TRANSACTION_LOG_PATH", "GOOGLE_CLOUD_LOCATION", "GOOGLE_CLOUD_PROJECT",
    "INPUT_PREFIX", "LOG_NAME", "MODEL_NAME", "OUTPUT_EXCEL_PREFIX", "PROCESSING_BUCKET",
    "PROCESSING_PREFIX", "PRODUCT_INPUT", "PRODUCT_OUTPUT", "PROJECT_NAME",
    "SANDBOX_SITE_BASE_ROOT", "SANDBOX_SITE_CLIENT_ID", "SANDBOX_SITE_CLIENT_SECRET",
    "SANDBOX_SITE_SITE_DOMAIN", "SANDBOX_SITE_SITE_PATH", "SANDBOX_SITE_TENANT_ID",
    "VERINT_SITE_BASE_ROOT", "VERINT_SITE_CLIENT_ID", "VERINT_SITE_CLIENT_SECRET",
    "VERINT_SITE_SITE_DOMAIN", "VERINT_SITE_SITE_PATH", "VERINT_SITE_TENANT_ID",
]
_ENV_INT = ["BATCH_SIZE", "MAX_CONCURRENT_UPLOAD", "LOOKBACK_DAYS"]


class _PhMeta(type):
    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _mk(name)


class _Ph(metaclass=_PhMeta):
    def __init__(self, *a, **k): pass
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Ph()
    def __call__(self, *a, **k): return _Ph()
    def __iter__(self): return iter(())


def _mk(name):
    return _PhMeta(name, (_Ph,), {})


class _AnyModule(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _mk(name)


def _stub(name: str):
    m = _AnyModule(name)
    sys.modules[name] = m
    parent, _, child = name.rpartition(".")
    if parent:
        sys.modules.setdefault(parent, _AnyModule(parent))
        setattr(sys.modules[parent], child, m)
    return m


def source_root() -> Path:
    return Path(os.environ.get("TRUE_SOURCE_ROOT", str(DEFAULT_SOURCE)))


def region_sha256() -> str:
    """Hash the scored region so upstream drift fails loudly."""
    path = source_root() / "src" / "modules" / "fact_checker.py"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    lo, hi = PINNED_REGION
    return hashlib.sha256("".join(lines[lo - 1:hi]).encode("utf-8")).hexdigest()


def load_production_factchecker():
    """Import the real FactCheckerModule. Raises if the source tree is absent."""
    root = source_root()
    if not (root / "src" / "modules" / "fact_checker.py").exists():
        raise FileNotFoundError(f"production source not found at {root}")

    for k in _ENV_STR:
        os.environ.setdefault(k, "stub")
    for k in _ENV_INT:
        os.environ.setdefault(k, "1")

    # google.auth needs a real shape: main.py:50-54 unpacks default() and catches
    # a named exception, so a permissive placeholder is not enough here.
    ga = types.ModuleType("google.auth")
    gae = types.ModuleType("google.auth.exceptions")

    class DefaultCredentialsError(Exception):
        pass

    gae.DefaultCredentialsError = DefaultCredentialsError
    ga.exceptions = gae
    ga.default = lambda *a, **k: (None, None)
    g = sys.modules.get("google") or types.ModuleType("google")
    g.auth = ga
    sys.modules["google"] = g
    sys.modules["google.auth"] = ga
    sys.modules["google.auth.exceptions"] = gae

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    stubbed: list[str] = []
    for _ in range(40):
        try:
            from src.modules.fact_checker import FactCheckerModule  # noqa: PLC0415
            return FactCheckerModule, stubbed
        except ModuleNotFoundError as e:
            if e.name is None or e.name.startswith("src"):
                raise
            _stub(e.name)
            stubbed.append(e.name)
        except ImportError as e:
            m = re.search(r"cannot import name '([^']+)' from '([^']+)'", str(e))
            if not m:
                raise
            full = f"{m.group(2)}.{m.group(1)}"
            _stub(full)
            stubbed.append(full)
    raise ImportError("too many stub rounds importing production fact_checker")


def production_scores(gt_rows: list[dict], pred_rows: list[dict]):
    """Run the real calculate_confusion_matrix. Returns (call_result, reason, product)."""
    import pandas as pd

    FactCheckerModule, _ = load_production_factchecker()
    fc = object.__new__(FactCheckerModule)  # bypasses __init__ -> bypasses config_validation
    cols = ["call_id", "phone_number", "call_result", "product", "main", "secondary", "third"]
    gt = pd.DataFrame(gt_rows, columns=cols)
    pred = pd.DataFrame(pred_rows, columns=cols)
    return fc.calculate_confusion_matrix(pred, gt)


if __name__ == "__main__":  # pragma: no cover
    print(f"source root : {source_root()}")
    print(f"region      : lines {PINNED_REGION[0]}-{PINNED_REGION[1]}")
    print(f"sha256      : {region_sha256()}")

import os, io, pathlib, json
from dataclasses import dataclass, replace
from typing import Any
import pandas as pd
from dotenv import load_dotenv
import openpyxl
from src.logger import (
  Logger,
  LoggerConfig,
)
load_dotenv()
Logger.configure(
    LoggerConfig.from_env()
)
from src.hook.sharepoint import SharePointModule
from src.hook.gcp_gcs import GCSModule, GCSError
from src.google_model.sentiment.google_output import run as google_sentiment
from src.google_model.sentiment.google_output import Config as GoogleSentimentOutputConfig

from src.google_model.sentiment_mnp.google_output import run as google_sentiment_mnp
from src.google_model.sentiment_mnp.google_output import Config as GoogleSentimentMnpOutputConfig

from src.google_model.sentiment_retention.google_output import run as google_sentiment_retention
from src.google_model.sentiment_retention.google_output import (
  Config as GoogleSentimentRetentionOutputConfig,
)

from src.google_model.sentiment_telesale.google_output import run as google_sentiment_telesale
from src.google_model.sentiment_telesale.google_output import (
  Config as GoogleSentimentTelesaleOutputConfig,
)

from src.google_model.documents.google_output import run as google_documents
from src.google_model.documents.google_output import Config as GoogleDocumentsOutputConfig

from src.local_model.sentiment.internal_asr_llm_output import run as local_asr_llm_sentiment
from src.local_model.sentiment.internal_asr_llm_output import Config as LocalSentimentOutputConfig

from src.local_model.sentiment.internal_asr_llm_split_output import run as local_asr_llm_split_sentiment
from src.local_model.sentiment.internal_asr_llm_split_output import Config as LocalSentimentSplitOutputConfig

from src.local_model.sentiment.internal_asr_llm_trim_output import run as local_asr_llm_trim_sentiment
from src.local_model.sentiment.internal_asr_llm_trim_output import Config as LocalSentimentTrimOutputConfig

from src.local_model.documents.internal_direct_output import run as local_documents
from src.local_model.documents.internal_direct_output import Config as LocalDocumentsOutputConfig

from src.google_model.sentiment.google_confusion_matrix  import run as google_sentiment_confusion_matrix
from src.google_model.sentiment.google_confusion_matrix  import Config as GoogleSentimentConfusionMatrixConfig

from src.google_model.sentiment.google_exact_match import run as google_sentiment_exact_match
from src.google_model.sentiment.google_exact_match import Config as GoogleSentimentExactMatchConfig

from src.google_model.sentiment_mnp.google_confusion_matrix import run as google_sentiment_mnp_confusion_matrix
from src.google_model.sentiment_mnp.google_confusion_matrix import Config as GoogleSentimentMnpConfusionMatrixConfig

from src.google_model.sentiment_retention.google_confusion_matrix import run as google_sentiment_retention_confusion_matrix
from src.google_model.sentiment_retention.google_confusion_matrix import Config as GoogleSentimentRetentionConfusionMatrixConfig

from src.google_model.sentiment_telesale.google_confusion_matrix import run as google_sentiment_telesale_confusion_matrix
from src.google_model.sentiment_telesale.google_confusion_matrix import Config as GoogleSentimentTelesaleConfusionMatrixConfig

from src.google_model.documents.google_confusion_matrix import Config as GoogleDocumentsConfusionMatrixConfig
from src.google_model.documents.google_confusion_matrix import run as google_documents_confusion_matrix

from src.google_model.documents.google_exact_match import Config as GoogleDocumentsExactMatchConfig
from src.google_model.documents.google_exact_match import run as google_documents_exact_match

from src.local_model.sentiment.internal_confusion_matrix import run as local_sentiment_confusion_matrix
from src.local_model.sentiment.internal_confusion_matrix import Config as LocalSentimentConfusionMatrixConfig

from src.local_model.sentiment.internal_exact_match import run as local_sentiment_exact_match
from src.local_model.sentiment.internal_exact_match import Config as LocalSentimentExactMatchConfig

from src.local_model.documents.internal_confusion_matrix import Config as LocalDocumentsConfusionMatrixConfig
from src.local_model.documents.internal_confusion_matrix import run as local_documents_confusion_matrix

from src.local_model.sentiment_mnp.internal_asr_llm_output import run as local_sentiment_mnp
from src.local_model.sentiment_mnp.internal_asr_llm_output import (
  Config as LocalSentimentMnpOutputConfig,
)

from src.local_model.sentiment_mnp.internal_confusion_matrix import run as local_sentiment_mnp_confusion_matrix
from src.local_model.sentiment_mnp.internal_confusion_matrix import (
  Config as LocalSentimentMnpConfusionMatrixConfig,
)

from src.local_model.sentiment_retention.internal_asr_llm_output import run as local_sentiment_retention
from src.local_model.sentiment_retention.internal_asr_llm_output import (
  Config as LocalSentimentRetentionOutputConfig,
)

from src.local_model.sentiment_retention.internal_confusion_matrix import run as local_sentiment_retention_confusion_matrix
from src.local_model.sentiment_retention.internal_confusion_matrix import (
  Config as LocalSentimentRetentionConfusionMatrixConfig,
)

from src.local_model.sentiment_telesale.internal_asr_llm_split_output import run as local_sentiment_telesale
from src.local_model.sentiment_telesale.internal_asr_llm_split_output import (
  Config as LocalSentimentTelesaleOutputConfig,
)

from src.local_model.sentiment_telesale.internal_confusion_matrix import run as local_sentiment_telesale_confusion_matrix
from src.local_model.sentiment_telesale.internal_confusion_matrix import (
  Config as LocalSentimentTelesaleConfusionMatrixConfig,
)

from src.local_model.documents.internal_exact_match import Config as LocalDocumentsExactMatchConfig
from src.local_model.documents.internal_exact_match import run as local_documents_exact_match

logger = Logger.get_logger(__name__)

# How many run folders the selector lists. The count it hides is always reported, because a
# silent truncation reads as "these are all the runs there are".
RUN_LIST_LIMIT = 10

# The resume picker lists fewer than the metrics picker on purpose: the run you resume is nearly
# always the one you just interrupted, while the run you score is often an older completed one.
RESUME_LIST_LIMIT = 5

# Distinguishes "this Config has no run_id field" from "run_id is None", which is itself a
# meaningful value -- start a fresh run.
_ABSENT = object()

# One row of the menu and the call it dispatches, deliberately the same object. The menu used
# to be a MENU tuple plus a parallel 20-branch if/elif on the option string; renumbering the
# two by hand is how an entry ends up labelled one thing and running another, so they are one
# table now and the numbering is derived from it rather than written down twice.
@dataclass(frozen=True)
class Action:
    """A menu entry.

    ``kind`` selects one of the three dispatch shapes the pipelines already have. It is a
    string rather than a callable because the three differ in *what the operator is asked*,
    not just in how the run is called, and that belongs next to the label:

    - ``output_google``: the Google pipelines have no resume path and build their own Config
      inside ``run()``, so the previewed instance is only a preview and ``run()`` takes no
      argument.
    - ``output_local``: the local pipelines accept a Config and resume from a ``run_id``, so
      the instance the operator confirms is exactly the one that executes.
    - ``scorer``: no spend gate. Scoring only reads a workbook and appends sheets.
    """

    label: str
    kind: str
    config_cls: type
    run: Any


SECTIONS: tuple[tuple[str, tuple[Action, ...]], ...] = (
    (
        "SENTIMENT -- QA",
        (
            Action("Run              --  Google", "output_google",
                   GoogleSentimentOutputConfig, google_sentiment),
            Action("Run              --  Local ASR + LLM", "output_local",
                   LocalSentimentOutputConfig, local_asr_llm_sentiment),
            Action("Run              --  Local ASR + LLM (Split)", "output_local",
                   LocalSentimentSplitOutputConfig, local_asr_llm_split_sentiment),
            Action("Run              --  Local ASR + LLM (Trim)", "output_local",
                   LocalSentimentTrimOutputConfig, local_asr_llm_trim_sentiment),
            Action("Exact Match      --  Google", "scorer",
                   GoogleSentimentExactMatchConfig, google_sentiment_exact_match),
            Action("Exact Match      --  Local", "scorer",
                   LocalSentimentExactMatchConfig, local_sentiment_exact_match),
            Action("Confusion Matrix --  Google", "scorer",
                   GoogleSentimentConfusionMatrixConfig, google_sentiment_confusion_matrix),
            Action("Confusion Matrix --  Local", "scorer",
                   LocalSentimentConfusionMatrixConfig, local_sentiment_confusion_matrix),
        ),
    ),
    (
        "SENTIMENT -- MNP",
        (
            # Four actions, not six: production runs ONE evaluation for this use case and so do
            # we. The exact-match scorer was deleted -- its agreement-only framing made Precision
            # equal Accuracy and Recall structurally 1.0, and its per-rank columns became
            # meaningless once reasons were scored as a set. See google_confusion_matrix.py.
            Action("Run              --  Google", "output_google",
                   GoogleSentimentMnpOutputConfig, google_sentiment_mnp),
            Action("Run              --  Local ASR + LLM", "output_local",
                   LocalSentimentMnpOutputConfig, local_sentiment_mnp),
            Action("Confusion Matrix --  Google", "scorer",
                   GoogleSentimentMnpConfusionMatrixConfig, google_sentiment_mnp_confusion_matrix),
            Action("Confusion Matrix --  Local", "scorer",
                   LocalSentimentMnpConfusionMatrixConfig, local_sentiment_mnp_confusion_matrix),
        ),
    ),
    (
        "SENTIMENT -- RETENTION",
        (
            # Four actions, not six: production runs ONE evaluation for this use case and so do
            # we. The exact-match scorer was deleted -- its agreement-only framing made Precision
            # equal Accuracy and Recall structurally 1.0, and its per-rank columns became
            # meaningless once reasons were scored as a set. See google_confusion_matrix.py.
            Action("Run              --  Google", "output_google",
                   GoogleSentimentRetentionOutputConfig, google_sentiment_retention),
            Action("Run              --  Local ASR + LLM", "output_local",
                   LocalSentimentRetentionOutputConfig, local_sentiment_retention),
            Action("Confusion Matrix --  Google", "scorer",
                   GoogleSentimentRetentionConfusionMatrixConfig,
                   google_sentiment_retention_confusion_matrix),
            Action("Confusion Matrix --  Local", "scorer",
                   LocalSentimentRetentionConfusionMatrixConfig,
                   local_sentiment_retention_confusion_matrix),
        ),
    ),
    (
        "SENTIMENT -- TELESALE",
        (
            # "Local (Split)" rather than "Local ASR + LLM": telesale has only the split
            # pipeline, which fans one file out across four per-topic requests because the
            # production prompt is too long for this endpoint in one piece.
            Action("Run              --  Google", "output_google",
                   GoogleSentimentTelesaleOutputConfig, google_sentiment_telesale),
            Action("Run              --  Local (Split)", "output_local",
                   LocalSentimentTelesaleOutputConfig, local_sentiment_telesale),
            # Two actions, not four: telesale has one evaluation. The exact-match scorer was
            # deleted because its Accuracy was identical to the confusion matrix's on all 39
            # criteria and its other three metrics were structurally degenerate. Its compare
            # sheets and its GT labels / Majority columns moved into the survivor.
            Action("Confusion Matrix --  Google", "scorer",
                   GoogleSentimentTelesaleConfusionMatrixConfig,
                   google_sentiment_telesale_confusion_matrix),
            Action("Confusion Matrix --  Local", "scorer",
                   LocalSentimentTelesaleConfusionMatrixConfig,
                   local_sentiment_telesale_confusion_matrix),
        ),
    ),
    (
        "DOCUMENTS",
        (
            Action("Run              --  Google", "output_google",
                   GoogleDocumentsOutputConfig, google_documents),
            Action("Run              --  Local", "output_local",
                   LocalDocumentsOutputConfig, local_documents),
            Action("Exact Match      --  Google", "scorer",
                   GoogleDocumentsExactMatchConfig, google_documents_exact_match),
            Action("Exact Match      --  Local", "scorer",
                   LocalDocumentsExactMatchConfig, local_documents_exact_match),
            Action("Confusion Matrix --  Google", "scorer",
                   GoogleDocumentsConfusionMatrixConfig, google_documents_confusion_matrix),
            Action("Confusion Matrix --  Local", "scorer",
                   LocalDocumentsConfusionMatrixConfig, local_documents_confusion_matrix),
        ),
    ),
)

# Flat, in section order: what ask_index validates against and what main() indexes. Option
# numbers are this tuple's 1-based positions and nothing else, so moving an entry between
# sections renumbers the menu and the dispatch together, in one edit.
ACTIONS: tuple[Action, ...] = tuple(a for _, rows in SECTIONS for a in rows)


class Cancelled(Exception):
    """The operator backed out at a prompt -- via 0, Ctrl-C, or EOF.

    Carried as an exception rather than a sentinel return so a cancellation inside a nested
    prompt (picking a run, then answering the spend gate) unwinds to one place instead of
    threading a None back through every caller.
    """


def ask(prompt: str) -> str:
    """Read one trimmed line from stdin.

    Prompts go to stdout via input() itself, deliberately: the logger writes to stderr, so a
    menu rendered through it separates from its own prompt the moment either stream is
    redirected.

    Raises:
        Cancelled: stdin reached EOF, or the operator pressed Ctrl-C.
    """
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise Cancelled from None


def ask_index(prompt: str, count: int) -> int:
    """Read a 1-based index in [1, count], re-prompting until the input is one.

    Reading an index rather than a typed-out identifier is what makes an out-of-range choice
    impossible by construction -- nothing downstream re-checks the selection.

    Args:
        prompt: Shown each attempt.
        count: Highest accepted index.

    Returns:
        The chosen index, 1-based.

    Raises:
        Cancelled: The operator entered 0.
    """
    while True:
        raw = ask(prompt)
        if raw == "0":
            raise Cancelled
        if raw.isdigit() and 1 <= int(raw) <= count:
            return int(raw)
        shown = raw if raw else "(blank)"
        print(f"  ! {shown} is not a choice between 1 and {count}.")


def ask_optional_index(prompt: str, count: int) -> int:
    """Read an index in [0, count], re-prompting until the input is one.

    The sibling of ``ask_index`` for prompts where 0 carries a meaning of its own rather than
    backing out -- here, "start a new run". The way out of this prompt is Ctrl-C or EOF, which
    ``ask`` already turns into ``Cancelled``.

    Args:
        prompt: Shown each attempt.
        count: Highest accepted index.

    Returns:
        The chosen index; 0 is a valid answer.

    Raises:
        Cancelled: stdin reached EOF, or the operator pressed Ctrl-C.
    """
    while True:
        raw = ask(prompt)
        if raw.isdigit() and 0 <= int(raw) <= count:
            return int(raw)
        shown = raw if raw else "(blank)"
        print(f"  ! {shown} is not a choice between 0 and {count}.")


def select_run_id(config: Any) -> str | None:
    """Prompt for a previous run to resume, or 0 for a fresh one.

    Reads GCS rather than SharePoint deliberately: a run only lands under ``dest_file`` once it
    finishes and uploads its workbook, so the SharePoint listing shows exactly the runs that
    never need resuming. The GCS prefixes hold the per-file checkpoints, which is what the
    pipeline itself reads back when ``config.run_id`` is set.

    Args:
        config: A local output Config *instance*; ``gcs_bucket`` and the GCS prefixes are read
            off it.

    Returns:
        The run id to resume, or None to start a fresh timestamped run.

    Raises:
        Cancelled: The listing failed, or the operator pressed Ctrl-C / stdin reached EOF.
    """
    # No output Config carries a timezone -- run() hardcodes it -- so this literal is what the
    # pipeline will stamp a new run folder with.
    client_gcs = GCSModule(project_id=os.environ["GCP_PROJECT_ID"], timezone="Asia/Bangkok")

    # A run killed before its first result exists only under the payload/processing prefix, so
    # the union is what makes an interrupted run resumable at all. getattr, not attribute
    # access: the documents config has gcs_processing_path and no gcs_payload_path.
    prefixes = [
        path
        for attr in ("gcs_dest_path", "gcs_payload_path", "gcs_processing_path")
        if (path := getattr(config, attr, None))
    ]
    try:
        run_ids = {
            # list_dirs returns full paths with a trailing "/", so the id is the last segment.
            directory.rstrip("/").rsplit("/", 1)[-1]
            for prefix in prefixes
            for directory in client_gcs.list_dirs(bucket_name=config.gcs_bucket, prefix=prefix)
        }
    except GCSError as exc:
        # Nothing has been spent at this point. A listing fault is almost always ADC or bucket
        # config, which would break the run seconds later anyway.
        print(f"Could not list runs in gs://{config.gcs_bucket}: {exc}")
        raise Cancelled from None

    if not run_ids:
        # Not a cancellation: a first-ever run has no prefixes yet and must not be blocked.
        print(f"\nNo previous runs under gs://{config.gcs_bucket} -- starting a new run.")
        logger.info("menu.run_id.selected", bucket=config.gcs_bucket, run_id=None)
        return None

    # Run ids are %Y-%m-%d_%H-%M-%S, which sorts chronologically as a plain string.
    ordered = sorted(run_ids, reverse=True)
    shown = ordered[:RESUME_LIST_LIMIT]
    hidden = len(ordered) - len(shown)

    print(f"\nRuns under gs://{config.gcs_bucket}  ({len(shown)} of {len(ordered)} shown)")
    print("   0. (new run)")
    for idx, run_id in enumerate(shown, start=1):
        print(f"  {idx:>2}. {run_id}")
    if hidden:
        print(f"      ({hidden} older run{'s' if hidden > 1 else ''} not shown)")

    choice = ask_optional_index(
        f"Select a run to resume [0 for a new run, 1-{len(shown)}, Ctrl-C to cancel]: ",
        len(shown),
    )
    selected = None if choice == 0 else shown[choice - 1]
    logger.info("menu.run_id.selected", bucket=config.gcs_bucket, run_id=selected)
    return selected


def select_run_prefix(config: Any) -> str:
    """Prompt for one of the most recent run folders under ``config.dest_file``.

    Args:
        config: A metrics Config *instance*; ``dest_file`` and ``timezone`` are read off it.

    Returns:
        The bare run folder name, e.g. ``2026-08-13_17-20-13``.

    Raises:
        Cancelled: The operator entered 0, or the destination holds no runs.
    """
    client_sb = SharePointModule(
        client_id=os.environ["SANDBOX_CLIENT_ID"],
        client_secret=os.environ["SANDBOX_CLIENT_SECRET"],
        tenant_id=os.environ["SANDBOX_TENANT_ID"],
        site_domain=os.environ["SANDBOX_SITE_DOMAIN"],
        site_path=os.environ["SANDBOX_SITE_PATH"],
        timezone=config.timezone,
    )
    # list_dirs returns full paths, so the folder name is the last segment. Those names are
    # %Y-%m-%d_%H-%M-%S, which sorts chronologically as a plain string -- no date parsing.
    all_dirs = client_sb.list_dirs(config.dest_file)
    prefixes = sorted((path.rsplit("/", 1)[-1] for path in all_dirs), reverse=True)
    if not prefixes:
        print(f"No runs found under {config.dest_file}.")
        raise Cancelled

    shown = prefixes[:RUN_LIST_LIMIT]
    hidden = len(prefixes) - len(shown)

    print(f"\nRuns under {config.dest_file}  ({len(shown)} of {len(prefixes)} shown)")
    for idx, prefix in enumerate(shown, start=1):
        print(f"  {idx:>2}. {prefix}")
    if hidden:
        print(f"      ({hidden} older run{'s' if hidden > 1 else ''} not shown)")

    selected = shown[ask_index(f"Select a run [1-{len(shown)}, 0 to cancel]: ", len(shown)) - 1]
    logger.info("menu.run.selected", dest_file=config.dest_file, run_prefix=selected)
    return selected


def confirm(name: str, config: Any) -> bool:
    """Show what a pipeline is about to do, and ask before it spends anything.

    Every field is read straight off the Config, so the preview costs no network call and
    cannot itself fail. It is here to catch the expensive mistake -- firing the wrong
    pipeline, or writing to the wrong destination -- not to estimate a bill.

    Returns:
        True if the operator answered yes.

    Raises:
        Cancelled: stdin reached EOF, or the operator pressed Ctrl-C.
    """
    bucket = getattr(config, "gcs_bucket", None)
    gcs_dest = getattr(config, "gcs_dest_path", None)
    # A sentinel rather than None, because None is a meaningful run_id -- "fresh run" -- so a
    # config that has the field must render differently from one that never had it.
    run_id = getattr(config, "run_id", _ABSENT)
    rows = (
        ("run", None if run_id is _ABSENT else (run_id or "(new run)")),
        ("model", getattr(config, "model_name", None)),
        ("asr", getattr(config, "asr_model_name", None)),
        # documents/google_output.py has src_file commented out and local documents never had
        # one, so this must be a getattr rather than an attribute access.
        ("src", getattr(config, "src_file", None)),
        ("dest", getattr(config, "dest_file", None)),
        ("gcs", f"{bucket}/{gcs_dest}" if bucket and gcs_dest else None),
        ("location", getattr(config, "vertex_location", None)),
        ("endpoint", getattr(config, "base_url", None)),
    )

    print(f"\nAbout to run: {name}")
    for label, value in rows:
        if value:
            print(f"  {label:<10}{value}")

    if ask("Proceed? [y/N]: ").lower() in ("y", "yes"):
        return True
    print("Cancelled -- nothing was submitted.")
    return False


def print_menu() -> None:
    """Render the menu on stdout, alongside the prompt rather than across streams from it.

    Grouped by use case, because that is the order the work happens in: pick a use case, then
    walk it through run -> exact match -> confusion matrix. The number beside each row is its
    1-based position in :data:`ACTIONS`, counted here rather than stored, so a regroup moves
    the label and its dispatch together and the two cannot disagree.
    """
    print("\nSelect an option:")
    print("   0. Exit")
    number = 0
    for title, rows in SECTIONS:
        print(f"\n  {title}")
        for action in rows:
            number += 1
            print(f"  {number:>2}. {action.label}")


def main() -> int:
    """Run one action and return. Returns a process exit code."""
    print_menu()
    try:
        index = ask_index(f"Enter the option number [0-{len(ACTIONS)}]: ", len(ACTIONS))
    except Cancelled:
        print("Exit.")
        return 0

    action = ACTIONS[index - 1]
    # The label goes in the log beside the number: option numbers move when the menu is
    # regrouped, so a bare number would not identify the run in an old log.
    logger.info("menu.option.selected", option=str(index), action=action.label)

    try:
        if action.kind == "output_google":
            # No resume path, and run() builds its own Config inside -- the instance shown
            # here is a preview of those defaults, not the object that executes.
            if confirm(action.label, action.config_cls()):
                action.run()
        elif action.kind == "output_local":
            # The previewed Config instance is the one that executes: the local output
            # pipelines take `config` and fall back to their own Config() only when it is
            # None, so the run_id picked here is exactly what confirm() showed. Keep it that
            # way -- a Config mutated after the preview, or rebuilt for the call, would
            # preview one thing and run another.
            config = action.config_cls()
            config = replace(config, run_id=select_run_id(config))
            if confirm(action.label, config):
                action.run(config)
        elif action.kind == "scorer":
            # No spend gate: scoring reads a workbook and appends sheets to it.
            config = action.config_cls()
            action.run(replace(config, run_prefix=select_run_prefix(config)))
        else:  # pragma: no cover -- a kind added to Action without a branch here.
            raise ValueError(f"unknown action kind: {action.kind!r}")
    except Cancelled:
        print("Cancelled.")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
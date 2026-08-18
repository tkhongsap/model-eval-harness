"""The refusals in `scripts/doc_claims.py`, made to fire rather than described.

Every wrong number this project has published reached a reader because a document was the
one artefact nothing executed. That makes the checker's own failure modes unusually
important: if it can be made to pass over a figure -- by a typo in a marker, a renamed key,
a source that is not there -- then it has replaced "nobody checked this" with "something
checked this and said it was fine", which is strictly worse than the state before it existed.

So each refusal gets a test that makes it fire, and the last test in the file runs the real
gate over the real documents, because a checker green only against fixtures it wrote itself
is checking its own imagination.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load_module():
    """Import the script by path: `scripts/` is not a package.

    Registering it in `sys.modules` first is not optional here -- `@dataclass` resolves its
    annotations through `sys.modules[cls.__module__]`, and without this the import dies at
    class-definition time with an unrelated-looking AttributeError.
    """
    spec = importlib.util.spec_from_file_location(
        "doc_claims", REPO / "scripts" / "doc_claims.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


doc_claims = _load_module()


SOURCE = {
    "items": {"pack_a": 138, "pooled": 188},
    "bands": {"accuracy": {"acceptable": 80.0, "excellent": 90.0}},
    "business_accuracy": [
        {
            "n": 188,
            "incumbent_correct": 175,
            "incumbent_accuracy": 0.9309,
            "incumbent_band": "excellent",
            "candidate_band": "excellent",
        }
    ],
    "pooled_paired": [{"discordant": 4, "net": 0, "band": None, "verdict": "UNDERPOWERED"}],
}


@pytest.fixture
def reports(tmp_path, monkeypatch) -> Path:
    """A stand-in docs/reports/ so a fixture never depends on today's real figures."""
    directory = tmp_path / "reports"
    directory.mkdir()
    (directory / "src.json").write_text(json.dumps(SOURCE), encoding="utf-8")
    monkeypatch.setattr(doc_claims, "REPORTS", directory)
    return directory


def _doc(tmp_path: Path, body: str, name: str = "doc.md") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def _run(mode: str, *targets: Path) -> tuple[int, str]:
    out = io.StringIO()
    code = doc_claims.main([mode, *(str(t) for t in targets)], out=out)
    return code, out.getvalue()


# ------------------------------------------------------------------------------------------
# Refusal 1: the cited report is not there.
# ------------------------------------------------------------------------------------------

def test_source_that_does_not_exist_refuses(tmp_path, reports) -> None:
    """Not "cannot resolve, skipping". An unreadable source means an unchecked figure."""
    doc = _doc(tmp_path, "<!--claim:gone.json:items.pooled:int-->188<!--/-->\n")
    claim = doc_claims.parse_document(doc).claims[0]
    with pytest.raises(doc_claims.MissingSource) as exc:
        doc_claims.render(claim, {})
    assert "nothing to fall back to" in str(exc.value)

    code, output = _run("--check", doc)
    assert code == 1
    assert "REFUSED" in output


# ------------------------------------------------------------------------------------------
# Refusal 2: the path does not resolve.
# ------------------------------------------------------------------------------------------

def test_path_that_does_not_resolve_refuses(tmp_path, reports) -> None:
    doc = _doc(tmp_path, "<!--claim:src.json:items.pack_b:int-->50<!--/-->\n")
    claim = doc_claims.parse_document(doc).claims[0]
    with pytest.raises(doc_claims.UnresolvedPath) as exc:
        doc_claims.render(claim, {})
    message = str(exc.value)
    # The renamed-key case is the common one, so the message has to say what WAS there.
    assert "'pack_b'" in message
    assert "'pack_a'" in message


def test_index_past_the_end_refuses(tmp_path, reports) -> None:
    doc = _doc(tmp_path, "<!--claim:src.json:business_accuracy[2].n:int-->188<!--/-->\n")
    claim = doc_claims.parse_document(doc).claims[0]
    with pytest.raises(doc_claims.UnresolvedPath) as exc:
        doc_claims.render(claim, {})
    assert "1 entries" in str(exc.value)


def test_walking_into_a_scalar_refuses(tmp_path, reports) -> None:
    """A report that flattened a nested block must not read as "key absent"."""
    doc = _doc(tmp_path, "<!--claim:src.json:items.pooled.value:int-->188<!--/-->\n")
    claim = doc_claims.parse_document(doc).claims[0]
    with pytest.raises(doc_claims.UnresolvedPath) as exc:
        doc_claims.render(claim, {})
    assert "not an object" in str(exc.value)


# ------------------------------------------------------------------------------------------
# Refusal 3: the document and the data disagree. The headline.
# ------------------------------------------------------------------------------------------

def test_figure_that_drifted_from_its_source_refuses(tmp_path, reports) -> None:
    """The DEVLOG 320/465 shape: the document is internally consistent and still wrong."""
    doc = _doc(
        tmp_path,
        "accuracy was <!--claim:src.json:business_accuracy[0].incumbent_accuracy:pct1-->"
        "92.4%<!--/--> on the pooled set.\n",
    )
    claim = doc_claims.parse_document(doc).claims[0]
    with pytest.raises(doc_claims.Mismatch) as exc:
        doc_claims.verify(claim, {})
    message = str(exc.value)
    assert "expected  93.1%" in message
    assert "found     92.4%" in message


def test_check_names_document_expected_found_and_source(tmp_path, reports) -> None:
    doc = _doc(
        tmp_path,
        "<!--claim:src.json:business_accuracy[0].incumbent_correct,"
        "business_accuracy[0].n:frac-->170/188<!--/-->\n",
    )
    code, output = _run("--check", doc)
    assert code == 1
    assert f"{doc}:1" in output.replace("\\", "/") or "doc.md:1" in output
    assert "expected  175/188" in output
    assert "found     170/188" in output
    assert "business_accuracy[0].incumbent_correct" in output
    assert "1 of 1 published figures do not match their source" in output


def test_a_stale_band_grade_refuses(tmp_path, reports) -> None:
    """Band grades are figures too: "excellent" is a published claim about a threshold."""
    doc = _doc(tmp_path, "<!--claim:src.json:pooled_paired[0].verdict:text-->AHEAD<!--/-->\n")
    with pytest.raises(doc_claims.Mismatch):
        doc_claims.verify(doc_claims.parse_document(doc).claims[0], {})


def test_paths_that_stopped_agreeing_refuse(tmp_path, reports) -> None:
    """"both excellent" is a two-sided claim, and half of it going stale must fire.

    No single number in the document is wrong in this case -- the word "both" is. That is the
    migration-decision failure mode exactly: a correct figure described with the wrong scope.
    """
    data = json.loads((reports / "src.json").read_text(encoding="utf-8"))
    data["business_accuracy"][0]["candidate_band"] = "good"
    (reports / "src.json").write_text(json.dumps(data), encoding="utf-8")

    doc = _doc(
        tmp_path,
        "both <!--claim:src.json:business_accuracy[0].incumbent_band,"
        "business_accuracy[0].candidate_band:text-->excellent<!--/-->\n",
    )
    with pytest.raises(doc_claims.Mismatch) as exc:
        doc_claims.verify(doc_claims.parse_document(doc).claims[0], {})
    assert "no longer agree" in str(exc.value)


# ------------------------------------------------------------------------------------------
# Refusal 4: the marker itself. A checker that ignores what it cannot parse lies.
# ------------------------------------------------------------------------------------------

def test_unclosed_marker_refuses(tmp_path, reports) -> None:
    doc = _doc(tmp_path, "the figure is <!--claim:src.json:items.pooled:int-->188 items\n")
    with pytest.raises(doc_claims.MalformedMarker) as exc:
        doc_claims.parse_document(doc)
    assert "unclosed" in str(exc.value)


def test_marker_missing_a_field_refuses(tmp_path, reports) -> None:
    """The syntax sketched in the planning document has no format field. It must not pass."""
    doc = _doc(tmp_path, "<!--claim:src.json:items.pooled-->188<!--/-->\n")
    with pytest.raises(doc_claims.MalformedMarker) as exc:
        doc_claims.parse_document(doc)
    assert "malformed claim marker" in str(exc.value)


def test_unknown_format_refuses(tmp_path, reports) -> None:
    doc = _doc(tmp_path, "<!--claim:src.json:items.pooled:pct2-->188<!--/-->\n")
    with pytest.raises(doc_claims.MalformedMarker) as exc:
        doc_claims.parse_document(doc)
    assert "unknown claim format 'pct2'" in str(exc.value)


def test_a_second_marker_opening_before_the_close_refuses(tmp_path, reports) -> None:
    """A dropped close would otherwise swallow the next figure into this one's text."""
    doc = _doc(
        tmp_path,
        "<!--claim:src.json:items.pack_a:int-->138 and "
        "<!--claim:src.json:items.pooled:int-->188<!--/-->\n",
    )
    with pytest.raises(doc_claims.MalformedMarker) as exc:
        doc_claims.parse_document(doc)
    assert "unclosed" in str(exc.value)


def test_stray_closer_refuses(tmp_path, reports) -> None:
    """An opener edited away leaves its close behind and silently unmarks the figure."""
    doc = _doc(tmp_path, "the pooled set has 188<!--/--> items\n")
    with pytest.raises(doc_claims.MalformedMarker) as exc:
        doc_claims.parse_document(doc)
    assert "no claim marker opening it" in str(exc.value)


def test_claim_spanning_a_line_break_refuses(tmp_path, reports) -> None:
    doc = _doc(tmp_path, "<!--claim:src.json:items.pooled:int-->188\nitems<!--/-->\n")
    with pytest.raises(doc_claims.MalformedMarker) as exc:
        doc_claims.parse_document(doc)
    assert "spans a line break" in str(exc.value)


def test_source_reaching_outside_docs_reports_refuses(tmp_path, reports) -> None:
    doc = _doc(tmp_path, "<!--claim:../../secret.json:items.pooled:int-->188<!--/-->\n")
    with pytest.raises(doc_claims.MalformedMarker) as exc:
        doc_claims.parse_document(doc)
    assert "not a bare filename" in str(exc.value)


def test_unusable_path_segment_refuses(tmp_path, reports) -> None:
    doc = _doc(tmp_path, "<!--claim:src.json:items..pooled:int-->188<!--/-->\n")
    claim = doc_claims.parse_document(doc).claims[0]
    with pytest.raises(doc_claims.MalformedMarker) as exc:
        doc_claims.render(claim, {})
    assert "unusable segment" in str(exc.value)


def test_frac_with_one_path_refuses(tmp_path, reports) -> None:
    doc = _doc(tmp_path, "<!--claim:src.json:business_accuracy[0].n:frac-->175/188<!--/-->\n")
    claim = doc_claims.parse_document(doc).claims[0]
    with pytest.raises(doc_claims.MalformedMarker) as exc:
        doc_claims.render(claim, {})
    assert "exactly two paths" in str(exc.value)


# ------------------------------------------------------------------------------------------
# Refusal 5: a run that checked nothing is not a passing run.
# ------------------------------------------------------------------------------------------

def test_a_document_with_no_markers_is_a_failure(tmp_path, reports) -> None:
    doc = _doc(tmp_path, "Accuracy was 93.1% on the pooled set, which is excellent.\n")
    code, output = _run("--check", doc)
    assert code == 1
    assert "no claim markers found" in output


def test_a_document_that_does_not_exist_is_a_failure(tmp_path, reports) -> None:
    code, output = _run("--check", tmp_path / "absent.md")
    assert code == 1
    assert "does not exist" in output


# ------------------------------------------------------------------------------------------
# The one thing passed over, and the fact that it is counted out loud.
# ------------------------------------------------------------------------------------------

def test_a_marker_in_a_code_fence_is_an_example_and_is_reported(tmp_path, reports) -> None:
    """The document that specifies this syntax necessarily contains a non-claim marker.

    Passing over it is correct; passing over it silently is not, because "it was in a fence"
    would then be an invisible reason a real figure went unchecked.
    """
    doc = _doc(
        tmp_path,
        "```markdown\n"
        "<!--claim:nowhere.json:not.a.path-->93.1%<!--/-->\n"
        "```\n"
        "and the real one: <!--claim:src.json:items.pooled:int-->188<!--/-->\n",
    )
    scan = doc_claims.parse_document(doc)
    assert len(scan.claims) == 1
    assert len(scan.examples) == 1

    code, output = _run("--check", doc)
    assert code == 0
    assert "1 marker(s) inside code fences read as examples, not claims" in output


# ------------------------------------------------------------------------------------------
# Rendering. The rule is to render the way the figure was produced.
# ------------------------------------------------------------------------------------------

def test_pct1_matches_how_the_producer_printed_it(tmp_path, reports) -> None:
    """scripts/pooled_bands.py printed `{100*inc/n:.1f}%`. Rounding differently here would
    make this a second opinion about rounding rather than a check on the number."""
    assert doc_claims._pct1(0.9309) == f"{100 * 175 / 188:.1f}%"


def test_int_refuses_a_value_it_would_have_to_round(tmp_path, reports) -> None:
    assert doc_claims._integer(90.0) == "90"
    with pytest.raises(doc_claims.Mismatch) as exc:
        doc_claims._integer(0.9309)
    assert "whole number" in str(exc.value)


def test_absent_band_renders_as_an_em_dash_rather_than_disappearing(tmp_path, reports) -> None:
    """`exact_band` returns None when no band exists at any net. That is a finding, not a
    hole: if a band later appears where the document prints an em dash, this must fire."""
    doc = _doc(tmp_path, "<!--claim:src.json:pooled_paired[0].band:pm-->—<!--/-->\n")
    assert doc_claims.verify(doc_claims.parse_document(doc).claims[0], {}) == "—"

    data = json.loads((reports / "src.json").read_text(encoding="utf-8"))
    data["pooled_paired"][0]["band"] = 16
    (reports / "src.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(doc_claims.Mismatch):
        doc_claims.verify(doc_claims.parse_document(doc).claims[0], {})


def test_a_typographic_minus_is_the_same_number(tmp_path, reports) -> None:
    """U+2212 is what these tables print. Folding it is safe; folding an en dash is not,
    because an en dash in this prose is a range separator ("90–96%", "5–16×")."""
    data = json.loads((reports / "src.json").read_text(encoding="utf-8"))
    data["pooled_paired"][0]["net"] = -6
    (reports / "src.json").write_text(json.dumps(data), encoding="utf-8")

    doc = _doc(tmp_path, "<!--claim:src.json:pooled_paired[0].net:int-->−6<!--/-->\n")
    assert doc_claims.verify(doc_claims.parse_document(doc).claims[0], {}) == "-6"
    assert doc_claims._normalise("90–96") == "90–96"


def test_text_format_refuses_a_number(tmp_path, reports) -> None:
    doc = _doc(tmp_path, "<!--claim:src.json:items.pooled:text-->188<!--/-->\n")
    with pytest.raises(doc_claims.Mismatch) as exc:
        doc_claims.verify(doc_claims.parse_document(doc).claims[0], {})
    assert "needs a string" in str(exc.value)


# ------------------------------------------------------------------------------------------
# The live gate. Fixtures the checker wrote itself prove only that it is self-consistent.
# ------------------------------------------------------------------------------------------

def test_the_published_documents_still_match_their_sources() -> None:
    """This is the gate. It reads docs/**/*.md and docs/reports/*.json as committed."""
    code, output = _run("--check")
    assert code == 0, output


def test_the_decision_document_marks_all_three_figure_families() -> None:
    """Coverage is the point. A checker over one lonely marker passes and proves nothing."""
    scan = doc_claims.parse_document(REPO / "docs" / "migration-decision.md")
    paths = {path for claim in scan.claims for path in claim.paths}
    assert any(p.startswith("business_accuracy[") for p in paths), "business accuracy"
    assert any(p.startswith("pooled_paired[") for p in paths), "paired verdicts"
    assert any(p.endswith("_band") or p.endswith(".band") for p in paths), "band grades"
    assert {claim.fmt for claim in scan.claims} >= {"frac", "pct1", "int", "text", "pm"}


def test_list_resolves_every_marked_figure() -> None:
    code, output = _run("--list")
    assert code == 0
    assert "MISMATCH" not in output

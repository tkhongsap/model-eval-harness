"""The four refusals in `scripts/pooled_bands.py`, exercised rather than asserted in prose.

Pooling is the one operation in this project that turns two honest measurements into a
dishonest one by arithmetic alone, and every way it goes wrong produces a number that looks
like MORE evidence rather than less. A refusal nobody has watched fire is a comment.

The fourth documented refusal -- "never turns a band into a winner" -- is a property of what
the script prints, not a raised exception, so it is checked as output rather than as an
error: the band grade and the paired verdict must appear for the same pair, because the
whole failure mode is quoting one without the other.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))


def _load_module():
    """Import the script by path: `scripts/` is not a package."""
    spec = importlib.util.spec_from_file_location(
        "pooled_bands", REPO / "scripts" / "pooled_bands.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


pooled_bands = _load_module()


# --------------------------------------------------------------------------------------
# The bands come from production, or they do not come at all.
# --------------------------------------------------------------------------------------

def test_bands_match_productions_published_values() -> None:
    """The whole point of reading the file is that these are not our numbers."""
    bands = pooled_bands.read_bands(pooled_bands.PROD_CONFIG)
    assert bands["precision"] == (75.0, 80.0, 90.0)
    assert bands["recall"] == (75.0, 80.0, 90.0)
    # Production spells this one `f1_score`; the script renames it to match the metrics files.
    assert bands["f1"] == (80.0, 85.0, 90.0)


def test_missing_config_refuses_rather_than_falling_back(tmp_path: Path) -> None:
    with pytest.raises(pooled_bands.Refused) as exc:
        pooled_bands.read_bands(tmp_path / "absent.yml")
    assert "nothing to fall back to" in str(exc.value)


def test_config_without_the_block_refuses(tmp_path: Path) -> None:
    """A renamed block must not silently degrade to 'no bands found, grade nothing'."""
    path = tmp_path / "qa.yml"
    path.write_text("app:\n  framework:\n    concurrency_upload: 4\n", encoding="utf-8")
    with pytest.raises(pooled_bands.Refused) as exc:
        pooled_bands.read_bands(path)
    assert "metric_thresholds" in str(exc.value)


def test_non_ascending_bands_refuse(tmp_path: Path) -> None:
    """A non-monotone scale puts a better number in a worse band, silently."""
    path = tmp_path / "qa.yml"
    path.write_text(
        "app:\n"
        "  metric_thresholds:\n"
        "    precision:\n"
        "      acceptable: 90\n"
        "      good: 80\n"
        "      excellent: 75\n",
        encoding="utf-8",
    )
    with pytest.raises(pooled_bands.Refused) as exc:
        pooled_bands.read_bands(path)
    assert "not ascending" in str(exc.value)


def test_partial_band_triple_refuses(tmp_path: Path) -> None:
    path = tmp_path / "qa.yml"
    path.write_text(
        "app:\n"
        "  metric_thresholds:\n"
        "    precision:\n"
        "      acceptable: 75\n"
        "      good: 80\n",
        encoding="utf-8",
    )
    with pytest.raises(pooled_bands.Refused) as exc:
        pooled_bands.read_bands(path)
    assert "missing" in str(exc.value)


# --------------------------------------------------------------------------------------
# Grading is production's vocabulary, including the part where a model fails it.
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value,expected",
    [
        (0.900, "excellent"),   # exactly on a boundary is inside the better band
        (0.899, "good"),
        (0.800, "good"),
        (0.799, "acceptable"),
        (0.750, "acceptable"),
        (0.749, "BELOW"),
        (0.713, "BELOW"),       # Gemma's measured reason precision
    ],
)
def test_grade_boundaries(value: float, expected: str) -> None:
    assert pooled_bands.grade(value, (75.0, 80.0, 90.0)) == expected


# --------------------------------------------------------------------------------------
# Pooling preconditions.
# --------------------------------------------------------------------------------------

def _contract(**overrides):
    base = {
        "prompt_sha": "p" * 64,
        "scoring_code_sha": "s" * 64,
        "repeats": 3,
        "testset_sha": "t" * 64,
        "items": 138,
    }
    base.update(overrides)
    return base


def _write_pack(path: Path, contract: dict) -> None:
    path.write_text(
        json.dumps({"shared_contract": contract, "models": {}}), encoding="utf-8"
    )


def test_refuses_to_pool_across_a_prompt_change(tmp_path, monkeypatch) -> None:
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    _write_pack(a, _contract())
    _write_pack(b, _contract(prompt_sha="q" * 64, testset_sha="u" * 64, items=50))
    monkeypatch.setattr(pooled_bands, "PACK_A", a)
    monkeypatch.setattr(pooled_bands, "PACK_B", b)
    with pytest.raises(pooled_bands.Refused) as exc:
        pooled_bands.load_packs()
    assert "prompt_sha" in str(exc.value)


def test_refuses_to_pool_across_a_scorer_change(tmp_path, monkeypatch) -> None:
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    _write_pack(a, _contract())
    _write_pack(b, _contract(scoring_code_sha="z" * 64, testset_sha="u" * 64, items=50))
    monkeypatch.setattr(pooled_bands, "PACK_A", a)
    monkeypatch.setattr(pooled_bands, "PACK_B", b)
    with pytest.raises(pooled_bands.Refused) as exc:
        pooled_bands.load_packs()
    assert "scoring_code_sha" in str(exc.value)


def test_refuses_to_pool_a_pack_with_itself(tmp_path, monkeypatch) -> None:
    """The dangerous one: identical items added twice halve the apparent noise."""
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    _write_pack(a, _contract())
    _write_pack(b, _contract())          # same testset_sha
    monkeypatch.setattr(pooled_bands, "PACK_A", a)
    monkeypatch.setattr(pooled_bands, "PACK_B", b)
    with pytest.raises(pooled_bands.Refused) as exc:
        pooled_bands.load_packs()
    assert "double-count" in str(exc.value)


def test_the_real_packs_are_poolable() -> None:
    """The committed packs satisfy every precondition -- otherwise the run above lied."""
    a, b = pooled_bands.load_packs()
    assert a["shared_contract"]["testset_sha"] != b["shared_contract"]["testset_sha"]
    for key in pooled_bands.POOLABLE:
        assert a["shared_contract"][key] == b["shared_contract"][key]


# --------------------------------------------------------------------------------------
# The arithmetic, and the two things the output must never do.
# --------------------------------------------------------------------------------------

def test_pooling_reason_reaches_a_testable_discordant_count() -> None:
    """Neither pack alone resolves; pooled, `reason` is the only dimension that can be.

    Hand-checked against the committed packs: 28 + 12 discordant, net -8 + 2.
    """
    from evalharness.compare import Disagreement, paired_verdict

    a, b = pooled_bands.load_packs()
    ra = a["models"]["qwen38"]["paired_vs_gemini"]["reason"]
    rb = b["models"]["qwen38"]["paired_vs_gemini"]["reason"]
    assert (ra["discordant"], rb["discordant"]) == (28, 12)
    assert (ra["net"], rb["net"]) == (-8, 2)

    table = Disagreement(
        dimension="reason",
        both_right=ra["both_right"] + rb["both_right"],
        both_wrong=ra["both_wrong"] + rb["both_wrong"],
        incumbent_only_right=ra["incumbent_only"] + rb["incumbent_only"],
        candidate_only_right=ra["candidate_only"] + rb["candidate_only"],
    )
    verdict = paired_verdict(table)
    assert table.incumbent_only_right + table.candidate_only_right == 40
    assert table.net == -6
    assert verdict.band == 16
    assert verdict.verdict == "INDISTINGUISHABLE"


def test_saturated_dimensions_stay_underpowered_when_pooled() -> None:
    """Pooling buys power only where the models actually disagree.

    call_result and product must NOT come out resolvable: d=4 and d=1 are below the six
    discordant clusters `exact_band` needs at this alpha. If a change ever makes these
    report a verdict, the pooling is wrong, not the models suddenly distinguishable.
    """
    from evalharness.compare import Disagreement, paired_verdict

    a, b = pooled_bands.load_packs()
    for dim, expected_d in (("call_result", 4), ("product", 1)):
        ra = a["models"]["qwen38"]["paired_vs_gemini"][dim]
        rb = b["models"]["qwen38"]["paired_vs_gemini"][dim]
        table = Disagreement(
            dimension=dim,
            both_right=ra["both_right"] + rb["both_right"],
            both_wrong=ra["both_wrong"] + rb["both_wrong"],
            incumbent_only_right=ra["incumbent_only"] + rb["incumbent_only"],
            candidate_only_right=ra["candidate_only"] + rb["candidate_only"],
        )
        verdict = paired_verdict(table)
        assert table.incumbent_only_right + table.candidate_only_right == expected_d
        assert verdict.band is None
        assert verdict.verdict == "UNDERPOWERED"


def test_models_absent_from_pack_b_are_never_reported_as_pooled(capsys, tmp_path) -> None:
    """Refusal 3: a pack-A figure under a 'pooled' heading is a lie of formatting."""
    assert pooled_bands.main(out_path=tmp_path / "pooled-bands.json") == 0
    out = capsys.readouterr().out
    pooled_section = out.split("3. IS IT DIFFERENT?")[1]
    verdict_lines = [
        line for line in pooled_section.splitlines()
        if any(v in line for v in ("UNDERPOWERED", "INDISTINGUISHABLE", "AHEAD", "BEHIND"))
    ]
    assert verdict_lines, "no paired verdicts printed at all"
    for absent in ("Qwen3.6", "Gemma"):
        assert not any(absent in line for line in verdict_lines)
        assert f"{absent}" in pooled_section  # named, with the reason, not silently dropped
    assert "not pooled" in pooled_section


def test_band_and_verdict_are_printed_together(capsys, tmp_path) -> None:
    """Refusal 4: the two answer different questions and must never be quotable apart."""
    assert pooled_bands.main(out_path=tmp_path / "pooled-bands.json") == 0
    out = capsys.readouterr().out
    # Qwen3.8 grades one band above Gemini on reason precision (79.8 vs 76.3) ...
    assert "79.8%" in out and "76.3%" in out
    # ... while the test on the same pair says the difference is not resolvable.
    assert "INDISTINGUISHABLE" in out
    assert "a model can be in a higher band while the test says the" in out


def test_the_suite_never_rewrites_the_published_json(tmp_path) -> None:
    """`pytest` must not regenerate the file `doc_claims.py` checks documents against.

    Both tests above once called `main()` with no argument, so every suite run rewrote
    `docs/reports/pooled-bands.json` in the real tree. That file is the source of truth the
    published-figure checker compares documents to -- so a drift between a document and its
    data could be erased by running the tests, before anyone saw it. A gate whose evidence
    its own test suite refreshes is not a gate.
    """
    published = pooled_bands.DOCS / "pooled-bands.json"
    before = published.read_bytes() if published.exists() else None

    assert pooled_bands.main(out_path=tmp_path / "elsewhere.json") == 0

    assert (tmp_path / "elsewhere.json").exists(), "the override was ignored"
    after = published.read_bytes() if published.exists() else None
    assert after == before, "main() wrote into the real tree despite an out_path override"

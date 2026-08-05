"""The whole chain, proved without a network: fakes -> runner -> flatten -> score -> report.

Every module in `evalgen` is tested on its own, and every one of those tests passes
against a pipeline that does not fit together. The failures this file exists to catch
are the ones that live in the joins:

  * a payload flattened at CALL grain instead of PRODUCT grain -- RET-16 is one call
    with three products, and an implementation that emits one row per call still
    scores, still prints, and silently loses two thirds of that item;
  * replicates pooled into one prediction list, which `metrics.outer_join` silently
    collapses to whichever replicate happened to be last (`metrics.py:122`);
  * two arms sharing one arm name, which `report.render` keys its mechanism tables by,
    so the paired comparison quietly becomes an arm compared with itself;
  * failures dropped rather than scored, which hands the arm that failed more often
    the easier denominator.

None of those raise. Each produces a full, plausible-looking mechanism table. So the
integration test asserts the table's CONTENT against arms whose behaviour is known
exactly -- a perfect arm must PASS everywhere, an arm broken on one known item must
FAIL exactly that item's mechanism -- rather than asserting that a report was produced.

**The client is a fake and there is no network anywhere in this file.** `build_client`
is replaced in every test that could reach for one, and the two tests that must not
call it at all (`check`, `--dry-run`) replace it with something that raises, so a
regression that started making calls fails here rather than on an invoice.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from evalgen import cli, runner  # noqa: E402
from evalgen.cli import EXIT_OK, EXIT_PROBLEMS, EXIT_REFUSED, main  # noqa: E402
from evalgen.testsets import load_testset  # noqa: E402

TESTSETS = ROOT / "tests" / "fixtures" / "testsets"
TESTSET_PATH = TESTSETS / "retention_v1.jsonl"
GT_PATH = TESTSETS / "retention_v1.gt.csv"


# --------------------------------------------------------------------------- fakes


@dataclass(frozen=True)
class FakeCompletion:
    """A structural copy of `client.Completion`.

    Copied rather than imported for the same reason `runner.py` does not import it:
    the SDK is not in the environment CI builds (the root `requirements.txt` pins the
    scoring path and omits `openai`), and a suite that imported it would pass on a
    laptop and error in CI.
    """

    content: str | None = None
    finish_reason: str | None = "stop"
    observed_model: str | None = "vendor/fake-model"
    generation_id: str | None = "gen-fake"
    provider: str | None = "FakeProvider"
    prompt_tokens: int | None = 1200
    completion_tokens: int | None = 180
    reasoning_tokens: int | None = 0
    cost: float | None = 0.0004
    latency_s: float = 0.01


class FakeTransportError(RuntimeError):
    """What the fake raises where `client.TransportError` would be. No status: a
    timeout has none, which is what `_is_retryable` treats as worth retrying."""

    def __init__(self, message: str, *, latency_s: float = 0.01) -> None:
        super().__init__(message)
        self.latency_s = latency_s


class FakeClient:
    """Answers by item id and replicate, and counts every call.

    `responder(item_id, nth)` returns a `FakeCompletion` or raises. `nth` counts calls
    for that item across replicates, so a responder can make an item flip between
    replicates -- which is the only way to produce a FLAKY verdict, and the verdict a
    single-replicate harness cannot tell from PASS.
    """

    def __init__(self, responder, *, testset) -> None:
        self._by_transcript = {item.transcript_th: item.item_id for item in testset.items}
        self._responder = responder
        self.calls: list[str] = []
        self.requests: list[dict] = []

    def complete(self, *, model, messages, max_tokens, temperature,
                 top_p=None, seed=None, response_format=None, provider=None):
        item_id = self._by_transcript[messages[1]["content"]]
        self.calls.append(item_id)
        self.requests.append({"item_id": item_id, "model": model, "provider": provider})
        nth = sum(1 for call in self.calls if call == item_id)
        return self._responder(item_id, nth)


def payload_for(item, *, call_result=None) -> dict:
    """The answer a perfect model would give for this item, in the schema's shape.

    Built from the item's OWN ground truth rather than hand-written, so the perfect arm
    is perfect by construction and any mechanism it fails is a defect in the pipeline
    rather than in this fixture.

    Note what is passed through verbatim: RET-17's `main` cell reads
    `"network, promotion related"`, which is TWO labels -- `records.parse_reasons`
    comma-splits it exactly as `get_reasons_set` does (`fact_checker.py:877`). Writing
    it into one `reason` slot is therefore the right shape, and a fixture that "fixed"
    it into two slots would be testing a grain the scorer does not use.
    """
    return {
        "product": {
            row["product"]: {
                "main": {"reason": row.get("main") or "", "keyword": ""},
                "secondary": {"reason": row.get("secondary") or "", "keyword": ""},
                "third": {"reason": row.get("third") or "", "keyword": ""},
                "retention_outcome": call_result or row.get("call_result") or "",
            }
            for row in item.gt
        },
        "call_event_detection": "Emerging or Undefined Events (เหตุผลที่ยังไม่สามารถจัดกลุ่มได้)",
        "recommendation": "เสนอโปรโมชันที่ตรงกับการใช้งาน",
    }


def answer(item, **overrides) -> FakeCompletion:
    """A completion whose content is the item's ground truth as JSON, Thai unescaped."""
    body = overrides.pop("payload", None) or payload_for(item)
    return FakeCompletion(content=json.dumps(body, ensure_ascii=False), **overrides)


# ------------------------------------------------------------------------ fixtures


@pytest.fixture(scope="module")
def testset():
    return load_testset(TESTSET_PATH)


@pytest.fixture
def env(monkeypatch, tmp_path):
    """A run environment with no network and no real secrets.

    `EVAL_HARNESS_KEY_HMAC` is set because `compare` refuses without it: the per-item
    regression list is HMAC-keyed, and an empty list prints as "none." -- which reads
    as "the candidate lost nothing" rather than "this was never computed".
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake-not-a-real-key")
    monkeypatch.setenv("EVAL_HARNESS_KEY_HMAC", "test-hmac-key")
    monkeypatch.setattr(runner, "BACKOFF_BASE_S", 0.0)
    return tmp_path


@pytest.fixture
def run_arm(env, testset, monkeypatch):
    """Run one arm through `main` with a fake client, and return its run directory.

    The client is installed per call rather than per test because two arms in one test
    answer differently, and swapping the responder underneath a shared fake would make
    the call counts of the two arms indistinguishable.
    """
    seen: list[FakeClient] = []

    def _run(arm, responder, *, repeats=2, model="vendor/fake-model"):
        client = FakeClient(responder, testset=testset)
        seen.append(client)
        monkeypatch.setattr(cli, "build_client", lambda api_key, timeout=120.0: client)
        out = env / "runs"
        before = set(out.iterdir()) if out.exists() else set()
        code = main([
            "baseline", "--arm", arm, "--model", model,
            "--testset", str(TESTSET_PATH), "--gt", str(GT_PATH),
            "--out", str(out), "--repeats", str(repeats), "--concurrency", "4",
        ])
        assert code == EXIT_OK, f"arm {arm} did not run cleanly"
        # The directory this call created, by difference. Matching on the arm name
        # would silently pick up the previous run when two runs land in one second,
        # which is the collision `new_run_dir` exists to prevent.
        created = sorted(set(out.iterdir()) - before)
        assert len(created) == 1, f"expected one new run directory, got {created}"
        return created[0]

    _run.clients = seen
    return _run


@pytest.fixture
def perfect(testset):
    """A responder that answers every item with its own ground truth."""
    by_id = {item.item_id: item for item in testset.items}
    return lambda item_id, _nth: answer(by_id[item_id])


def mechanism_row(report: str, mechanism: str) -> list[str]:
    """The verdict pair for one mechanism, read out of section 1's table."""
    table = report.split("2. PER-ITEM DISAGREEMENT")[0]
    line = next(
        line for line in table.splitlines() if line.strip().startswith(mechanism + " ")
    )
    return line.split()[-2:]


# ============================================================================ check


def test_check_passes_on_the_committed_pack(capsys):
    assert main(["check"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "items      20" in out
    assert "rows       22" in out, "22 rows over 20 items: RET-16 is one call, three products"


def test_check_never_builds_a_client(monkeypatch):
    """`check` is documented as costing nothing. A regression that made it construct a
    client would still pass every other test in this file, and would be discovered by
    a 401 or an invoice rather than by CI."""
    def explode(*args, **kwargs):
        raise AssertionError("check must not build a client: no network, no key, no cost")

    monkeypatch.setattr(cli, "build_client", explode)
    assert main(["check"]) == EXIT_OK


def test_check_catches_a_ground_truth_that_drifted_from_the_testset(tmp_path, capsys):
    """THE cross-file check, and the only place it happens.

    There are two ground truths: `item.gt` inside the JSONL and the CSV. `flatten`
    builds failure rows from the first, `metrics` scores against the second. When they
    disagree a failed item emits rows keyed on products the scorer is not expecting,
    those rows join with nothing, and `Coverage.items_joined` falls on the arm that
    failed -- which is the last place anyone is surprised by a lower number.
    """
    tampered = tmp_path / "drifted.gt.csv"
    text = GT_PATH.read_text(encoding="utf-8")
    tampered.write_text(
        text.replace(
            "5001,0810000001,postpaid,save,network,,",
            "5001,0810000001,postpaid,churn,network,,",
        ),
        encoding="utf-8",
        newline="\n",
    )

    code = main(["check", "--gt", str(tampered)])

    assert code == EXIT_PROBLEMS
    out = capsys.readouterr().out
    assert "RET-01" in out and "disagree" in out


def test_check_reports_a_gt_row_no_item_claims(tmp_path, capsys):
    """An unclaimed ground-truth row scores as a miss on every arm, because all three
    denominators are ground-truth driven (metrics.py:11-13)."""
    tampered = tmp_path / "extra.gt.csv"
    tampered.write_text(
        GT_PATH.read_text(encoding="utf-8") + "5099,0810000099,postpaid,save,network,,\n",
        encoding="utf-8",
        newline="\n",
    )

    assert main(["check", "--gt", str(tampered)]) == EXIT_PROBLEMS
    assert "5099" in capsys.readouterr().out


# ========================================================================== dry run


def test_the_dry_run_makes_zero_calls_and_writes_one_body_per_item(
    monkeypatch, tmp_path, capsys
):
    """A dry run exists so a 9.6 KB Thai prompt can be read before tokens are bought.
    It is worth nothing if it can spend money, so the client factory raises here."""
    def explode(*args, **kwargs):
        raise AssertionError("--dry-run must make zero API calls")

    monkeypatch.setattr(cli, "build_client", explode)

    code = main([
        "baseline", "--arm", "incumbent", "--model", "google/gemini-2.5-flash",
        "--out", str(tmp_path), "--dry-run",
    ])

    assert code == EXIT_OK
    directory = next(p for p in tmp_path.iterdir() if p.is_dir())
    bodies = [
        json.loads(line)
        for line in (directory / "requests.jsonl").read_text(encoding="utf-8").split("\n")
        if line.strip()
    ]
    assert len(bodies) == 20
    assert [b["item_id"] for b in bodies] == [f"RET-{n:02d}" for n in range(1, 21)], (
        "bodies must be written in testset order, so two dry runs diff line for line"
    )
    assert not (directory / "run.jsonl").exists(), (
        "a dry run must not write a run log; a log with no responses in it would be "
        "read as a run that produced nothing"
    )
    out = capsys.readouterr().out
    assert "SYSTEM PROMPT" in out and "**Role**: You are a call center agent" in out


def test_the_dry_run_body_carries_the_decoding_and_the_schema(monkeypatch, tmp_path):
    """The four things a reviewer is checking for, each of which is invisible in the
    prompt text: production's decoding parameters, the usage-accounting flag that
    decides whether cost is reported at all, the routing flag that decides whether the
    schema is enforced at all, and the schema whose `required` keys
    `runner._required_keys` reads back out of this very request."""
    monkeypatch.setattr(cli, "build_client", lambda *a, **k: pytest.fail("no calls"))
    main([
        "baseline", "--arm", "incumbent", "--model", "google/gemini-2.5-flash",
        "--out", str(tmp_path), "--dry-run",
    ])
    directory = next(p for p in tmp_path.iterdir() if p.is_dir())
    body = json.loads((directory / "requests.jsonl").read_text(encoding="utf-8").split("\n")[0])

    request = body["request"]
    assert request["temperature"] == 0.0
    assert request["top_p"] == 0.0  # production's own value (main.py:1116-1119)
    assert request["seed"] == 0
    assert request["extra_body"] == {
        "usage": {"include": True},
        # Without this, OpenRouter DROPS `response_format` on an endpoint that cannot
        # honour it and the arm silently falls back to prompt-coaxing while the run log
        # looks identical. Asserted in the reviewed body because that file is the only
        # artifact where a reader could notice it had gone.
        "provider": {"require_parameters": True},
    }
    assert [m["role"] for m in request["messages"]] == ["system", "user"]
    assert request["response_format"]["json_schema"]["schema"]["required"] == [
        "product", "call_event_detection", "recommendation",
    ]


def test_the_dry_run_body_is_the_body_the_client_would_send(monkeypatch, tmp_path, testset):
    """The dry run and the real client must build ONE request, not two that agree today.

    Proved by driving the real `OpenRouterClient` against a stubbed SDK and comparing
    what it hands to `create` with what the dry run wrote out.

    **The client is driven from the ORIGINAL inputs, never from the written file.** An
    earlier version of this test fed the written body back into `complete`, which made
    it self-referential: it proved the client echoes whatever it is given, and a
    mutation run showed it staying green while the dry-run body was corrupted. The
    inputs below -- the assembled prompt, the item's own transcript, production's
    decoding values -- are assembled here independently, so a change on either side
    now moves one dict and not the other.
    """
    pytest.importorskip("openai", reason="the SDK is deliberately absent from CI")
    from evalgen.client import OpenRouterClient
    from evalgen.prompts import build_base

    monkeypatch.setattr(cli, "build_client", lambda *a, **k: pytest.fail("no calls"))
    main([
        "baseline", "--arm", "incumbent", "--model", "google/gemini-2.5-flash",
        "--out", str(tmp_path), "--dry-run",
    ])
    directory = next(p for p in tmp_path.iterdir() if p.is_dir())
    written = json.loads(
        (directory / "requests.jsonl").read_text(encoding="utf-8").split("\n")[0]
    )
    assert written["item_id"] == "RET-01"

    sent: dict = {}

    class _Stub:
        def create(self, **kwargs):
            sent.update(kwargs)
            raise RuntimeError("stop before the network reaches anything")

    client = OpenRouterClient("sk-fake")
    monkeypatch.setattr(client._client.chat, "completions", _Stub())
    with pytest.raises(Exception):
        client.complete(
            model="google/gemini-2.5-flash",
            messages=[
                {"role": "system", "content": build_base().system_text},
                {"role": "user", "content": testset.items[0].transcript_th},
            ],
            # Production's decoding (main.py:1116-1119) and the harness token budget,
            # restated rather than read back out of the file under test.
            max_tokens=8000,
            temperature=0.0,
            top_p=0.0,
            seed=0,
            response_format=cli.response_format(),
        )

    assert sent == written["request"], (
        "the dry-run body and the client's own request have diverged. Both must come "
        "from request.build_request; a second construction is a review of a request "
        "nobody makes. (If the CLI's decoding defaults moved, they moved away from "
        "production's, which is the other thing this compares.)"
    )


def test_a_missing_ground_truth_is_refused_before_any_call(env, testset, monkeypatch, capsys):
    """The GT file is not read until `run.json` is written, which is after every call
    has been paid for. Checked up front instead, so the refusal costs nothing."""
    client = FakeClient(lambda i, n: pytest.fail("no call should be made"), testset=testset)
    monkeypatch.setattr(cli, "build_client", lambda api_key, timeout=120.0: client)

    code = main([
        "baseline", "--arm", "incumbent", "--model", "vendor/fake",
        "--gt", str(env / "does-not-exist.csv"), "--out", str(env / "runs"),
        "--repeats", "1",
    ])

    assert code == EXIT_REFUSED
    assert client.calls == [], "the refusal must land before the first request"
    assert "ground truth not found" in capsys.readouterr().err


def test_a_drifted_pack_is_refused_before_any_call(env, testset, monkeypatch, capsys):
    """A pack whose evidence spans no longer appear in their transcripts still runs and
    still scores; the numbers are just about labels nobody can defend. Milliseconds to
    check, money and an hour to discover afterwards."""
    broken = env / "broken.jsonl"
    lines = [
        line for line in TESTSET_PATH.read_text(encoding="utf-8").split("\n") if line.strip()
    ]
    first = json.loads(lines[0])
    first["evidence"]["ev_reason:network"] = "ข้อความที่ไม่มีอยู่ในบทสนทนา"
    lines[0] = json.dumps(first, ensure_ascii=False)
    broken.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    client = FakeClient(lambda i, n: pytest.fail("no call should be made"), testset=testset)
    monkeypatch.setattr(cli, "build_client", lambda api_key, timeout=120.0: client)

    code = main([
        "baseline", "--arm", "incumbent", "--model", "vendor/fake",
        "--testset", str(broken), "--gt", str(GT_PATH), "--out", str(env / "runs"),
        "--repeats", "1",
    ])

    assert code == EXIT_REFUSED
    assert client.calls == []
    err = capsys.readouterr().err
    assert "RET-01" in err and "verbatim substring" in err


def test_the_two_ground_truths_are_reconciled_before_any_call(
    env, testset, monkeypatch, capsys
):
    """`_gt_disagreements` runs in the PREFLIGHT, not only in `check`.

    `check` is a command a caller can skip, and this is the only check that ever puts
    the item's own `gt` beside the ground-truth CSV. `flatten._gt_skeleton` builds
    failure rows from the item while `metrics.*` scores against the CSV, so a
    disagreement moves failure rows off the merge key -- on the arm that failed, which
    is exactly where a lower number surprises nobody.

    Asserted here rather than trusted because the guard used to live in `cmd_check`
    alone: the run path called `validate(testset)` and nothing else, and `validate`
    checks an item against itself without ever opening the CSV.
    """
    tampered = env / "drifted.gt.csv"
    tampered.write_text(
        GT_PATH.read_text(encoding="utf-8").replace(
            "5001,0810000001,postpaid,save,network,,",
            "5001,0810000001,postpaid,churn,network,,",
        ),
        encoding="utf-8",
        newline="\n",
    )

    client = FakeClient(lambda i, n: pytest.fail("no call should be made"), testset=testset)
    monkeypatch.setattr(cli, "build_client", lambda api_key, timeout=120.0: client)

    code = main([
        "baseline", "--arm", "incumbent", "--model", "vendor/fake",
        "--testset", str(TESTSET_PATH), "--gt", str(tampered),
        "--out", str(env / "runs"), "--repeats", "1",
    ])

    assert code == EXIT_REFUSED
    assert client.calls == [], "the refusal must land before the first request is paid for"
    err = capsys.readouterr().err
    assert "RET-01" in err and "disagree" in err


# =================================================================== the whole chain


def test_a_perfect_arm_passes_every_mechanism_end_to_end(run_arm, perfect, capsys):
    """The floor. Fake completions carrying each item's own ground truth must survive
    the entire chain -- classify, flatten to product grain, normalise, score, group by
    family -- and come out PASS on all five mechanisms.

    If this fails, the pipeline is losing or reshaping rows somewhere between the model
    and the verdict, and every number the harness prints describes a different run from
    the one that happened.
    """
    incumbent = run_arm("incumbent", perfect)
    candidate = run_arm("candidate", perfect)
    capsys.readouterr()

    code = main(["compare", "--incumbent", str(incumbent), "--candidate", str(candidate)])
    report = capsys.readouterr().out

    assert code == EXIT_OK, "two perfect arms have no FAIL and no FLAKY"
    assert "1. MECHANISM TABLE" in report
    for mechanism in ("clear", "thai_linguistic", "tiebreak", "multislot", "escape"):
        assert mechanism_row(report, mechanism) == ["PASS", "PASS"]
    assert "N_flip = 0" in report
    assert "RECONCILED: NO" in report and "RECONCILED: YES" not in report
    assert "REPLICATE 1" in report, (
        "the report must say which replicate the aggregate table was scored on; "
        "otherwise a reader assumes it covers the whole run"
    )


def test_a_broken_candidate_fails_exactly_its_own_mechanism(
    run_arm, perfect, testset, capsys
):
    """One item wrong on every replicate is FAIL, and it is FAIL for its mechanism only.

    RET-10 is the speaker-attribution item: three save-shaped phrases are agent
    politeness while the customer asks for the porting code, so a model that flattens
    the turns answers `save` where the truth is `churn`. That is the failure the item
    itself predicts, and the report must quote that prediction verbatim -- "it failed"
    is noise, "it failed the way we said" is a finding.
    """
    by_id = {item.item_id: item for item in testset.items}

    def broken(item_id, _nth):
        item = by_id[item_id]
        if item_id == "RET-10":
            return answer(item, payload=payload_for(item, call_result="save"))
        return answer(item)

    incumbent = run_arm("incumbent", perfect)
    candidate = run_arm("candidate", broken)
    capsys.readouterr()

    code = main(["compare", "--incumbent", str(incumbent), "--candidate", str(candidate)])
    report = capsys.readouterr().out

    assert code == EXIT_PROBLEMS, "a FAIL verdict is a problem, not a clean exit"
    assert mechanism_row(report, "thai_linguistic") == ["PASS", "FAIL"]
    assert mechanism_row(report, "clear") == ["PASS", "PASS"], (
        "only the mechanism holding the broken item may change verdict"
    )
    assert "wrong on every replicate: RET-10" in report
    assert "ย้ายค่าย" in report, "the item's own predicted failure, quoted verbatim"


def test_an_item_that_flips_between_replicates_is_flaky_not_pass(
    run_arm, perfect, testset, capsys
):
    """The verdict a single-replicate harness cannot produce, and the reason replicates
    are paid for at all. Majority-voting 2/3 into PASS would delete exactly the
    nondeterminism the replicates were run to find."""
    by_id = {item.item_id: item for item in testset.items}

    def flipping(item_id, nth):
        item = by_id[item_id]
        if item_id == "RET-03" and nth == 2:
            return answer(item, payload=payload_for(item, call_result="churn"))
        return answer(item)

    incumbent = run_arm("incumbent", perfect, repeats=3)
    candidate = run_arm("candidate", flipping, repeats=3)
    capsys.readouterr()

    code = main(["compare", "--incumbent", str(incumbent), "--candidate", str(candidate)])
    report = capsys.readouterr().out

    assert code == EXIT_PROBLEMS
    assert mechanism_row(report, "clear") == ["PASS", "FLAKY"]
    assert "RET-03 (2/3)" in report
    assert "incumbent    N_flip = 0" in report, "the incumbent was stable"
    assert "candidate    N_flip = 1" in report, (
        "one cell moved -- call_result on RET-03's single row. Counting rows instead of "
        "cells would report the same number here and the wrong one on a row whose "
        "outcome and reasons both moved."
    )
    assert mechanism_row(report, "thai_linguistic") == ["PASS", "PASS"], (
        "a flip in one mechanism must not spread to another"
    )


def test_every_item_emits_rows_on_every_replicate_including_dead_ones(
    run_arm, testset, capsys
):
    """An arm whose transport failures vanish has an easier denominator than the arm
    whose network held up. The chain must carry a dead request all the way to a scored
    row: 22 records per replicate, the same count a perfect arm produces."""
    def dead(_item_id, _nth):
        raise FakeTransportError("APITimeoutError: timed out")

    directory = run_arm("dead", dead, repeats=2)
    capsys.readouterr()

    loaded = cli.load_run(directory)
    assert loaded.result.outcome_counts() == {"transport_error": 40}

    per_replicate = cli.replicate_records(loaded.result, testset.items)
    assert [len(rows) for rows in per_replicate] == [22, 22], (
        "22 ground-truth rows over 20 items, on every replicate, including the ones "
        "whose HTTP call never returned (flatten.py, 'Failure rows keep the grain')"
    )
    assert all(not record.parse_ok for rows in per_replicate for record in rows)


def test_ret16_is_three_scored_rows_not_one(run_arm, perfect, testset, capsys):
    """The grain change, asserted at the CLI level rather than in flatten's own unit
    test. One call, three products, three rows on the merge key
    `(call_id, phone_number, product)` (fact_checker.py:1075). An implementation that
    emitted one row per call would lose two thirds of this item and still print a
    complete-looking table."""
    directory = run_arm("incumbent", perfect, repeats=1)
    capsys.readouterr()

    loaded = cli.load_run(directory)
    (rows,) = cli.replicate_records(loaded.result, testset.items)
    ret16 = [record for record in rows if record.call_id == "5016"]

    assert len(ret16) == 3
    assert sorted(r.product for r in ret16) == ["postpaid", "tol", "tvs"]
    assert sorted(r.call_result for r in ret16) == ["churn", "save", "unknown"]
    assert len(rows) == 22


def test_two_product_keys_that_fold_together_are_refused_not_silently_dropped(
    run_arm, testset, capsys
):
    """More rows than merge keys is a grain error, and the scorer cannot report it.

    `flatten.to_rows` deliberately does not lowercase -- `records.from_row` -> `norm_text`
    folds case once, and two normalisation sites is one too many. The cost of that
    correct decision is that a payload naming both `Postpaid` and `POSTPAID` emits two
    rows that collapse to ONE merge key, and `metrics.outer_join` builds
    `{r.key: r for r in pred}` (metrics.py:122) keeping whichever came LAST. The
    discarded prediction is counted nowhere in `Coverage`: the denominator does not
    move, `parse_failures` does not move, and the surviving `call_result` is an
    accident of dict order.

    `replicate_records` is the one seam where `len(rows)` and `len({record.key})` are
    both in scope, so it is the only place the loss can be turned into a number. It is
    a refusal rather than a warning, and it is affordable: the run log is already on
    disk, so nothing paid for is lost.
    """
    by_id = {item.item_id: item for item in testset.items}

    def duplicate_key(item_id, _nth):
        item = by_id[item_id]
        if item_id != "RET-01":
            return answer(item)
        return answer(item, payload={
            "product": {
                "Postpaid": {
                    "main": {"reason": "network", "keyword": ""},
                    "secondary": {"reason": "", "keyword": ""},
                    "third": {"reason": "", "keyword": ""},
                    "retention_outcome": "save",
                },
                # Same product after norm_text, different key in a legal JSON object.
                "POSTPAID": {
                    "main": {"reason": "other", "keyword": ""},
                    "secondary": {"reason": "", "keyword": ""},
                    "third": {"reason": "", "keyword": ""},
                    "retention_outcome": "churn",
                },
            },
            "call_event_detection": "x",
            "recommendation": "",
        })

    directory = run_arm("collider", duplicate_key, repeats=1)
    capsys.readouterr()
    loaded = cli.load_run(directory)

    with pytest.raises(cli.CliError) as excinfo:
        cli.replicate_records(loaded.result, testset.items)

    message = str(excinfo.value)
    assert "RET-01" in message
    assert "2 row(s)" in message and "1 distinct merge key" in message
    # Both spellings, because the folded one alone prints as an impossible duplicate
    # and does not tell the reader which key the model actually wrote.
    assert "'Postpaid'" in message and "'POSTPAID'" in message
    assert "['postpaid', 'postpaid']" in message


def test_an_arm_that_named_no_product_is_counted_where_coverage_cannot_see_it(
    run_arm, testset, capsys
):
    """`answered_nothing` is the counter for the route `Coverage.parse_failures` misses.

    A response that parsed, satisfied every required key and still named no product
    takes the ground-truth skeleton with `parse_ok=True`. It therefore scores a product
    true positive per ground-truth product while `parse_failures` reads ZERO -- an arm
    that answered nothing is arithmetically indistinguishable from a perfect one on the
    product dimension alone (`flatten.py`, KNOWN CONSEQUENCE).

    So the run must show it somewhere. Asserted end to end, through the real run loop
    and `arm_summary`, rather than only against `flatten.named_no_product`: the counter
    is worthless if the CLI forgets to call it.
    """
    empty = {"product": {}, "call_event_detection": "x", "recommendation": ""}
    directory = run_arm(
        "mute", lambda item_id, _nth: answer(None, payload=empty), repeats=1
    )
    capsys.readouterr()

    loaded = cli.load_run(directory)
    assert loaded.result.outcome_counts() == {"ok": 20}, (
        "every response parsed and carried all three required keys, so classify is "
        "right to call it ok -- which is exactly why parse_failures cannot see it"
    )

    gt = cli._load_gt(GT_PATH)
    per_replicate = cli.replicate_records(loaded.result, testset.items)
    summary = cli.arm_summary(loaded, gt, per_replicate)

    assert summary.answered_nothing == 20, (
        "one per item. If this reads 0 the report prints an arm that named nothing as "
        "though it had answered."
    )
    product = summary.dimensions["product"]
    assert product.weighted("recall") == 1.0, "the unearned credit, still unearned"
    assert product.coverage.parse_failures == 0, (
        "and still invisible in Coverage -- which is the whole reason "
        "answered_nothing exists"
    )
    assert summary.dimensions["call_result"].weighted("recall") == 0.0


# ====================================================== the per-call cost accounting
#
# `run.jsonl` has carried tokens, cost and latency per call since the runner was
# written and the xlsx export prints them, but the TEXT report showed none of it. These
# tests run the real chain -- fake client, real run loop, real log, real `arm_summary`
# -- because the arithmetic is trivial and the join is not: the figures have to come
# off the SAME rows the report's other numbers describe, and the cost-per-correct ratio
# has to divide one replicate's cost by that replicate's hit count rather than a whole
# run's bill by one replicate's answers.


def test_the_run_totals_come_off_every_row_of_the_run(run_arm, perfect, testset, capsys):
    """20 items x 2 replicates, at the fake's own per-call usage. Every column is the
    fake's constant times 40, so a total computed over one replicate, over the parsed
    rows only, or over the items rather than the calls each lands on a different
    number."""
    directory = run_arm("incumbent", perfect, repeats=2)
    capsys.readouterr()

    loaded = cli.load_run(directory)
    gt = cli._load_gt(GT_PATH)
    summary = cli.arm_summary(loaded, gt, cli.replicate_records(loaded.result, testset.items))

    assert summary.calls == 40, "20 items x 2 replicates, dead rows included"
    assert summary.prompt_tokens == 40 * 1200
    assert summary.completion_tokens == 40 * 180
    assert summary.reasoning_tokens == 0
    assert summary.cost_usd_lower_bound == pytest.approx(40 * 0.0004)
    assert summary.calls_without_cost == 0
    assert summary.latency_median_s == pytest.approx(0.01)
    assert summary.latency_max_s == pytest.approx(0.01)


def test_a_row_that_reported_no_cost_is_counted_rather_than_counted_as_free(
    run_arm, testset, by_id, capsys
):
    """`usage.cost` is a provider-dependent extension. A missing one is skipped by
    `runner.total_cost()` rather than summed as zero (runner.py:411-421), so the total
    is a floor -- and the count of the rows it could not see is what tells a reader how
    much of the run the floor covers. Summed as zero instead, an arm whose provider
    reports nothing prints as the cheapest one in the comparison."""
    def sometimes_costed(item_id, _nth):
        return answer(by_id[item_id], cost=None if item_id in {"RET-02", "RET-05"} else 0.0004)

    directory = run_arm("incumbent", sometimes_costed, repeats=2)
    capsys.readouterr()

    loaded = cli.load_run(directory)
    gt = cli._load_gt(GT_PATH)
    summary = cli.arm_summary(loaded, gt, cli.replicate_records(loaded.result, testset.items))

    assert summary.calls_without_cost == 4, "two items x two replicates reported nothing"
    assert summary.cost_usd_lower_bound == pytest.approx(36 * 0.0004), (
        "the four uncosted calls are absent from the total, not counted as 0.00"
    )


def test_cost_per_correct_answer_divides_one_replicate_by_that_same_replicate(
    run_arm, perfect, testset, capsys
):
    """The alignment that makes the ratio mean anything.

    `dimensions` is scored on one replicate, so the numerator has to be that replicate's
    cost. Using the whole run's bill would report `repeats` times the cost of an answer
    -- 2x here, 3x at the default -- against a production path that makes ONE call per
    item. The two numbers are deliberately different in this test (0.008 against 0.016)
    so an implementation that reached for `total_cost()` fails rather than agreeing by
    coincidence.

    22 correct: the pack's 22 ground-truth rows all carry a call_result, and a perfect
    arm gets all of them. That count is the scorer's own true-positive total, not a new
    notion of correctness invented for a cost table.
    """
    directory = run_arm("incumbent", perfect, repeats=2)
    capsys.readouterr()

    loaded = cli.load_run(directory)
    gt = cli._load_gt(GT_PATH)
    summary = cli.arm_summary(loaded, gt, cli.replicate_records(loaded.result, testset.items))

    assert summary.cost_per_correct_dimension == "call_result"
    assert summary.correct_answers == 22
    assert summary.scored_replicate_cost_usd == pytest.approx(20 * 0.0004)
    assert summary.cost_usd_lower_bound == pytest.approx(40 * 0.0004), (
        "the run total covers both replicates; the ratio's numerator must not"
    )
    assert summary.cost_per_correct_usd == pytest.approx(20 * 0.0004 / 22)


def test_the_scored_replicate_is_read_off_the_log_not_assumed_to_be_one(
    run_arm, perfect, testset, capsys
):
    """`replicate_records` returns replicates in SORTED order and `dimensions` is scored
    on index 0, which is the lowest replicate number present -- 1 in every log this CLI
    writes, and not necessarily 1 in a log someone filtered by hand.

    Taking the minimum is what makes the numerator select the same calls the denominator
    scored by construction. Hardcoding 1 against this log would sum the cost of nothing
    and print 0.000000 USD per correct answer, which reads as a free model.
    """
    directory = run_arm("incumbent", perfect, repeats=2)
    capsys.readouterr()

    log = directory / "run.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").split("\n") if line.strip()]
    for row in rows:
        row["replicate"] += 1                      # the log now starts at replicate 2
        row["cost"] = 0.001 if row["replicate"] == 2 else 0.002
    log.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8", newline="\n",
    )

    loaded = cli.load_run(directory)
    gt = cli._load_gt(GT_PATH)
    summary = cli.arm_summary(loaded, gt, cli.replicate_records(loaded.result, testset.items))

    assert summary.scored_replicate_cost_usd == pytest.approx(20 * 0.001), (
        "the numerator must be replicate 2 -- the one that was scored -- not an empty "
        "selection of a replicate 1 that no longer exists"
    )
    assert summary.cost_per_correct_usd == pytest.approx(20 * 0.001 / 22)


def test_an_arm_whose_provider_reported_no_cost_gets_no_ratio_at_all(
    run_arm, testset, by_id, capsys
):
    """The whole run finishes with nothing to total. 0.000000 USD per correct answer
    would read as a free model; the numerator is UNKNOWN, which is a different fact and
    the one the report has to print."""
    directory = run_arm(
        "incumbent", lambda item_id, _nth: answer(by_id[item_id], cost=None), repeats=1
    )
    capsys.readouterr()

    loaded = cli.load_run(directory)
    gt = cli._load_gt(GT_PATH)
    summary = cli.arm_summary(loaded, gt, cli.replicate_records(loaded.result, testset.items))

    assert summary.calls_without_cost == 20 and summary.calls == 20
    assert summary.cost_usd_lower_bound == 0.0
    assert summary.correct_answers == 22, "the arm was perfect; only the billing is absent"
    assert summary.cost_per_correct_usd is None, (
        "a zero numerator over 22 correct answers is 0.000000 USD per correct answer, "
        "which is a claim that the model was free rather than that it was unmeasured"
    )


def test_latency_counts_the_calls_that_died(run_arm, testset, by_id, capsys):
    """A median over the successes alone improves as the failures get worse, which is
    the wrong direction for the one number whose job is to say what an arm cost in wall
    clock. A 120s timeout is 120 seconds that were spent."""
    def one_dead_item(item_id, _nth):
        if item_id == "RET-04":
            raise FakeTransportError("APITimeoutError: timed out", latency_s=120.0)
        return answer(by_id[item_id], latency_s=0.5)

    directory = run_arm("incumbent", one_dead_item, repeats=1)
    capsys.readouterr()

    loaded = cli.load_run(directory)
    gt = cli._load_gt(GT_PATH)
    summary = cli.arm_summary(loaded, gt, cli.replicate_records(loaded.result, testset.items))

    assert summary.calls == 20, "the dead item is still one row, and still one call"
    assert summary.latency_max_s == pytest.approx(120.0), (
        "the timeout is in the maximum. Excluding failures would report this arm as the "
        "fast one"
    )
    assert summary.latency_median_s == pytest.approx(0.5)
    assert summary.calls_without_cost == 1, "nothing was billed for a request that failed"


def test_compare_prints_the_cost_section_end_to_end(run_arm, perfect, by_id, capsys):
    """The counter is worthless if the report forgets to print it. Asserted through the
    real `compare` path rather than against `render` alone, which is the same argument
    `answered_nothing` is tested on."""
    def pricier(item_id, _nth):
        return answer(by_id[item_id], prompt_tokens=2600, completion_tokens=1450,
                      reasoning_tokens=1100, cost=0.0031, latency_s=4.2)

    incumbent = run_arm("incumbent", perfect, repeats=1)
    candidate = run_arm("candidate", pricier, repeats=1)
    capsys.readouterr()

    assert main(["compare", "--incumbent", str(incumbent), "--candidate", str(candidate)]) == EXIT_OK
    report = capsys.readouterr().out
    section = report.split("6. COST, TOKENS AND LATENCY")[1]

    assert "LOWER BOUND" in section
    assert str(20 * 1200) in section and str(20 * 2600) in section
    assert "0.008000" in section and "0.062000" in section
    assert "correct call_result rows" in section, "the ratio names the dimension it used"
    assert "REPLICATE 1" in report, (
        "and the footer still says which replicate the scored half of that ratio came "
        "from"
    )


# =========================================================== the comparison refusals


def test_two_runs_in_the_same_second_do_not_overwrite_each_other(tmp_path):
    """Second-resolution timestamps collide the moment a script runs two arms back to
    back, which is the normal case. With a reused directory the second run's log
    overwrites the first, both `compare` arguments resolve to one directory, and the
    report prints a model compared against itself in perfect agreement.

    Found by `test_compare_refuses_two_arms_with_the_same_name`, which could not create
    two distinct runs. Pinned here so the fix is a property rather than a coincidence.
    """
    stamp = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)
    first = cli.new_run_dir(tmp_path, "incumbent", now=stamp)
    second = cli.new_run_dir(tmp_path, "incumbent", now=stamp)

    assert first != second
    assert first.name == "20260804-120000Z-incumbent"
    assert second.name == "20260804-120000Z-incumbent-2"
    assert sorted((first, second)) == [first, second], (
        "suffixes must keep runs in the order they happened"
    )


def test_compare_refuses_two_arms_with_the_same_name(run_arm, perfect, capsys):
    """`report.render` keys its mechanism tables by arm name, so two runs both called
    `incumbent` collapse into one dict entry and the paired report silently compares an
    arm with itself -- printing identical columns that read as perfect agreement."""
    first = run_arm("incumbent", perfect, repeats=1)
    second = run_arm("incumbent", perfect, repeats=1)
    capsys.readouterr()

    assert first != second, "the two runs must be distinct directories"
    code = main(["compare", "--incumbent", str(first), "--candidate", str(second)])

    assert code == EXIT_REFUSED
    err = capsys.readouterr().err
    assert "both runs are named 'incumbent'" in err
    assert "--arm" in err, "the refusal must say how to fix it"


def test_compare_refuses_a_testset_that_changed_since_the_run(env, run_arm, perfect, capsys):
    """Scoring against an edited testset compares answers with labels they never saw.
    The sha in run.json is what makes that detectable at all."""
    incumbent = run_arm("incumbent", perfect, repeats=1)
    candidate = run_arm("candidate", perfect, repeats=1)
    capsys.readouterr()

    edited = env / "edited.jsonl"
    lines = [
        line for line in TESTSET_PATH.read_text(encoding="utf-8").split("\n") if line.strip()
    ]
    edited.write_text("\n".join(lines[:19]) + "\n", encoding="utf-8", newline="\n")

    code = main([
        "compare", "--incumbent", str(incumbent), "--candidate", str(candidate),
        "--testset", str(edited),
    ])

    assert code == EXIT_REFUSED
    assert "has changed since the run" in capsys.readouterr().err


def test_compare_refuses_arms_scored_by_different_harness_versions(
    run_arm, perfect, capsys
):
    """`manifest.assert_comparable` blocks on `scorer_sha`. Two arms scored by
    different versions of this code are not a paired comparison, however similar the
    numbers look."""
    incumbent = run_arm("incumbent", perfect, repeats=1)
    candidate = run_arm("candidate", perfect, repeats=1)
    capsys.readouterr()

    meta_path = candidate / "run.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["scorer_sha"] = "deadbee"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8", newline="\n")

    code = main(["compare", "--incumbent", str(incumbent), "--candidate", str(candidate)])

    assert code == EXIT_REFUSED
    assert "scorer_sha" in capsys.readouterr().err


def test_compare_refuses_without_the_hmac_key(run_arm, perfect, monkeypatch, capsys):
    """The regression list is HMAC-keyed. With no key it cannot be computed, and an
    empty list prints as 'none.' -- which reads as 'the candidate lost nothing' rather
    than 'this was never checked'."""
    incumbent = run_arm("incumbent", perfect, repeats=1)
    candidate = run_arm("candidate", perfect, repeats=1)
    capsys.readouterr()

    monkeypatch.delenv("EVAL_HARNESS_KEY_HMAC")
    code = main(["compare", "--incumbent", str(incumbent), "--candidate", str(candidate)])

    assert code == EXIT_REFUSED
    assert "EVAL_HARNESS_KEY_HMAC" in capsys.readouterr().err


# ================================================================= the run log itself


def test_the_run_log_round_trips_without_losing_a_field(run_arm, perfect, testset, capsys):
    """`compare` reads runs off disk, so a field dropped by the log is a field the
    report silently stops knowing. Reconstructing `ItemResult` rather than inventing a
    second row type is what makes this checkable in one assertion."""
    directory = run_arm("incumbent", perfect, repeats=2)
    capsys.readouterr()

    loaded = cli.load_run(directory)
    assert len(loaded.result.results) == 40
    assert [r.item_id for r in loaded.result.results] == [
        item.item_id for item in testset.items for _ in range(2)
    ], "testset order, replicate-major within each item"
    assert [r.replicate for r in loaded.result.results[:2]] == [1, 2]
    assert loaded.result.observed_models() == {"vendor/fake-model": 40}
    assert loaded.result.config.repeats == 2
    assert loaded.result.prompt_sha == loaded.meta["prompt_sha"]
    row = loaded.result.results[0]
    assert row.parse_ok is True and row.payload is not None and row.repairs == ()


def test_an_incomplete_run_directory_is_refused_by_name(run_arm, perfect, capsys):
    """A run directory is something a person can edit, move or truncate. Naming the
    missing field beats a KeyError traceback, which teaches the reader that the tool
    crashes rather than that their run.json is incomplete."""
    directory = run_arm("incumbent", perfect, repeats=1)
    capsys.readouterr()

    meta_path = directory / "run.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    del meta["decoding"]
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8", newline="\n")

    with pytest.raises(cli.CliError) as exc:
        cli.load_run(directory)
    assert "decoding" in str(exc.value)


def test_a_run_log_naming_an_item_the_testset_does_not_have_is_refused(
    run_arm, perfect, testset, capsys
):
    """Silently skipping an unknown item would score a model's answers against labels
    for a different set of items and report the coverage as complete."""
    directory = run_arm("incumbent", perfect, repeats=1)
    capsys.readouterr()

    log = directory / "run.jsonl"
    lines = [line for line in log.read_text(encoding="utf-8").split("\n") if line.strip()]
    first = json.loads(lines[0])
    first["item_id"] = "RET-99"
    lines[0] = json.dumps(first, ensure_ascii=False)
    log.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    loaded = cli.load_run(directory)
    with pytest.raises(cli.CliError) as exc:
        cli.replicate_records(loaded.result, testset.items)
    assert "RET-99" in str(exc.value)


# ======================================================================== stability


def test_stability_probe_runs_the_named_items_only(env, testset, monkeypatch, capsys):
    """The N_flip probe. The subset is written to its own file so `testset_sha`
    describes the three items that ran, not the twenty that did not -- provenance that
    claims twenty is worse than none.

    Item and replicate counts are asserted from the fake's own call list, because a
    probe that quietly ran the whole pack would still print a plausible N_flip and
    would cost seven times what it said it would.
    """
    by_id = {item.item_id: item for item in testset.items}

    def flipping(item_id, nth):
        item = by_id[item_id]
        if item_id == "RET-11" and nth in (2, 4):
            return answer(item, payload=payload_for(item, call_result="save"))
        return answer(item)

    client = FakeClient(flipping, testset=testset)
    monkeypatch.setattr(cli, "build_client", lambda api_key, timeout=120.0: client)

    code = main([
        "stability", "--model", "vendor/fake-model", "--items", "RET-01,RET-11,RET-16",
        "--repeats", "5", "--out", str(env / "runs"),
        "--testset", str(TESTSET_PATH), "--gt", str(GT_PATH),
    ])
    out = capsys.readouterr().out

    assert code == EXIT_OK
    assert set(client.calls) == {"RET-01", "RET-11", "RET-16"}
    assert len(client.calls) == 15, "3 items x 5 replicates, and nothing else"
    assert "N_flip = 1" in out, (
        "RET-11's call_result took two values across the five replicates: one cell"
    )
    assert "RET-11" in out and "FLAKY" in out
    assert "RET-01     PASS" in out

    directory = next(p for p in (env / "runs").iterdir() if p.is_dir())
    subset = load_testset(directory / "testset.jsonl")
    assert [item.item_id for item in subset.items] == ["RET-01", "RET-11", "RET-16"], (
        "the subset keeps TESTSET order, not the order given on the command line"
    )
    meta = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    assert meta["items"] == 3
    assert meta["testset_sha"] == cli.testset_sha(directory / "testset.jsonl"), (
        "the recorded sha must describe the 3-item file that ran, not the 20-item pack"
    )


def test_stability_refuses_an_item_id_that_does_not_exist(env, capsys):
    code = main([
        "stability", "--model", "vendor/fake-model", "--items", "RET-01,RET-99",
        "--out", str(env / "runs"),
    ])
    assert code == EXIT_REFUSED
    assert "RET-99" in capsys.readouterr().err


# ========================================================== pinning the backend
#
# MEASURED 2026-08-04: a 60-call `qwen/qwen3.6-27b` run was served by TWO backends
# under one model id. `observed_models` showed `60 x qwen/qwen3.6-27b` throughout,
# because `response.model` is the model id and the model id never changed. What did
# change was the tokenizer: 14 of 20 items returned two distinct `prompt_tokens`
# values for a byte-identical request (the incumbent returned 0 of 20).


@pytest.fixture
def by_id(testset):
    """item_id -> TestItem. `FakeClient` hands a responder the id; `answer` wants the
    item, because it builds the payload out of that item's own ground truth."""
    return {item.item_id: item for item in testset.items}


@pytest.fixture
def pinned_run(env, testset, monkeypatch):
    """Drive `main` with an explicit argv and hand back the run directory AND the fake.

    Separate from `run_arm` rather than a widening of it: these tests assert on what
    reached `complete` (the pin) as well as on what was written, and `run_arm`'s
    signature fixes the argv. Two small fixtures beat one that takes a flag deciding
    which half of itself to run.
    """
    def _run(extra_argv, responder):
        client = FakeClient(responder, testset=testset)
        monkeypatch.setattr(cli, "build_client", lambda api_key, timeout=120.0: client)
        out = env / "runs"
        before = set(out.iterdir()) if out.exists() else set()
        code = main([
            "baseline", "--testset", str(TESTSET_PATH), "--gt", str(GT_PATH),
            "--out", str(out), "--concurrency", "4", *extra_argv,
        ])
        assert code == EXIT_OK
        created = sorted(set(out.iterdir()) - before)
        assert len(created) == 1, f"expected one new run directory, got {created}"
        return created[0], client

    return _run


def test_the_pinned_dry_run_body_carries_all_three_routing_keys(monkeypatch, tmp_path):
    """`order` alone is a preference, not a pin.

    Each of the three keys is load-bearing and the missing one fails differently:
    without `order` there is no pin at all; without `allow_fallbacks: false` OpenRouter
    falls through to another eligible endpoint whenever the named one is busy, which is
    indistinguishable in the log from not falling through; without `require_parameters`
    an endpoint that cannot honour the schema is served anyway with the schema dropped.

    Asserted on the whole dict rather than key by key, so a fourth key appearing -- or
    `allow_fallbacks` flipping to true -- fails here rather than in a run nobody
    re-reads.
    """
    monkeypatch.setattr(cli, "build_client", lambda *a, **k: pytest.fail("no calls"))
    main([
        "baseline", "--arm", "candidate", "--model", "qwen/qwen3.6-27b",
        "--provider", "CoreWeave", "--out", str(tmp_path), "--dry-run",
    ])
    directory = next(p for p in tmp_path.iterdir() if p.is_dir())
    body = json.loads((directory / "requests.jsonl").read_text(encoding="utf-8").split("\n")[0])

    assert body["request"]["extra_body"] == {
        "usage": {"include": True},
        "provider": {
            "order": ["CoreWeave"],
            "allow_fallbacks": False,
            "require_parameters": True,
        },
    }


def test_an_unpinned_dry_run_body_is_unchanged(monkeypatch, tmp_path):
    """Adding the pin must not narrow routing on runs that did not ask for it.

    A `provider.order` that appeared by default would silently change which backend
    every existing command routes to, which is a different arm measured under the old
    arm's name.
    """
    monkeypatch.setattr(cli, "build_client", lambda *a, **k: pytest.fail("no calls"))
    main([
        "baseline", "--arm", "incumbent", "--model", "google/gemini-2.5-flash",
        "--out", str(tmp_path), "--dry-run",
    ])
    directory = next(p for p in tmp_path.iterdir() if p.is_dir())
    body = json.loads((directory / "requests.jsonl").read_text(encoding="utf-8").split("\n")[0])

    assert body["request"]["extra_body"] == {
        "usage": {"include": True},
        "provider": {"require_parameters": True},
    }


def test_the_pin_is_sent_to_the_client_and_recorded_in_run_json(pinned_run, by_id):
    """Both halves. A pin that is sent but not recorded leaves a run nobody can
    attribute; a pin that is recorded but not sent is a run.json asserting something
    false about a run that was not pinned at all."""
    directory, client = pinned_run(
        ["--arm", "candidate", "--model", "qwen/qwen3.6-27b",
         "--provider", "CoreWeave", "--repeats", "1"],
        lambda item_id, nth: answer(by_id[item_id], provider="CoreWeave"),
    )

    assert {call["provider"] for call in client.requests} == {"CoreWeave"}
    meta = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    assert meta["provider_requested"] == "CoreWeave"
    assert meta["observed_providers"] == {"CoreWeave": 20}


def test_an_unpinned_run_records_a_null_provider_rather_than_no_key(pinned_run, by_id):
    """"Was this pinned?" must be answerable from run.json alone. A missing key leaves
    it answerable only from the file's age, and the answer decides whether the arm is
    one system or a blend of two."""
    directory, _ = pinned_run(
        ["--arm", "incumbent", "--model", "vendor/fake-model", "--repeats", "1"],
        lambda item_id, nth: answer(by_id[item_id]),
    )

    meta = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    assert "provider_requested" in meta and meta["provider_requested"] is None


def test_run_json_records_the_token_fingerprint_that_proves_the_pin(pinned_run, by_id, capsys):
    """The proof a pin held is `prompt_tokens` uniformity, not the `provider` echo.

    The fake reports two token counts for one item across replicates -- the shape of
    the real defect -- while reporting a single, correct-looking `provider` on every
    call. The provider histogram is therefore clean and the fingerprint is not, which
    is exactly the case a `provider`-only check would pass.
    """
    def responder(item_id, nth):
        if item_id == "RET-01":
            return answer(by_id[item_id], provider="CoreWeave",
                          prompt_tokens=2587 if nth == 1 else 3691)
        return answer(by_id[item_id], provider="CoreWeave", prompt_tokens=2600)

    directory, _ = pinned_run(
        ["--arm", "candidate", "--model", "qwen/qwen3.6-27b",
         "--provider", "CoreWeave", "--repeats", "2"],
        responder,
    )

    meta = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    assert meta["observed_providers"] == {"CoreWeave": 40}, (
        "the provider field is clean, which is the point: it is the router describing "
        "its own routing"
    )
    assert meta["split_items"] == {"RET-01": [2587, 3691]}
    assert meta["prompt_token_spread"]["RET-02"] == [2600]

    out = capsys.readouterr().out
    assert "19/20 items returned exactly one value" in out
    assert "FAILED" in out and "RET-01" in out


def test_a_clean_pin_prints_the_passing_fingerprint(pinned_run, by_id, capsys):
    """The passing shape, so the failing one above is not the only thing asserted."""
    pinned_run(
        ["--arm", "candidate", "--model", "qwen/qwen3.6-27b",
         "--provider", "CoreWeave", "--repeats", "2"],
        lambda item_id, nth: answer(by_id[item_id], provider="CoreWeave", prompt_tokens=2600),
    )

    out = capsys.readouterr().out
    assert "20/20 items returned exactly one value" in out
    assert "one tokenizer answered" in out


def test_the_new_fields_round_trip_through_the_run_log(pinned_run, by_id):
    """Written, then read back into the same dataclass. `LoadedRun` claims the round
    trip is lossless, and these four fields are the ones it was silently not."""
    directory, _ = pinned_run(
        ["--arm", "candidate", "--model", "qwen/qwen3.6-27b",
         "--provider", "CoreWeave", "--repeats", "1"],
        lambda item_id, nth: answer(
            by_id[item_id], provider="CoreWeave", generation_id="gen-xyz",
            finish_reason="stop",
        ),
    )

    loaded = cli.load_run(directory)
    row = loaded.result.results[0]
    assert row.provider == "CoreWeave"
    assert row.generation_id == "gen-xyz"
    assert row.finish_reason == "stop"
    assert json.loads(row.raw_content)["product"], "the model's own text came back"
    assert loaded.result.config.provider == "CoreWeave"


def test_a_run_log_written_before_these_fields_existed_still_loads(pinned_run, by_id):
    """The two 2026-08-04 runs are real runs and must stay readable.

    The fix for "the schema violations were undiagnosable" must not begin by making
    the runs that contain them unloadable. Only the four new keys are tolerated as
    absent; a log missing a field that has always been written is still refused, which
    the second half asserts.
    """
    directory, _ = pinned_run(
        ["--arm", "incumbent", "--model", "vendor/fake-model", "--repeats", "1"],
        lambda item_id, nth: answer(by_id[item_id]),
    )
    log = directory / "run.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").split("\n") if line.strip()]
    for row in rows:
        for key in ("provider", "provider_requested", "generation_id", "finish_reason",
                    "raw_content"):
            row.pop(key)
    log.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8", newline="\n",
    )
    meta_path = directory / "run.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    for key in ("provider_requested", "observed_providers", "prompt_token_spread",
                "split_items"):
        meta.pop(key)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8", newline="\n")

    loaded = cli.load_run(directory)
    assert len(loaded.result.results) == 20
    assert loaded.result.results[0].provider is None
    assert loaded.result.results[0].raw_content is None
    assert loaded.result.config.provider is None

    rows[0].pop("prompt_tokens")
    log.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8", newline="\n",
    )
    with pytest.raises(cli.CliError):
        cli.load_run(directory)

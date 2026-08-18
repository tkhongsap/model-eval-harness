"""Two controls on score_asr.py that are easy to believe you already have.

PROVENANCE. Every committed report stamps the code that produced it. On 2026-08-17 both
ASR reports stamped `scoring_code_sha256: bed27990c1258617...`, and that value matches the
sha256 of no committed version of `score_asr.py` -- HEAD's is `64667716d387383f...`.
Verification of that round read the mismatch as "generated from uncommitted intermediate
code" and wrote the conclusion into `docs/eval-round-report.md` and
`docs/harness-tightening-plan.md`.

The conclusion was wrong, and finding out why is what these tests are for. `bed27990` is
HEAD's blob with every LF expanded to CRLF: the bytes a Windows checkout puts on disk under
`core.autocrlf`, hashed by a stamp that reads the working tree rather than the object store.
Same content, different rendering, different hash. The reports were honest; the stamp could
not say so. `test_report_identifies_the_code_that_produced_it` therefore accepts exactly the
two renderings git itself can produce for a commit and no others, and it fails -- loudly,
naming the offender -- for a stamp that matches neither.

The genuine defect those reports do carry is the second test: they name no commit and carry
no `dirty` flag, so nothing in either file distinguishes "this is HEAD's content" from "this
hashed the same by coincidence". Both are marked `xfail(strict=True)`. Regenerate them and
the xfail becomes an XPASS that fails the suite, which is the point: the exemption has to be
deleted by whoever fixes it, not left to rot.

PARALLEL SCORING. score_item is O(len(ref) x len(hyp)) pure Python and it runs that DP four
times per item, which put a 138-call arm at about an hour and a half. Slowness is not a
neutral property of a scorer: it is what makes somebody score five items and compare the
result against last week's twenty. The pool is the same function on more cores, and the test
that matters is that the two paths serialise to identical bytes -- including the runaway
refusals, which must still fire before the distance inside a worker exactly as they do here.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import score_asr as S  # noqa: E402

REPORTS = ROOT / "reports"
SCORING_REL = "scripts/score_asr.py"

# Git identity for the throwaway repositories below. Passed with -c rather than written to
# a config so nothing here can depend on, or disturb, the developer's own git identity.
GIT_IDENTITY = ["-c", "user.email=provenance-test@example.invalid",
                "-c", "user.name=provenance test",
                "-c", "commit.gpgsign=false"]


def _git_available() -> bool:
    try:
        S._git(["rev-parse", "--git-dir"], cwd=ROOT)
    except OSError:
        return False
    return True


needs_git = pytest.mark.skipif(
    not _git_available(),
    reason="git cannot answer here, so nothing about commit identity can be checked",
)


# --- the stamp itself -------------------------------------------------------------------


def test_the_two_hashes_disagree_on_line_endings_and_that_is_the_point() -> None:
    """The whole 2026-08-17 confusion, reduced to four lines.

    `sha256` is a property of the checkout. `sha256_lf` is a property of the content. A
    stamp carrying only the first cannot be compared against a stamp taken on another
    platform, and the reader who tries concludes the code has gone missing.
    """
    lf = "def f():\n    return 1\n".encode("utf-8")
    crlf = "def f():\r\n    return 1\r\n".encode("utf-8")

    assert S.file_hashes(lf)["sha256"] != S.file_hashes(crlf)["sha256"], (
        "if these ever agree the incident this test describes cannot happen, and the "
        "sha256_lf field below has stopped earning its place"
    )
    assert S.file_hashes(lf)["sha256_lf"] == S.file_hashes(crlf)["sha256_lf"]
    assert S.file_hashes(lf)["sha256_lf"] == S.file_hashes(lf)["sha256"], (
        "LF content must hash the same both ways, or sha256_lf is not a normalisation but "
        "a second, unrelated number"
    )


def test_the_incident_hash_is_head_rendered_crlf() -> None:
    """Pin the finding itself, so nobody re-derives it from scratch.

    This is not a test of new behaviour. It is the evidence for why the acceptance rule in
    `test_report_identifies_the_code_that_produced_it` admits a CRLF rendering at all: the
    published stamp that "matched nothing" is a commit's own content, rendered the way a
    Windows checkout writes it.
    """
    blob = _blob_at("d568505")
    assert hashlib.sha256(blob).hexdigest().startswith("64667716d387383f")
    crlf = blob.replace(b"\n", b"\r\n")
    assert hashlib.sha256(crlf).hexdigest() == (
        "bed27990c125861715a8c61f35b9feb98f7c3a1a3a6d6f1dbf183d8bd44d57fc"
    ), "the stamp on both 2026-08-17 reports is this commit's content, CRLF-rendered"


@needs_git
def test_stamp_names_a_commit_and_a_boolean_dirty() -> None:
    prov = S.code_provenance()
    assert len(prov["commit"]) == 40 and int(prov["commit"], 16) >= 0
    assert isinstance(prov["dirty"], bool), (
        "dirty must be a boolean when git answered; None is reserved for 'git could not "
        "tell us', and collapsing the two loses the distinction the field exists for"
    )
    assert prov["files"] == list(S.SCORING_PATH)
    # It goes into a JSON report, so it has to survive the trip.
    json.dumps(prov)


def test_git_unavailable_is_recorded_in_words_not_omitted() -> None:
    """The failure mode that would otherwise be invisible.

    A report generated on a machine with no git, if the fields were simply left out, is
    byte-indistinguishable from a report whose provenance block was never wired up. Both
    read as "we did not check", and only one of them is honest about it.
    """
    prov = S.code_provenance(git=f"git-not-installed-{uuid.uuid4().hex}")
    assert set(prov) >= {"sha256", "sha256_lf", "commit", "dirty", "git_status"}
    assert prov["commit"] is None
    assert prov["dirty"] is None, "an unknown tree state is not a clean one"
    assert prov["git_status"].startswith("unavailable: ")
    assert "not found" in prov["git_status"]


# --- clean / dirty / no-commits, driven against a real repository -------------------------
#
# Against the working tree these three states are whatever the developer's tree happens to
# be, which is not a test. A throwaway repository can be walked through all of them.


@pytest.fixture()
def scratch_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    S._git(["init"], cwd=repo)
    return repo


@needs_git
def test_a_repository_with_no_commits_is_reported_not_guessed(scratch_repo: Path) -> None:
    prov = S.code_provenance(("a.py",), repo=scratch_repo)
    assert prov["commit"] is None
    assert prov["dirty"] is None
    assert prov["git_status"].startswith("unavailable: ")
    assert "rev-parse" in prov["git_status"]


@needs_git
def test_clean_then_dirty_then_untracked(scratch_repo: Path) -> None:
    S._git(["add", "a.py"], cwd=scratch_repo)
    S._git([*GIT_IDENTITY, "commit", "-m", "first"], cwd=scratch_repo)

    clean = S.code_provenance(("a.py",), repo=scratch_repo)
    assert clean["dirty"] is False
    assert clean["git_status"] == "clean"
    assert len(clean["commit"]) == 40

    (scratch_repo / "a.py").write_text("x = 2\n", encoding="utf-8")
    dirty = S.code_provenance(("a.py",), repo=scratch_repo)
    assert dirty["dirty"] is True
    assert "a.py" in dirty["git_status"]
    assert dirty["commit"] == clean["commit"], (
        "an edit does not move HEAD -- which is exactly why the commit id alone cannot "
        "say whether the code that ran was ever committed"
    )

    # A module git has never seen is not a clean tree either. `--porcelain` reports it, and
    # the scoring path is only reproducible if every file in it is in the object store.
    (scratch_repo / "b.py").write_text("y = 3\n", encoding="utf-8")
    S._git(["checkout", "--", "a.py"], cwd=scratch_repo)
    untracked = S.code_provenance(("a.py", "b.py"), repo=scratch_repo)
    assert untracked["dirty"] is True
    assert "b.py" in untracked["git_status"]


@needs_git
def test_dirty_covers_the_modules_the_arithmetic_depends_on() -> None:
    """normalise_thai is in asr_common.py and moves every `_norm` figure in the report.

    A dirty flag that only watched score_asr.py would call the tree clean with an
    uncommitted normalisation underneath it -- the same class of failure the whole
    provenance block exists to prevent, one import away.
    """
    assert "asr_common.py" in S.SCORING_PATH
    assert "thai_num.py" in S.SCORING_PATH


# --- the audit of what is already committed -----------------------------------------------


def _blob_at(commit: str) -> bytes:
    proc = subprocess.run(["git", "show", f"{commit}:./{SCORING_REL}"],
                          cwd=str(ROOT), capture_output=True)
    if proc.returncode != 0:
        pytest.fail(f"cannot read {SCORING_REL} at {commit}: "
                    f"{proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout


def _commit_renderings() -> dict[str, str]:
    """sha256 -> a description of which commit and which rendering produced it.

    Two entries per commit, and exactly two, because those are the two byte sequences git
    can put on a disk for one blob: LF as stored, and CRLF as `core.autocrlf` writes it on
    Windows. This is not a tolerance and it is not there to let the 2026-08-17 reports
    through -- it is the actual identity relation between a commit and a working tree, and
    a stamp matching neither rendering has still failed.
    """
    log = S._git(["log", "--follow", "--format=%H", "--", SCORING_REL], cwd=ROOT)
    out: dict[str, str] = {}
    for commit in log.split():
        blob = _blob_at(commit)
        assert b"\r" not in blob, (
            f"the blob at {commit[:7]} already contains CR, so 'the CRLF rendering of this "
            f"commit' is not well defined and this map would be guessing"
        )
        out[hashlib.sha256(blob).hexdigest()] = f"{commit[:7]} (LF, as git stores it)"
        out[hashlib.sha256(blob.replace(b'\n', b'\r\n')).hexdigest()] = (
            f"{commit[:7]} (CRLF, as a Windows checkout writes it)")
    return out


def _stamped_reports() -> list[str]:
    names = []
    for path in sorted(REPORTS.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(doc, dict) and doc.get("scoring_code_sha256"):
            names.append(path.name)
    return names


STAMPED_REPORTS = _stamped_reports()

# Reports whose provenance block predates the block existing. Named, not tolerated: each
# entry is an xfail(strict=True), so re-scoring the arm turns it into an XPASS that breaks
# the suite until the entry is deleted.
NO_GIT_PROVENANCE = {
    "gemini-2.5-flash-audio.json":
        "generated 2026-08-17, before score_asr.py stamped a commit or a dirty flag; its "
        "sha256 is HEAD's content CRLF-rendered, which the hash alone cannot demonstrate. "
        "Re-score the arm to retire this.",
    "qwen3-asr-1.7b.json":
        "generated 2026-08-17, same missing block -- and this is the report carrying the "
        "0.673 pooled CER that the ASR-012 runaway produced, so it needs re-scoring under "
        "the runaway gate regardless.",
}


def _report_params() -> list:
    params = []
    for name in STAMPED_REPORTS:
        reason = NO_GIT_PROVENANCE.get(name)
        marks = [pytest.mark.xfail(strict=True, reason=reason)] if reason else []
        params.append(pytest.param(name, marks=marks))
    return params


@needs_git
def test_the_report_audit_is_not_vacuous() -> None:
    """A test that silently found nothing to check has not checked anything.

    Both halves can rot quietly: a reports directory that stops being committed makes the
    parametrisation empty, and a `_commit_renderings` that stops finding history makes
    every stamp unmatchable at once rather than one at a time.
    """
    assert set(NO_GIT_PROVENANCE) <= set(STAMPED_REPORTS), (
        f"exemptions name reports that are not there: "
        f"{sorted(set(NO_GIT_PROVENANCE) - set(STAMPED_REPORTS))}"
    )
    known = _commit_renderings()
    head_lf = hashlib.sha256(_blob_at("HEAD")).hexdigest()
    assert head_lf in known, "HEAD's own content is not in the map, so the map is broken"
    assert len(known) % 2 == 0 and len(known) >= 4


@needs_git
@pytest.mark.parametrize("name", STAMPED_REPORTS)
def test_report_identifies_the_code_that_produced_it(name: str) -> None:
    doc = json.loads((REPORTS / name).read_text(encoding="utf-8"))
    stamp = doc["scoring_code_sha256"]
    known = _commit_renderings()

    if stamp in known:
        return
    if (doc.get("scoring_code") or {}).get("dirty") is True:
        # An honest report of an uncommitted run. Not reproducible, and it says so.
        return

    pytest.fail(
        f"{name} stamps scoring_code_sha256={stamp[:16]}..., which is neither rendering of "
        f"any committed score_asr.py, and the report does not declare itself dirty. Either "
        f"the code that produced it was never committed and the report failed to say so, "
        f"or the stamp was written by hand. Known renderings:\n  "
        + "\n  ".join(f"{h[:16]}...  {w}" for h, w in sorted(known.items(), key=lambda kv: kv[1]))
    )


@needs_git
@pytest.mark.parametrize("name", _report_params())
def test_report_carries_its_commit_and_dirty_flag(name: str) -> None:
    """The defect the two 2026-08-17 reports really do have.

    A content hash says which bytes ran. It cannot say whether anyone else can obtain those
    bytes, and it cannot say whether the tree around them was committed. Without the commit
    and the flag, a reader who finds a stamp matching nothing has no way to tell an
    uncommitted run from a rendering difference -- which is precisely the hour this cost.
    """
    doc = json.loads((REPORTS / name).read_text(encoding="utf-8"))
    block = doc.get("scoring_code")
    assert isinstance(block, dict), f"{name} carries no scoring_code provenance block"
    assert "commit" in block and "dirty" in block and "git_status" in block
    assert block["sha256"] == doc["scoring_code_sha256"], (
        "the top-level stamp and the block disagree about the same file, which means one "
        "of them was edited by hand"
    )


# --- parallel scoring must change nothing --------------------------------------------------

_UNIT = "ขอบคุณครับ ยินดีให้บริการครับ "

# The synthetic phone block, `^0810000[0-9]{3}$`. 0810000301 is already in use by this pack
# (asr-eval reserves 301-339), so nothing here consumes a fresh number.
_ENTITIES = [{"type": "phone", "value": "0810000301",
              "spoken": "ศูนย์ แปด หนึ่ง ศูนย์ ศูนย์ ศูนย์ ศูนย์ สาม ศูนย์ หนึ่ง"}]
_TIMELINE = {"segments": [{"kind": "speech", "start_s": 0.0, "dur_s": 100.0}]}


def _tasks() -> list[tuple]:
    """Six items chosen so that completion order is NOT submission order.

    Per-item cost spans two orders of magnitude here -- one long call, four tiny ones, and
    a runaway that refuses in microseconds -- and worker start-up staggers on top of that.
    Measured on this fixture at three workers, `as_completed` returns
    SLOW-000, FAST-001, FAST-004, LOOP-005, FAST-002, FAST-003. So the order assertion in
    the equivalence test has something to catch; on a fixture of six identical items it
    would pass whatever the pool did.

    Item 5 is the runaway, and it is here because the gate has to fire inside a worker
    exactly as it does in-process: before the distance, returning a refusal rather than a
    CER. A parallel path that scored it would differ from the serial path by one item and
    by a corpus figure.
    """
    long_ref = ("สวัสดีครับ ติดต่อฝ่ายบริการลูกค้า "
                "ขอทราบหมายเลขโทรศัพท์ ศูนย์ แปด หนึ่ง ศูนย์ ศูนย์ ศูนย์ ศูนย์ สาม ศูนย์ หนึ่ง ครับ " * 6)
    long_hyp = long_ref.replace("ครับ", "คับ").replace("ศูนย์", "สูญ")
    short_ref = "สวัสดีครับ ขอบคุณที่ใช้บริการ"
    tasks = [("SLOW-000", long_ref, long_hyp, _ENTITIES, _TIMELINE, 300.0, "retention")]
    for i in range(1, 5):
        tasks.append((f"FAST-{i:03d}", short_ref, short_ref[: 29 - i] + "ค",
                      _ENTITIES, _TIMELINE, 300.0, "hold_ivr"))
    tasks.append(("LOOP-005", long_ref, long_ref[: len(long_ref) // 4] + _UNIT * 40,
                  _ENTITIES, _TIMELINE, 300.0, "retention"))
    return tasks


def test_the_equivalence_fixture_exercises_both_outcomes() -> None:
    """Guard the guard: a fixture where nothing is refused proves nothing about refusals."""
    rows = S.score_items(_tasks(), 1)
    assert any("runaway" in r for r in rows), (
        "no item in the fixture trips the runaway gate, so the parallel test never checks "
        "that the gate still runs before the distance inside a worker"
    )
    assert any("cer_norm" in r for r in rows), "no item is actually scored"


def test_parallel_and_serial_scoring_are_byte_identical(monkeypatch) -> None:
    """The only thing the pool is allowed to change is how long it takes.

    Serialised rather than compared field by field on purpose: a dict comparison would pass
    on reordered keys, and the report is a JSON file whose diff against its predecessor is
    how a change gets noticed.
    """
    tasks = _tasks()

    pools: list[int | None] = []

    class SpyPool(concurrent.futures.ProcessPoolExecutor):
        def __init__(self, *args, **kwargs):
            pools.append(kwargs.get("max_workers"))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", SpyPool)

    serial = S.score_items(tasks, 1)
    parallel = S.score_items(tasks, S.resolve_jobs(3, len(tasks)))

    assert pools == [3], (
        "no process pool was created, so this test compared the serial path against "
        "itself and would pass however wrong the parallel path was"
    )

    dump = json.dumps(serial, ensure_ascii=False).encode("utf-8")
    assert dump == json.dumps(parallel, ensure_ascii=False).encode("utf-8")

    submitted = [t[0] for t in tasks]
    assert [r["item_id"] for r in serial] == submitted
    assert [r["item_id"] for r in parallel] == submitted, (
        "results came back in completion order; the slowest item is first on purpose"
    )


def test_resolve_jobs_refuses_a_worker_count_below_one() -> None:
    for bad in (0, -1, -14):
        with pytest.raises(ValueError, match="at least 1"):
            S.resolve_jobs(bad, 20)


def test_resolve_jobs_never_spawns_more_workers_than_there_is_work() -> None:
    assert S.resolve_jobs(64, 3) == 3
    assert S.resolve_jobs(None, 1) == 1
    assert S.resolve_jobs(None, 0) == 1
    assert 1 <= S.resolve_jobs(None, 4) <= 4


def test_cli_refuses_jobs_below_one() -> None:
    """The refusal has to fire at the command line too, before anything is read.

    resolve_jobs raising is no use to somebody who typed `--jobs 0` and walked away: the
    process must exit non-zero with the reason, not start scoring.
    """
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "score_asr.py"),
                           "--jobs", "0"], capture_output=True, text=True)
    assert proc.returncode != 0
    assert "at least 1" in proc.stderr

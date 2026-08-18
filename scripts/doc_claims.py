"""Check that a number published in a document still matches the data it came from.

    python scripts/doc_claims.py --check      # verify every marked figure, exit 1 on any miss
    python scripts/doc_claims.py --list       # print every marked figure and its source value

WHY THIS EXISTS. Six wrong numbers have gone out of this repository and not one of them was
a scorer bug. Every one was a correct number described wrongly, or a correct number that
stopped being correct and nobody re-derived it.

  * DEVLOG.md carried "entity 320/465 = 68.8%" for weeks. The figure is 450/465; 320 came
    off a scorer two revisions old. Note that 320/465 really is 68.8% -- the fraction and
    the percentage agreed with each other, which is precisely why re-reading the sentence
    never caught it. Both halves were stale together. Internal consistency is not evidence.
  * docs/migration-decision.md printed a pack-A-only weighted F1 two paragraphs under a
    POOLED accuracy table with nothing marking the scope change. The F1 lead reverses
    between the two packs, so the juxtaposition did not merely blur the scope, it aimed the
    reader at the opposite conclusion.

The scorer was right both times, and the suite reads no prose, so 525 numeric tokens across
docs/*.md sat under no test at all.

HOW A FIGURE OPTS IN. Prose stays prose. Only a figure wrapped in a marker is checked:

    <!--claim:pooled-bands.json:business_accuracy[0].candidate_accuracy:pct1-->93.1%<!--/-->

  source   a bare filename under docs/reports/. No directories, so a marker cannot reach
           out of the published-report tree and cite something nobody ships.
  paths    one or more comma-separated dotted/indexed lookups into that JSON.
  format   how the source value is turned into document text. The set is deliberately tiny:

             int    integer, or a float that is exactly integral      188, 90, -6
             f3     float to three decimal places                     0.955
             pct1   fraction scaled to percent, one dp, with sign     93.1%
             num1   the same without the percent sign                 97.4
             frac   exactly two paths, numerator/denominator          175/188
             text   the string as stored                              excellent, UNDERPOWERED
             pm     a plus/minus band; null renders as an em dash     +/-16, --

           Every format except `frac` accepts more than one path and requires them all to
           render identically. That is how a sentence like "both excellent" or "175 each"
           gets checked as the two-sided claim it actually is, instead of half of one.

RENDER THE WAY THE PRODUCER RENDERS. `pct1` uses the same `%.1f` on value*100 that
scripts/pooled_bands.py used to print the figure in the first place. Rounding the "better"
way here, half-up or via Decimal, would make this a second opinion about rounding rather
than a check on the number, and it would fire on figures that are not wrong.

FIVE REFUSALS, because a document checker that shrugs is worse than none -- it converts
"nobody checked" into "something checked and said fine":

  1. A marker naming a JSON file that is not there. Not "skip, cannot resolve".
  2. A marker whose path does not resolve. The message names the last step that did resolve
     and what was available at it, because the usual cause is a renamed key.
  3. A marker whose rendered value differs from the document text. This is the headline.
  4. A malformed marker -- unclosed, missing a field, unknown format, a stray closer. A
     checker that ignores what it cannot parse gives false assurance: the marker is visible
     in the diff, so a reviewer believes the figure beside it is covered.
  5. A run that checked nothing. Zero claims found is how this quietly stops being a gate,
     whether from a mistyped path argument or from someone deleting markers with a paragraph.

THE ONE THING NOT CHECKED, AND IT IS COUNTED OUT LOUD. A marker inside a fenced code block
is literal sample text -- docs/harness-tightening-plan.md sketches this very syntax, and a
checker that refuses the document specifying it is a checker nobody lands. Fenced markers are
therefore read as examples, but never silently: both modes print how many were passed over,
so "the marker is in a fence" can never be the unnoticed reason a figure went unchecked.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
REPORTS = DOCS / "reports"

# U+2212 MINUS SIGN. Typographers write "-6" as this; Python writes an ASCII hyphen. Folding
# it is safe because the two are the same number. U+2013 EN DASH is deliberately NOT folded:
# in these documents it is a range separator ("90-96%", "5-16x"), and treating it as a minus
# would silently turn a range into a negative.
MINUS_SIGN = "−"
EM_DASH = "—"
PLUS_MINUS = "±"

CLOSE = "<!--/-->"

# Two patterns on purpose. LOOSE_OPEN finds anything a human would read as the start of a
# claim, including the broken spellings; HEADER is the only thing accepted. Everything
# LOOSE_OPEN finds and HEADER rejects becomes refusal 4 rather than silence.
LOOSE_OPEN = re.compile(r"<!--\s*claim", re.IGNORECASE)
HEADER = re.compile(
    r"<!--claim:(?P<source>[^:<>]+):(?P<paths>[^:<>]+):(?P<fmt>[^:<>]+)-->"
)

SEGMENT = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<indices>(?:\[\d+\])*)$")
INDEX = re.compile(r"\[(\d+)\]")

FENCE = re.compile(r"^ {0,3}(?P<mark>```+|~~~+)", re.MULTILINE)


class ClaimError(Exception):
    """A published figure could not be confirmed against its source."""


class MalformedMarker(ClaimError):
    """The marker itself is unusable, so what it claims to cover is unknown."""


class MissingSource(ClaimError):
    """The cited report does not exist."""


class UnresolvedPath(ClaimError):
    """The cited report exists but does not contain the value."""


class Mismatch(ClaimError):
    """The document and the data disagree. The whole point."""


class NothingChecked(ClaimError):
    """The run found no claims, so a green result would mean nothing."""


def _show(path: Path) -> str:
    """Repo-relative when it can be, absolute when it cannot.

    `relative_to` raises on anything outside the repository, and a refusal message must
    never be the thing that raises -- a document handed over by absolute path from somewhere
    else is exactly the case a "does not exist" message is being built for.
    """
    try:
        return str(path.relative_to(REPO)).replace("\\", "/")
    except ValueError:
        return str(path)


@dataclass(frozen=True)
class Claim:
    """One marked figure, located precisely enough to fix without searching for it."""

    document: Path
    line: int
    source: str
    paths: tuple[str, ...]
    fmt: str
    found: str

    @property
    def where(self) -> str:
        return f"{_show(self.document)}:{self.line}"

    @property
    def cited(self) -> str:
        return f"docs/reports/{self.source}#{','.join(self.paths)}"


@dataclass(frozen=True)
class Scan:
    """What a document yielded: the claims, and the markers passed over as examples.

    `examples` is a list rather than a count so the reason a figure went unchecked can be
    printed with its line number. An unexplained gap between "markers in the file" and
    "figures verified" is how this becomes a gate in name only.
    """

    claims: list[Claim]
    examples: list[str]


# ------------------------------------------------------------------------------------------
# Parsing.  Refusal 4 lives here.
# ------------------------------------------------------------------------------------------

def fenced_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges covered by fenced code blocks.

    Markdown inside a fence is sample text, not published prose, and the document that
    specifies this marker syntax necessarily contains a marker that is not a claim. An
    unterminated fence runs to end of file, matching how a renderer reads it.
    """
    spans: list[tuple[int, int]] = []
    open_at: int | None = None
    for fence in FENCE.finditer(text):
        if open_at is None:
            open_at = fence.start()
        else:
            spans.append((open_at, fence.end()))
            open_at = None
    if open_at is not None:
        spans.append((open_at, len(text)))
    return spans


def parse_document(path: Path) -> Scan:
    """Every claim in one document, or a refusal naming the marker that stopped it.

    Deliberately returns no partial result alongside an error. A document holding one broken
    marker covers an unknown number of figures, and handing back the ones that did parse
    invites reading the run as "mostly fine".
    """
    text = path.read_text(encoding="utf-8")
    spans = fenced_spans(text)
    starts: list[int] = []
    examples: list[str] = []
    for match in LOOSE_OPEN.finditer(text):
        if any(begin <= match.start() < end for begin, end in spans):
            examples.append(f"{_show(path)}:{text.count(chr(10), 0, match.start()) + 1}")
            continue
        starts.append(match.start())

    claims: list[Claim] = []
    closed_at: set[int] = set()

    for position, start in enumerate(starts):
        line = text.count("\n", 0, start) + 1
        header = HEADER.match(text, start)
        if header is None:
            snippet = text[start:start + 60].splitlines()[0]
            raise MalformedMarker(
                f"{_show(path)}:{line}: malformed claim marker {snippet!r}. Expected exactly "
                "`<!--claim:SOURCE.json:PATH:FORMAT-->`: three colon-separated fields, no "
                "space in the opener. A marker that cannot be parsed is not skipped, because "
                "the figure beside it looks covered to everyone reading the diff."
            )

        body = header.end()
        close = text.find(CLOSE, body)
        next_open = starts[position + 1] if position + 1 < len(starts) else None
        if close < 0 or (next_open is not None and next_open < close):
            raise MalformedMarker(
                f"{_show(path)}:{line}: unclosed claim marker for "
                f"{header.group('paths')!r}. Every claim ends with `{CLOSE}` before the next "
                "one opens; without it the checked text runs on into whatever follows."
            )
        found = text[body:close]
        if "\n" in found:
            raise MalformedMarker(
                f"{_show(path)}:{line}: claim marker for {header.group('paths')!r} spans a "
                "line break. A figure is one run of text on one line, so a claim that wraps "
                "is a marker closed in the wrong place."
            )
        closed_at.add(close)

        source = header.group("source").strip()
        if "/" in source or "\\" in source or ".." in source:
            raise MalformedMarker(
                f"{_show(path)}:{line}: claim source {source!r} is not a bare filename. "
                "Sources are read from docs/reports/ only, so a document cannot quote a file "
                "that is not published beside it."
            )
        fmt = header.group("fmt").strip()
        if fmt not in FORMATS:
            raise MalformedMarker(
                f"{_show(path)}:{line}: unknown claim format {fmt!r}. Known formats are "
                f"{', '.join(sorted(FORMATS))}."
            )
        paths = tuple(p.strip() for p in header.group("paths").split(","))
        if not all(paths):
            raise MalformedMarker(
                f"{_show(path)}:{line}: claim on {source!r} has an empty path field."
            )
        claims.append(
            Claim(document=path, line=line, source=source, paths=paths, fmt=fmt, found=found)
        )

    for stray in re.finditer(re.escape(CLOSE), text):
        if stray.start() in closed_at:
            continue
        if any(begin <= stray.start() < end for begin, end in spans):
            continue
        line = text.count("\n", 0, stray.start()) + 1
        raise MalformedMarker(
            f"{_show(path)}:{line}: closing `{CLOSE}` with no claim marker opening it. "
            "The usual cause is an opener edited away that left its close behind, which "
            "silently unmarks the figure between them."
        )
    return Scan(claims=claims, examples=examples)


# ------------------------------------------------------------------------------------------
# Resolution.  Refusals 1 and 2 live here.
# ------------------------------------------------------------------------------------------

def load_source(name: str, cache: dict[str, object]) -> object:
    if name in cache:
        return cache[name]
    path = REPORTS / name
    if not path.exists():
        raise MissingSource(
            f"claim source {_show(path)} does not exist. The figure is quoted from a report "
            "that is not in the tree, so there is nothing to check it against and nothing to "
            "fall back to."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MissingSource(f"claim source {_show(path)} is not valid JSON: {exc}") from exc
    cache[name] = data
    return data


def _steps(path: str) -> list[str | int]:
    steps: list[str | int] = []
    for segment in path.split("."):
        match = SEGMENT.match(segment)
        if match is None:
            raise MalformedMarker(
                f"claim path {path!r} has an unusable segment {segment!r}. Paths are dotted "
                "names with optional [n] indices, e.g. business_accuracy[0].n."
            )
        steps.append(match.group("name"))
        steps.extend(int(i) for i in INDEX.findall(match.group("indices")))
    return steps


def resolve(data: object, path: str, source: str) -> object:
    """Walk one dotted/indexed path, or refuse naming the point it stopped at.

    The message carries the prefix that DID resolve and what was available there. The usual
    cause is a renamed key in a regenerated report, and a bare "path not found" sends the
    reader off to re-derive a figure that was never wrong.
    """
    node = data
    walked: list[str] = []
    for step in _steps(path):
        here = "".join(walked) or "<root>"
        if isinstance(step, int):
            if not isinstance(node, list):
                raise UnresolvedPath(
                    f"{source}: {path} indexes [{step}] into {here}, which is a "
                    f"{type(node).__name__}, not a list."
                )
            if not 0 <= step < len(node):
                raise UnresolvedPath(
                    f"{source}: {path} indexes [{step}] into {here}, which has "
                    f"{len(node)} entries."
                )
            walked.append(f"[{step}]")
            node = node[step]
            continue
        if not isinstance(node, dict):
            raise UnresolvedPath(
                f"{source}: {path} looks up {step!r} in {here}, which is a "
                f"{type(node).__name__}, not an object."
            )
        if step not in node:
            raise UnresolvedPath(
                f"{source}: {path} does not resolve. {here} has no key {step!r}; it has "
                f"{sorted(node)}. A renamed key in a regenerated report looks exactly like "
                "this, and the published figure may well still be correct."
            )
        walked.append(f".{step}" if walked else step)
        node = node[step]
    return node


# ------------------------------------------------------------------------------------------
# Formatting.
# ------------------------------------------------------------------------------------------

def _number(value: object, fmt: str) -> float:
    # bool is an int in Python and would render as 1 or 0. A field that turned into a boolean
    # is a change in the report's shape, not a figure.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Mismatch(f"format {fmt!r} needs a number, but the source holds {value!r}.")
    return float(value)


def _integer(value: object, fmt: str = "int") -> str:
    number = _number(value, fmt)
    if number != int(number):
        raise Mismatch(
            f"format {fmt!r} needs a whole number, but the source holds {value!r}. Rounding "
            "here would publish a different figure from the one stored."
        )
    return str(int(number))


def _f3(value: object) -> str:
    return f"{_number(value, 'f3'):.3f}"


def _pct1(value: object) -> str:
    return f"{_number(value, 'pct1') * 100:.1f}%"


def _num1(value: object) -> str:
    return f"{_number(value, 'num1') * 100:.1f}"


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise Mismatch(f"format 'text' needs a string, but the source holds {value!r}.")
    return value


def _pm(value: object) -> str:
    # None is a real answer here, not a missing one: exact_band returns it when no band exists
    # at any net, and the documents print an em dash for that. Rendering it "None", or
    # skipping the claim, would let "no band exists" be replaced by a band unnoticed.
    if value is None:
        return EM_DASH
    return f"{PLUS_MINUS}{_integer(value, 'pm')}"


# `frac` is the one format taking two paths, so it is dispatched in render() rather than here.
FORMATS: dict[str, object] = {
    "int": _integer,
    "f3": _f3,
    "pct1": _pct1,
    "num1": _num1,
    "text": _text,
    "pm": _pm,
    "frac": None,
}


def render(claim: Claim, cache: dict[str, object]) -> str:
    """The document text this claim's source values should produce."""
    data = load_source(claim.source, cache)
    values = [resolve(data, path, claim.source) for path in claim.paths]

    if claim.fmt == "frac":
        if len(values) != 2:
            raise MalformedMarker(
                f"{claim.where}: format 'frac' needs exactly two paths "
                f"(numerator,denominator); got {len(values)}."
            )
        return f"{_integer(values[0], 'frac')}/{_integer(values[1], 'frac')}"

    rendered = [FORMATS[claim.fmt](value) for value in values]
    if len(set(rendered)) != 1:
        # The document states ONE figure for these paths -- "both excellent", "175 each". If
        # the paths have drifted apart, that word is now the wrong word even though no single
        # number in the document is wrong, which is exactly the failure this exists for.
        pairs = ", ".join(f"{p}={r}" for p, r in zip(claim.paths, rendered))
        raise Mismatch(
            f"{claim.where}: the document states one value for {len(values)} paths, but they "
            f"no longer agree: {pairs}."
        )
    return rendered[0]


def _normalise(text: str) -> str:
    return text.replace(MINUS_SIGN, "-")


def verify(claim: Claim, cache: dict[str, object]) -> str:
    """Raise unless the document text is what the source renders to. Returns that value."""
    expected = render(claim, cache)
    if _normalise(claim.found) != _normalise(expected):
        raise Mismatch(
            f"{claim.where}: published figure does not match its source.\n"
            f"    source    {claim.cited}\n"
            f"    format    {claim.fmt}\n"
            f"    expected  {expected}\n"
            f"    found     {claim.found}"
        )
    return expected


# ------------------------------------------------------------------------------------------
# Driving.
# ------------------------------------------------------------------------------------------

def documents(targets: list[str]) -> list[Path]:
    if not targets:
        return sorted(DOCS.rglob("*.md"))
    resolved: list[Path] = []
    for target in targets:
        path = Path(target)
        if not path.is_absolute():
            path = REPO / path
        if not path.exists():
            raise MissingSource(f"document {_show(path)} does not exist.")
        resolved.append(path)
    return resolved


def collect(targets: list[str]) -> Scan:
    claims: list[Claim] = []
    examples: list[str] = []
    for path in documents(targets):
        scan = parse_document(path)
        claims.extend(scan.claims)
        examples.extend(scan.examples)
    if not claims:
        raise NothingChecked(
            "no claim markers found in "
            + (", ".join(targets) if targets else f"{_show(DOCS)}/**/*.md")
            + ". A checker that finds nothing and reports success is indistinguishable from "
            "one that is broken, so this is a failure rather than a clean run."
        )
    return Scan(claims=claims, examples=examples)


def _examples_line(scan: Scan) -> str:
    if not scan.examples:
        return ""
    return (
        f"\n{len(scan.examples)} marker(s) inside code fences read as examples, not claims: "
        + ", ".join(scan.examples)
    )


def check(targets: list[str], out) -> int:
    """--check. Every marked figure; every failure listed; exit 1 if there is one."""
    try:
        scan = collect(targets)
    except ClaimError as exc:
        print(f"REFUSED: {exc}", file=out)
        return 1

    cache: dict[str, object] = {}
    failures = 0
    for claim in scan.claims:
        try:
            verify(claim, cache)
        except ClaimError as exc:
            failures += 1
            print(f"REFUSED: {exc}", file=out)
    seen = len({claim.document for claim in scan.claims})
    if failures:
        print(
            f"\n{failures} of {len(scan.claims)} published figures do not match their source "
            f"({seen} document(s) checked).{_examples_line(scan)}",
            file=out,
        )
        return 1
    print(
        f"{len(scan.claims)} published figures match their source "
        f"({seen} document(s) checked).{_examples_line(scan)}",
        file=out,
    )
    return 0


def show(targets: list[str], out) -> int:
    """--list. Every marked figure and what its source resolves to."""
    try:
        scan = collect(targets)
    except ClaimError as exc:
        print(f"REFUSED: {exc}", file=out)
        return 1

    cache: dict[str, object] = {}
    for claim in scan.claims:
        try:
            value = render(claim, cache)
        except ClaimError as exc:
            print(f"REFUSED: {exc}", file=out)
            return 1
        flag = "" if _normalise(value) == _normalise(claim.found) else "   <-- MISMATCH"
        print(f"{claim.where:<34} {claim.fmt:<5} {claim.cited}", file=out)
        print(f"{'':<34} source {value!r}  document {claim.found!r}{flag}", file=out)
    print(
        f"\n{len(scan.claims)} claims. --check is the gate; this only reports."
        f"{_examples_line(scan)}",
        file=out,
    )
    return 0


def main(argv: list[str] | None = None, out=None) -> int:
    # Windows consoles in this project's locale are cp874, and these figures carry the
    # plus/minus, minus and em-dash characters the documents use. A checker that dies of
    # UnicodeEncodeError while printing a mismatch has reported nothing.
    if out is None:
        out = sys.stdout
        if hasattr(out, "reconfigure"):
            out.reconfigure(errors="backslashreplace")

    parser = argparse.ArgumentParser(
        description="Verify figures published in Markdown against docs/reports/*.json."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="verify; exit 1 on any mismatch")
    mode.add_argument("--list", action="store_true", help="print every claim and its value")
    parser.add_argument(
        "documents", nargs="*", help="documents to scan (default: docs/**/*.md)"
    )
    args = parser.parse_args(argv)
    return check(args.documents, out) if args.check else show(args.documents, out)


if __name__ == "__main__":
    raise SystemExit(main())

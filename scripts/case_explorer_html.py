"""Render the case explorer to a single self-contained HTML file.

Presentation only. Every number this file displays was computed and verified in
`case_explorer.py`; nothing here recomputes a metric, with one deliberate exception:
the browser aggregates the precomputed scoring atoms so that filtering the case list
re-scores the slice. That aggregation is a transcription of `f1_from_atoms`, and the
page checks itself against the authoritative figures on load -- see SELF-CHECK below.

Colour: the three status steps are validated against the dataviz six-checks in both
modes rather than picked by eye.

    light  #00795C / #CE1F62 / #9C6B08   all pass; CVD deutan dE 6.6 sits in the 6-8
                                         band, which is legal only with secondary
                                         encoding -- every status here also carries a
                                         glyph and a word, never colour alone.
    dark   #17A886 / #D0225F / #B58435   all five checks pass outright, CVD dE 8.3.

Green-vs-red is the one pairing that cannot be made colourblind-safe, which is why the
"wrong" step is a magenta-red rather than a true red: it buys separation under deutan
and protan that a conventional red does not.
"""

from __future__ import annotations

import json

# The Thai stack is the one real typographic constraint. The transcripts carry vowel and
# tone marks stacked above and below the baseline, so they need a face that draws them and
# a line-height that leaves room -- 1.95 rather than the 1.5 the Latin UI runs at. The CSP
# blocks font CDNs, and a Thai webfont is far too heavy to inline as a data URI, so this
# is a system stack chosen for coverage rather than an embedded face.
CSS = """
:root {
  color-scheme: light dark;

  --bg: #F4F6F5;
  --surface: #FFFFFF;
  --surface-2: #FAFBFA;
  --surface-3: #F0F2F1;
  --ink: #14181C;
  --ink-2: #4C565F;
  --ink-3: #7C868E;
  --line: #E0E4E2;
  --line-2: #CBD1CE;
  --accent: #0F5F73;
  --accent-soft: #E4F0F3;

  --ok: #00795C;
  --bad: #CE1F62;
  --warn: #9C6B08;
  --ok-soft: #E3F2EC;
  --bad-soft: #FCE7EF;
  --warn-soft: #F9EFDC;

  --ui: ui-sans-serif, "Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system, sans-serif;
  --mono: ui-monospace, "Cascadia Mono", "SF Mono", Consolas, monospace;
  --thai: "Noto Sans Thai", "IBM Plex Sans Thai", "Leelawadee UI", "Sarabun", Tahoma, sans-serif;

  --r: 7px;
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #101110;
    --surface: #1A1A19;
    --surface-2: #202120;
    --surface-3: #272827;
    --ink: #ECEEEC;
    --ink-2: #A6ACA9;
    --ink-3: #767D79;
    --line: #2C2E2C;
    --line-2: #3C3F3D;
    --accent: #6FC0D4;
    --accent-soft: #17282D;

    --ok: #17A886;
    --bad: #D0225F;
    --warn: #B58435;
    --ok-soft: #10241F;
    --bad-soft: #2A1119;
    --warn-soft: #241B0E;
  }
}

:root[data-theme="dark"] {
  --bg: #101110;
  --surface: #1A1A19;
  --surface-2: #202120;
  --surface-3: #272827;
  --ink: #ECEEEC;
  --ink-2: #A6ACA9;
  --ink-3: #767D79;
  --line: #2C2E2C;
  --line-2: #3C3F3D;
  --accent: #6FC0D4;
  --accent-soft: #17282D;

  --ok: #17A886;
  --bad: #D0225F;
  --warn: #B58435;
  --ok-soft: #10241F;
  --bad-soft: #2A1119;
  --warn-soft: #241B0E;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 400 14px/1.5 var(--ui);
  height: 100dvh;
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr);
  overflow: hidden;
}

/* ---------- header ---------- */

.top {
  display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px 16px;
  padding: 12px 20px;
  background: var(--surface);
  border-bottom: 1px solid var(--line);
}
.top h1 { margin: 0; font-size: 16px; font-weight: 650; letter-spacing: -0.01em; }
.top .sub { color: var(--ink-3); font-size: 12.5px; }
.top .spacer { flex: 1 1 auto; }
.pill {
  font: 500 11px/1 var(--mono);
  padding: 4px 7px; border-radius: 5px;
  background: var(--surface-3); color: var(--ink-2);
  border: 1px solid var(--line);
  white-space: nowrap;
}
button.tbtn {
  font: 500 12px var(--ui); cursor: pointer;
  padding: 5px 10px; border-radius: 5px;
  background: var(--surface-3); color: var(--ink-2); border: 1px solid var(--line);
}
button.tbtn:hover { border-color: var(--line-2); color: var(--ink); }

#selfcheck {
  display: none; grid-column: 1 / -1;
  padding: 10px 20px; font: 600 13px var(--ui);
  background: var(--bad-soft); color: var(--bad);
  border-bottom: 1px solid var(--bad);
}
#selfcheck.bad { display: block; }

/* ---------- scoreboard ---------- */

.board {
  background: var(--surface); border-bottom: 1px solid var(--line);
  padding: 12px 20px 14px;
  overflow-x: auto;
}
.board-head {
  display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
  margin-bottom: 10px;
}
.board-head strong { font-size: 13px; font-weight: 650; }
.board-head span { color: var(--ink-3); font-size: 12.5px; }
#slice-n { font-family: var(--mono); color: var(--ink); font-weight: 600; }

table.board-t { border-collapse: collapse; min-width: 660px; width: 100%; }
table.board-t th {
  text-align: left; font: 600 10.5px/1 var(--ui); letter-spacing: .07em;
  text-transform: uppercase; color: var(--ink-3);
  padding: 0 10px 7px 0; white-space: nowrap;
}
table.board-t th.dim { width: 26%; }
table.board-t td { padding: 4px 10px 4px 0; vertical-align: middle; }
table.board-t tr + tr td { border-top: 1px solid var(--line); }
.mname { font-size: 13px; font-weight: 550; white-space: nowrap; }
.mname .tag {
  font: 500 9.5px/1 var(--mono); text-transform: uppercase; letter-spacing: .06em;
  padding: 3px 5px; border-radius: 4px; margin-left: 7px;
  background: var(--surface-3); color: var(--ink-3); border: 1px solid var(--line);
  vertical-align: 1px;
}
.mname .tag.prod { background: var(--accent-soft); color: var(--accent); border-color: transparent; }

.cell { display: flex; align-items: center; gap: 9px; }
.cell .v {
  font: 600 14px/1 var(--mono); font-variant-numeric: tabular-nums;
  min-width: 46px; text-align: right;
}
.cell .track {
  position: relative; flex: 1 1 auto; min-width: 60px;
  height: 7px; border-radius: 4px; background: var(--surface-3);
  overflow: hidden;
}
.cell .fill {
  position: absolute; inset: 0 auto 0 0; border-radius: 4px;
  background: var(--accent); transition: width .18s ease;
}
.cell.best .v { color: var(--ok); }

/* ---------- shell ---------- */

.shell { display: grid; grid-template-columns: 336px minmax(0, 1fr); min-height: 0; }

aside {
  border-right: 1px solid var(--line); background: var(--surface-2);
  display: grid; grid-template-rows: auto minmax(0, 1fr); min-height: 0;
}

.filters { padding: 12px 14px; border-bottom: 1px solid var(--line); }
.frow { margin-bottom: 10px; }
.frow:last-child { margin-bottom: 0; }
.flabel {
  font: 600 10px/1 var(--ui); letter-spacing: .08em; text-transform: uppercase;
  color: var(--ink-3); margin-bottom: 6px; display: block;
}
.chips { display: flex; flex-wrap: wrap; gap: 4px; }
.chip {
  font: 500 11.5px var(--ui); cursor: pointer; user-select: none;
  padding: 3px 8px; border-radius: 20px;
  background: var(--surface); color: var(--ink-2); border: 1px solid var(--line);
}
.chip:hover { border-color: var(--line-2); }
.chip[aria-pressed="true"] {
  background: var(--accent); color: #fff; border-color: var(--accent);
}
:root[data-theme="dark"] .chip[aria-pressed="true"],
:root:not([data-theme="light"]) .chip[aria-pressed="true"] { color: #0B1416; }
.chip .n { opacity: .62; font-family: var(--mono); font-size: 10px; margin-left: 3px; }

select, input[type="search"] {
  width: 100%; font: 400 12.5px var(--ui);
  padding: 6px 8px; border-radius: 5px;
  background: var(--surface); color: var(--ink); border: 1px solid var(--line);
}
select:focus-visible, input:focus-visible, .chip:focus-visible, button:focus-visible,
.case:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
label.check {
  display: flex; align-items: center; gap: 6px; font-size: 12.5px;
  color: var(--ink-2); cursor: pointer; padding: 2px 0;
}

/* ---------- case list ---------- */

.list { overflow-y: auto; min-height: 0; }
.list-head {
  position: sticky; top: 0; z-index: 2;
  display: flex; justify-content: space-between; align-items: center;
  padding: 7px 14px; background: var(--surface-3);
  border-bottom: 1px solid var(--line);
  font: 600 10px/1 var(--ui); letter-spacing: .08em; text-transform: uppercase;
  color: var(--ink-3);
}
.case {
  display: grid; grid-template-columns: 1fr auto; gap: 2px 8px;
  padding: 8px 14px; cursor: pointer;
  border-bottom: 1px solid var(--line);
  border-left: 3px solid transparent;
}
.case:hover { background: var(--surface-3); }
.case[aria-selected="true"] {
  background: var(--accent-soft); border-left-color: var(--accent);
}
.case .id { font: 600 12px var(--mono); }
.case .meta { grid-column: 1; font-size: 11px; color: var(--ink-3); }
.case .dots { grid-column: 2; grid-row: 1 / span 2; align-self: center; display: flex; gap: 3px; }
.legend { display: flex; gap: 3px; }
.legend span {
  width: 24px; text-align: center;
  font: 600 9px/1 var(--mono); color: var(--ink-3);
  letter-spacing: 0; text-transform: none;
}
.dot {
  width: 24px; height: 18px; border-radius: 4px;
  display: grid; place-items: center;
  font: 700 10px/1 var(--ui);
}
.dot.ok  { background: var(--ok-soft);  color: var(--ok); }
.dot.bad { background: var(--bad-soft); color: var(--bad); }
.empty { padding: 30px 16px; text-align: center; color: var(--ink-3); font-size: 13px; }

/* ---------- detail ---------- */

main { overflow-y: auto; min-height: 0; padding: 18px 22px 60px; }
.wrap { max-width: 1080px; }

.case-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px 12px; margin-bottom: 4px; }
.case-head h2 { margin: 0; font: 650 19px var(--mono); letter-spacing: -0.01em; }
.mech { color: var(--ink-2); font-size: 13.5px; margin: 8px 0 18px; max-width: 74ch; }

.dilbar {
  display: flex; align-items: center; gap: 7px; flex-wrap: wrap;
  margin: -8px 0 18px; padding: 9px 12px;
  background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--r);
}
.dilbar .k {
  font: 600 10px/1 var(--ui); letter-spacing: .07em; text-transform: uppercase;
  color: var(--ink-3); margin-right: 3px;
}
button.dil {
  display: flex; align-items: baseline; gap: 6px; cursor: pointer;
  font: 600 12px var(--mono); padding: 4px 9px; border-radius: 5px;
  background: var(--surface); color: var(--ink-2); border: 1px solid var(--line);
}
button.dil:hover { border-color: var(--line-2); color: var(--ink); }
button.dil.on { background: var(--accent); border-color: var(--accent); color: #fff; }
/* the accent is a light cyan in dark mode, so the label on it must go dark. Both the
   stamped and the unstamped (system-dark) states need saying -- a rule written only
   under [data-theme] never fires for a viewer on the default "system" setting. */
:root[data-theme="dark"] button.dil.on { color: #0B1416; }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) button.dil.on { color: #0B1416; }
}
button.dil .c { font: 400 10px var(--mono); opacity: .72; }

section.blk { margin-bottom: 20px; }
section.blk > h3 {
  margin: 0 0 9px; font: 600 10.5px/1 var(--ui);
  letter-spacing: .09em; text-transform: uppercase; color: var(--ink-3);
}
.card {
  background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--r); overflow: hidden;
}

.turns { padding: 4px 0; max-height: 340px; overflow-y: auto; }
.turns.all { max-height: none; }
.turn { display: grid; grid-template-columns: 74px minmax(0, 1fr); gap: 10px; padding: 5px 14px; }
.turn + .turn { border-top: 1px solid var(--surface-3); }
.turn .who {
  font: 600 10px/1.9 var(--ui); letter-spacing: .05em; text-transform: uppercase;
  color: var(--ink-3); text-align: right; padding-top: 3px;
}
.turn.cust .who { color: var(--accent); }
.turn .txt { font-family: var(--thai); font-size: 14.5px; line-height: 1.95; }
.more {
  width: 100%; border: 0; border-top: 1px solid var(--line);
  background: var(--surface-3); color: var(--ink-2);
  font: 500 12px var(--ui); padding: 7px; cursor: pointer;
}
.more:hover { color: var(--ink); }

table.g { border-collapse: collapse; width: 100%; }
table.g th {
  text-align: left; font: 600 10px/1 var(--ui); letter-spacing: .07em;
  text-transform: uppercase; color: var(--ink-3);
  padding: 8px 12px; background: var(--surface-2);
  border-bottom: 1px solid var(--line);
}
table.g td { padding: 8px 12px; vertical-align: top; border-bottom: 1px solid var(--line); }
table.g tr:last-child td { border-bottom: 0; }
.lab {
  display: inline-block; font: 500 12px var(--mono);
  padding: 2px 6px; border-radius: 4px; margin: 1px 3px 1px 0;
  background: var(--surface-3); color: var(--ink);
}
.lab.ok  { background: var(--ok-soft);  color: var(--ok); }
.lab.bad { background: var(--bad-soft); color: var(--bad); }
.lab.gone { background: var(--bad-soft); color: var(--bad); text-decoration: line-through; }
.none { color: var(--ink-3); font-style: italic; font-size: 12.5px; }
.ev { font-family: var(--thai); font-size: 13px; line-height: 1.9; color: var(--ink-2); display: block; }
.cite { font: 400 10.5px var(--mono); color: var(--ink-3); }

/* ---------- model answers ---------- */

/* align-items:start so one tall card (an unstable model showing all three repeats)
   does not stretch its whole row into empty space */
.models {
  display: grid; gap: 12px; align-items: start;
  grid-template-columns: repeat(auto-fit, minmax(330px, 1fr));
}
.model { border-radius: var(--r); border: 1px solid var(--line); background: var(--surface); overflow: hidden; }
.model.wrong { border-color: var(--bad); }
.model > header {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 9px 12px; border-bottom: 1px solid var(--line); background: var(--surface-2);
}
.model > header .nm { font-weight: 600; font-size: 13.5px; }
.model > header .sp { flex: 1 1 auto; }
.badge {
  font: 700 10px/1 var(--ui); letter-spacing: .05em; text-transform: uppercase;
  padding: 4px 6px; border-radius: 4px;
}
.badge.ok  { background: var(--ok-soft);  color: var(--ok); }
.badge.bad { background: var(--bad-soft); color: var(--bad); }
.badge.warn { background: var(--warn-soft); color: var(--warn); }
.dims { display: flex; gap: 5px; flex-wrap: wrap; padding: 8px 12px 0; }
.dchip {
  font: 600 10.5px/1 var(--ui); padding: 4px 7px; border-radius: 4px;
  background: var(--surface-3); color: var(--ink-3);
}
.dchip.ok  { background: var(--ok-soft);  color: var(--ok); }
.dchip.bad { background: var(--bad-soft); color: var(--bad); }
.mtable { padding: 8px 12px 4px; }
.mtable table { border-collapse: collapse; width: 100%; font-size: 12.5px; }
.mtable td { padding: 5px 6px 5px 0; vertical-align: top; }
.mtable tr + tr td { border-top: 1px solid var(--surface-3); }
.mtable td.p { font: 600 12px var(--mono); white-space: nowrap; width: 1%; }
.stat { padding: 7px 12px; font: 400 11px var(--mono); color: var(--ink-3); border-top: 1px solid var(--line); }
details.extras { border-top: 1px solid var(--line); }
details.extras > summary {
  padding: 7px 12px; cursor: pointer; font-size: 11.5px; color: var(--ink-3);
  list-style: none;
}
details.extras > summary::before { content: "\\25B8  "; }
details[open].extras > summary::before { content: "\\25BE  "; }
details.extras > summary:hover { color: var(--ink); }
.extras-body { padding: 2px 12px 12px; border-top: 1px solid var(--surface-3); }
.extras-body .k {
  font: 600 9.5px/1 var(--ui); letter-spacing: .07em; text-transform: uppercase;
  color: var(--ink-3); margin: 9px 0 3px;
}
.extras-body .thai { font-family: var(--thai); font-size: 13px; line-height: 1.9; color: var(--ink-2); }
.reps { border-top: 1px solid var(--line); padding: 8px 12px; background: var(--warn-soft); }
.reps .k { font: 600 10px/1 var(--ui); letter-spacing: .06em; text-transform: uppercase; color: var(--warn); margin-bottom: 5px; }
.reps .r { font-size: 12px; padding: 2px 0; color: var(--ink-2); }
.reps .r b { font: 600 11px var(--mono); color: var(--ink-3); margin-right: 5px; }

.note {
  background: var(--surface-2); border: 1px solid var(--line);
  border-left: 3px solid var(--accent);
  border-radius: 0 var(--r) var(--r) 0;
  padding: 11px 14px; font-size: 12.5px; color: var(--ink-2); max-width: 78ch;
}
.note b { color: var(--ink); font-weight: 600; }
.note + .note { margin-top: 8px; }

@media (max-width: 900px) {
  body { height: auto; overflow: visible; display: block; }
  .shell { grid-template-columns: 1fr; }
  aside { border-right: 0; border-bottom: 1px solid var(--line); }
  .list { max-height: 300px; }
  main { padding: 16px 14px 40px; }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
"""


JS = r"""
const DATA = JSON.parse(document.getElementById("data").textContent);
const MODELS = DATA.models;
const DIMS = ["call_result", "reason", "product"];
const DIM_NAME = { call_result: "Call outcome", reason: "Reason", product: "Product" };

/* ---------------------------------------------------------------------------
   SCORING -- a transcription of f1_from_atoms in scripts/case_explorer.py.
   It sums precomputed (ground truth, prediction) pairs; it never decides what
   joins to what or what a label means. Those decisions were made by the real
   scorer and are baked into DATA.atoms. selfCheck() below proves the sum
   reproduces the published figures before anyone reads a sliced number.
--------------------------------------------------------------------------- */
function f1(modelKey, dim, keep) {
  const all = DATA.atoms[modelKey][dim];
  const rows = keep ? all.filter(r => keep.has(r[0])) : all;
  const isCR = dim === "call_result";
  let acc = 0, totalW = 0;
  for (const cls of DATA.classes[dim]) {
    let tp = 0, fp = 0, fn = 0;
    for (const r of rows) {
      const g = isCR ? r[1] === cls : (r[1] !== null && r[1].indexOf(cls) !== -1);
      const p = isCR ? r[2] === cls : (r[2] !== null && r[2].indexOf(cls) !== -1);
      if (g && p) tp++; else if (!g && p) fp++; else if (g && !p) fn++;
    }
    const w = tp + fn;
    if (!w) continue;
    const pr = (tp + fp) ? tp / (tp + fp) : 0;
    const rc = tp / w;
    acc += ((pr + rc) ? 2 * pr * rc / (pr + rc) : 0) * w;
    totalW += w;
  }
  return totalW ? acc / totalW : 0;
}

function selfCheck() {
  const bad = [];
  for (const m of MODELS) for (const d of DIMS) {
    const got = f1(m.key, d, null), want = DATA.authoritative_f1[m.key][d];
    if (Math.abs(got - want) > 1e-9) bad.push(`${m.key}/${d}: ${got} vs ${want}`);
  }
  if (bad.length) {
    const el = document.getElementById("selfcheck");
    el.className = "bad";
    el.textContent = "SELF-CHECK FAILED - this page's arithmetic does not reproduce the "
      + "scorer's published figures, so every number shown is suspect: " + bad.join("; ");
  }
  return bad.length === 0;
}

/* --------------------------------- state --------------------------------- */

const state = {
  families: new Set(), slice: "all", verdict: "all",
  focus: "", focusMode: "wrong", unstable: false, multi: false, q: "",
  selected: DATA.items[0].item_id,
};

const esc = s => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;");
const fmt = v => v.toFixed(3);

function matches(it) {
  if (state.families.size && !state.families.has(it.family)) return false;
  if (state.slice !== "all" && it.slice !== state.slice) return false;

  const n = MODELS.filter(m => it.models[m.key].correct).length;
  if (state.verdict === "allright" && n !== MODELS.length) return false;
  if (state.verdict === "anywrong" && n === MODELS.length) return false;
  if (state.verdict === "allwrong" && n !== 0) return false;
  if (state.verdict === "split" && (n === 0 || n === MODELS.length)) return false;

  if (state.focus) {
    const ok = it.models[state.focus].correct;
    if (state.focusMode === "wrong" && ok) return false;
    if (state.focusMode === "right" && !ok) return false;
    if (state.focusMode === "only") {
      for (const m of MODELS) {
        const c = it.models[m.key].correct;
        if (m.key === state.focus ? !c : c) return false;
      }
    }
  }

  if (state.unstable && MODELS.every(m => it.models[m.key].stable)) return false;
  if (state.multi && it.gt.length < 2) return false;

  if (state.q) {
    const q = state.q.toLowerCase();
    const hay = [
      it.item_id, it.call_id, it.family, it.mechanism, it.why_it_matters,
      it.expected_failure, it.turns.map(t => t.text).join(" "),
      it.gt.map(g => [g.product, g.call_result].concat(g.reasons).join(" ")).join(" "),
    ].join(" ").toLowerCase();
    if (hay.indexOf(q) === -1) return false;
  }
  return true;
}

/* ------------------------------- rendering ------------------------------- */

function renderBoard(items) {
  const keep = new Set(items.map(i => i.call_id));
  document.getElementById("slice-n").textContent =
    items.length + " of " + DATA.items.length;
  document.getElementById("slice-note").textContent =
    items.length === DATA.items.length
      ? "the whole eval set - these are the published figures"
      : "recomputed for this slice only";

  const scores = {};
  for (const d of DIMS) scores[d] = MODELS.map(m => f1(m.key, d, keep));

  let html = "<thead><tr><th>Model</th>";
  for (const d of DIMS) html += '<th class="dim">' + DIM_NAME[d] + "</th>";
  html += "</tr></thead><tbody>";

  MODELS.forEach((m, i) => {
    html += '<tr><td class="mname">' + esc(m.short)
      + '<span class="tag' + (m.ours ? '' : ' prod') + '">'
      + (m.ours ? "our gpu" : "production") + "</span></td>";
    for (const d of DIMS) {
      const v = scores[d][i];
      const best = items.length && v === Math.max.apply(null, scores[d]);
      html += '<td><div class="cell' + (best ? " best" : "") + '">'
        + '<span class="v">' + (items.length ? fmt(v) : "--") + "</span>"
        + '<span class="track"><span class="fill" style="width:'
        + (items.length ? (v * 100).toFixed(1) : 0) + '%"></span></span></div></td>';
    }
    html += "</tr>";
  });
  document.getElementById("board").innerHTML = html + "</tbody>";
}

function renderList(items) {
  document.getElementById("list-n").textContent = items.length + " cases";
  if (!items.length) {
    document.getElementById("cases").innerHTML =
      '<div class="empty">No case matches these filters.</div>';
    return;
  }
  let html = "";
  for (const it of items) {
    html += '<div class="case" role="option" tabindex="0" data-id="' + it.item_id
      + '" aria-selected="' + (it.item_id === state.selected) + '">'
      + '<div class="id">' + esc(it.item_id) + "</div>"
      + '<div class="meta">' + esc(it.family) + " &middot; " + it.slice
      + (it.gt.length > 1 ? " &middot; " + it.gt.length + " products" : "")
      + "</div><div class='dots'>";
    for (const m of MODELS) {
      const md = it.models[m.key];
      html += '<span class="dot ' + (md.correct ? "ok" : "bad") + '" title="'
        + esc(m.short) + ": " + (md.correct ? "correct" : "wrong")
        + (md.stable ? "" : " (unstable)") + '">'
        + (md.correct ? "&#10003;" : "&#10007;") + "</span>";
    }
    html += "</div></div>";
  }
  document.getElementById("cases").innerHTML = html;
}

function labels(list, cls) {
  if (!list || !list.length) return '<span class="none">none</span>';
  return list.map(l => '<span class="lab' + (cls ? " " + cls : "") + '">'
    + esc(l) + "</span>").join("");
}

function renderGT(it) {
  const evBy = {};
  for (const e of it.evidence) (evBy[e.dim + ":" + e.label] ||= []).push(e.value);
  const ruleBy = {};
  for (const r of it.rules) ruleBy[r.dim + ":" + r.label] = r.value;

  let html = '<table class="g"><thead><tr><th>Product</th><th>Call outcome</th>'
    + "<th>Reasons</th><th>Why - the words in the call</th></tr></thead><tbody>";
  for (const g of it.gt) {
    const keys = [["call_result", g.call_result]].concat(
      g.reasons.map(r => ["reason", r]));
    keys.unshift(["product", g.product]);
    let ev = "";
    for (const [dim, label] of keys) {
      const spans = evBy[dim + ":" + label] || [];
      const rule = ruleBy[dim + ":" + label];
      if (!spans.length && !rule) continue;
      ev += '<div style="margin-bottom:6px">'
        + '<span class="lab">' + esc(label) + "</span> "
        + (rule ? '<span class="cite">' + esc(rule) + "</span>" : "")
        + spans.map(s => '<span class="ev">&ldquo;' + esc(s) + "&rdquo;</span>").join("")
        + "</div>";
    }
    html += "<tr><td><span class='lab'>" + esc(g.product) + "</span></td>"
      + "<td><span class='lab'>" + esc(g.call_result) + "</span></td>"
      + "<td>" + labels(g.reasons) + "</td>"
      + "<td>" + (ev || '<span class="none">no span recorded</span>') + "</td></tr>";
  }
  return html + "</tbody></table>";
}

function diffRows(it, md) {
  const byP = {};
  for (const g of it.gt) byP[g.product] = { gt: g, pred: null };
  for (const r of md.rows) (byP[r.product] ||= { gt: null, pred: null }).pred = r;

  let html = "<table>";
  for (const p of Object.keys(byP)) {
    const { gt, pred } = byP[p];
    let outcome, reasons;
    if (!pred) {
      outcome = '<span class="lab bad">not answered</span>';
      reasons = '<span class="none">-</span>';
    } else {
      const okOut = gt && pred.call_result === gt.call_result;
      outcome = '<span class="lab ' + (okOut ? "ok" : "bad") + '">'
        + esc(pred.call_result || "none") + "</span>"
        + (gt && !okOut ? ' <span class="cite">want ' + esc(gt.call_result) + "</span>" : "");
      const want = new Set(gt ? gt.reasons : []);
      const got = new Set(pred.reasons);
      const hit = pred.reasons.filter(r => want.has(r));
      const extra = pred.reasons.filter(r => !want.has(r));   // said it, nobody asked
      const miss = (gt ? gt.reasons : []).filter(r => !got.has(r));  // should have said it
      // labels() prints "none" for an empty list, which is right for a whole cell and
      // wrong for one of three concatenated parts -- guard each part, and let the cell
      // fall through to a single "none" only when all three are genuinely empty.
      reasons = (hit.length ? labels(hit, "ok") : "")
        + (extra.length ? labels(extra, "bad") : "")
        + (miss.length ? labels(miss, "gone") : "");
      if (!reasons) reasons = '<span class="none">none</span>';
    }
    if (!gt) outcome += ' <span class="cite">not in ground truth</span>';
    html += '<tr><td class="p">' + esc(p) + "</td><td>" + outcome
      + "</td><td>" + reasons + "</td></tr>";
  }
  return html + "</table>";
}

function renderModel(it, m) {
  const md = it.models[m.key];
  let h = '<div class="model' + (md.correct ? "" : " wrong") + '"><header>'
    + '<span class="nm">' + esc(m.name) + "</span>"
    + '<span class="sp"></span>'
    + (md.stable ? "" : '<span class="badge warn">unstable</span>')
    + '<span class="badge ' + (md.correct ? "ok" : "bad") + '">'
    + (md.correct ? "&#10003; correct" : "&#10007; wrong") + "</span></header>";

  h += '<div class="dims">';
  for (const d of DIMS) {
    h += '<span class="dchip ' + (md.dims[d] ? "ok" : "bad") + '">'
      + DIM_NAME[d] + " " + (md.dims[d] ? "&#10003;" : "&#10007;") + "</span>";
  }
  h += "</div>";

  h += '<div class="mtable">' + diffRows(it, md) + "</div>";

  if (!md.stable && md.reps) {
    h += '<div class="reps"><div class="k">the three repeats disagreed &mdash; '
      + "correct on " + md.hits + " of " + md.reps.length + "</div>";
    md.reps.forEach((rows, i) => {
      const txt = rows.length
        ? rows.map(r => r.product + " / " + (r.call_result || "-")
            + (r.reasons.length ? " / " + r.reasons.join(", ") : "")).join(" &nbsp;|&nbsp; ")
        : "no rows";
      h += '<div class="r"><b>run ' + (i + 1) + (i === 0 ? "*" : "") + "</b>"
        + esc(txt).replace(/&amp;nbsp;/g, "&nbsp;") + "</div>";
    });
    h += '<div class="r" style="margin-top:4px"><span class="cite">'
      + "* run 1 is the one that is scored</span></div></div>";
  }

  const t = md.tokens;
  h += '<div class="stat">in ' + (t.in == null ? "?" : t.in) + " tok &middot; out "
    + (t.out == null ? "?" : t.out) + " tok"
    + (md.latency == null ? "" : " &middot; " + md.latency + " s") + "</div>";

  const x = md.extras;
  const kw = Object.keys(x.keywords || {});
  if (x.recommendation || x.call_event_detection || kw.length) {
    h += '<details class="extras"><summary>Also in the answer, but not scored</summary>'
      + '<div class="extras-body">';
    if (x.call_event_detection) {
      h += '<div class="k">call_event_detection</div><div class="thai">'
        + esc(x.call_event_detection) + "</div>";
    }
    for (const p of kw) {
      for (const slot of Object.keys(x.keywords[p])) {
        h += '<div class="k">' + esc(p) + " &middot; " + esc(slot) + " keyword</div>"
          + '<div class="thai">' + esc(x.keywords[p][slot]) + "</div>";
      }
    }
    if (x.recommendation) {
      h += '<div class="k">recommendation</div><div class="thai">'
        + esc(x.recommendation) + "</div>";
    }
    h += "</div></details>";
  }
  return h + "</div>";
}

function renderDetail(it) {
  const long = it.turns.length > 14;
  let h = '<div class="wrap"><div class="case-head"><h2>' + esc(it.item_id) + "</h2>"
    + '<span class="pill">' + esc(it.family) + "</span>"
    + '<span class="pill">' + esc(it.slice) + "</span>"
    + '<span class="pill">call ' + esc(it.call_id) + "</span>"
    + '<span class="pill">' + it.chars + " chars</span>"
    + "</div>";
  h += '<p class="mech">' + esc(it.mechanism) + "</p>";

  // The same call at three lengths. Jumping between them is the point: it is the only
  // place in the set where transcript length is the ONLY thing that changed.
  if (it.dilation) {
    h += '<div class="dilbar"><span class="k">Same call, three lengths</span>';
    for (const id of it.dilation.members) {
      const other = DATA.items.find(x => x.item_id === id);
      h += '<button type="button" class="dil' + (id === it.item_id ? " on" : "")
        + '" data-goto="' + id + '">' + esc(id)
        + '<span class="c">' + other.chars + " chars</span></button>";
    }
    h += "</div>";
  }

  h += '<section class="blk"><h3>1 &middot; What went in</h3><div class="card">'
    + '<div class="turns' + (long ? "" : " all") + '" id="turns">';
  for (const t of it.turns) {
    const cust = t.who.indexOf("ลูกค้า") === 0;
    h += '<div class="turn' + (cust ? " cust" : "") + '">'
      + '<div class="who">' + (cust ? "customer" : (t.who ? "agent" : "")) + "</div>"
      + '<div class="txt">' + esc(t.text) + "</div></div>";
  }
  h += "</div>" + (long
    ? '<button class="more" id="more">Show all ' + it.turns.length + " turns</button>"
    : "") + "</div></section>";

  h += '<section class="blk"><h3>2 &middot; The right answer, and why</h3>'
    + '<div class="card">' + renderGT(it) + "</div></section>";

  h += '<section class="blk"><h3>3 &middot; Why this case is in the set</h3>'
    + '<div class="note"><b>What it tests.</b> ' + esc(it.why_it_matters) + "</div>"
    + '<div class="note"><b>The failure it was built to catch.</b> '
    + esc(it.expected_failure) + "</div></section>";

  h += '<section class="blk"><h3>4 &middot; What each model answered</h3>'
    + '<div class="note" style="margin-bottom:12px"><b>Correct here means the whole '
    + "answer.</b> Every product, every outcome and every reason must match, on all "
    + "rows of the call. That is stricter than the F1 above, which scores one label at "
    + "a time -- so a case marked wrong can still be mostly right, and a model can lose "
    + "a case over a single extra reason.</div>"
    + '<div class="models">';
  for (const m of MODELS) h += renderModel(it, m);
  h += "</div></section></div>";

  const main = document.getElementById("detail");
  main.innerHTML = h;
  main.scrollTop = 0;
  const btn = document.getElementById("more");
  if (btn) btn.onclick = () => {
    document.getElementById("turns").classList.add("all");
    btn.remove();
  };
  for (const b of main.querySelectorAll("[data-goto]")) {
    b.onclick = () => {
      // A dilation may be filtered out of the current list; select it regardless, so
      // the comparison the button offers actually works.
      state.selected = b.dataset.goto;
      renderDetail(DATA.items.find(i => i.item_id === b.dataset.goto));
      for (const el of document.querySelectorAll(".case")) {
        el.setAttribute("aria-selected", el.dataset.id === state.selected);
      }
    };
  }
}

/* -------------------------------- wiring -------------------------------- */

let visible = [];

function render() {
  visible = DATA.items.filter(matches);
  if (visible.length && !visible.some(i => i.item_id === state.selected)) {
    state.selected = visible[0].item_id;
  }
  renderBoard(visible);
  renderList(visible);
  const sel = DATA.items.find(i => i.item_id === state.selected);
  if (sel && visible.length) renderDetail(sel);
  else if (!visible.length) {
    document.getElementById("detail").innerHTML =
      '<div class="empty">Nothing selected. Widen the filters.</div>';
  }
}

function select(id) {
  state.selected = id;
  for (const el of document.querySelectorAll(".case")) {
    el.setAttribute("aria-selected", el.dataset.id === id);
  }
  renderDetail(DATA.items.find(i => i.item_id === id));
}

function move(delta) {
  const i = visible.findIndex(x => x.item_id === state.selected);
  const next = visible[Math.min(visible.length - 1, Math.max(0, i + delta))];
  if (next && next.item_id !== state.selected) {
    select(next.item_id);
    const el = document.querySelector('.case[data-id="' + next.item_id + '"]');
    if (el) el.scrollIntoView({ block: "nearest" });
  }
}

document.getElementById("cases").addEventListener("click", e => {
  const row = e.target.closest(".case");
  if (row) select(row.dataset.id);
});
document.getElementById("cases").addEventListener("keydown", e => {
  const row = e.target.closest(".case");
  if (row && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); select(row.dataset.id); }
});
document.addEventListener("keydown", e => {
  if (e.target.matches("input, select, textarea")) return;
  if (e.key === "ArrowDown" || e.key === "j") { e.preventDefault(); move(1); }
  if (e.key === "ArrowUp" || e.key === "k") { e.preventDefault(); move(-1); }
});

for (const chip of document.querySelectorAll(".chip[data-family]")) {
  chip.onclick = () => {
    const f = chip.dataset.family;
    if (state.families.has(f)) state.families.delete(f); else state.families.add(f);
    chip.setAttribute("aria-pressed", state.families.has(f));
    render();
  };
}
const bind = (id, key, prop) => {
  document.getElementById(id).addEventListener("change", e => {
    state[key] = prop === "checked" ? e.target.checked : e.target.value;
    render();
  });
};
bind("f-slice", "slice"); bind("f-verdict", "verdict");
bind("f-focus", "focus"); bind("f-focusmode", "focusMode");
bind("f-unstable", "unstable", "checked"); bind("f-multi", "multi", "checked");
document.getElementById("f-q").addEventListener("input", e => {
  state.q = e.target.value.trim(); render();
});
document.getElementById("reset").onclick = () => {
  state.families.clear(); state.slice = "all"; state.verdict = "all";
  state.focus = ""; state.focusMode = "wrong"; state.unstable = false;
  state.multi = false; state.q = "";
  for (const c of document.querySelectorAll(".chip[data-family]")) {
    c.setAttribute("aria-pressed", "false");
  }
  for (const [id, v] of [["f-slice", "all"], ["f-verdict", "all"],
                          ["f-focus", ""], ["f-focusmode", "wrong"], ["f-q", ""]]) {
    document.getElementById(id).value = v;
  }
  document.getElementById("f-unstable").checked = false;
  document.getElementById("f-multi").checked = false;
  render();
};

const root = document.documentElement;
document.getElementById("theme").onclick = () => {
  const dark = root.getAttribute("data-theme") === "dark"
    || (!root.getAttribute("data-theme")
        && matchMedia("(prefers-color-scheme: dark)").matches);
  root.setAttribute("data-theme", dark ? "light" : "dark");
};

selfCheck();
render();
"""


def render(data: dict) -> str:
    families: dict[str, int] = {}
    for item in data["items"]:
        families[item["family"]] = families.get(item["family"], 0) + 1

    chips = "".join(
        '<button class="chip" data-family="{f}" aria-pressed="false" type="button">'
        "{f}<span class=\"n\">{n}</span></button>".format(f=f, n=n)
        for f, n in sorted(families.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    # The dots in the case list are positional, one column per model in board order.
    # A tooltip is not enough when the point of the page is walking a room through it,
    # so the column gets a printed code. Explicit rather than derived: an abbreviation
    # a reader has to decode is worse than no abbreviation.
    codes = {"gemini": "GEM", "qwen38": "Q3.8", "qwen36": "Q3.6", "gemma": "GM4"}
    legend = "".join(
        "<span>{}</span>".format(codes.get(m["key"], m["key"][:4].upper()))
        for m in data["models"]
    )
    focus_opts = "".join(
        '<option value="{k}">{s}</option>'.format(k=m["key"], s=m["short"])
        for m in data["models"]
    )
    runs = " &middot; ".join(
        "{}: {}".format(m["short"], m["run_id"]) for m in data["models"]
    )

    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # `<` only ever appears inside JSON string values here, so escaping it is both safe
    # and sufficient to keep a transcript from closing the script element early.
    blob = blob.replace("<", "\\u003c")

    # A complete document, not a fragment: this file is opened straight off disk, so it
    # gets no wrapper and must declare its own encoding. Without the charset the Thai
    # transcripts render as mojibake -- verified, not assumed.
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Retention Eval Case Explorer</title>
<style>{CSS}</style>
</head>
<body>

<div class="top">
  <h1>Retention eval &mdash; case explorer</h1>
  <span class="sub">138 calls &middot; ground truth, four models, one case at a time</span>
  <span class="spacer"></span>
  <span class="pill">retention_v3</span>
  <span class="pill">scored run: repeat 1 of 3</span>
  <button class="tbtn" id="theme" type="button">Light / dark</button>
</div>
<div id="selfcheck"></div>

<div class="board">
  <div class="board-head">
    <strong>Weighted F1</strong>
    <span>for <span id="slice-n"></span> cases &mdash; <span id="slice-note"></span></span>
  </div>
  <table class="board-t" id="board"></table>
</div>

<div class="shell">
  <aside>
    <div class="filters">
      <div class="frow">
        <span class="flabel">What the case tests</span>
        <div class="chips">{chips}</div>
      </div>
      <div class="frow two">
        <div>
          <span class="flabel">Split</span>
          <select id="f-slice">
            <option value="all">Tune + holdout</option>
            <option value="tune">Tune only</option>
            <option value="holdout">Holdout only</option>
          </select>
        </div>
        <div>
          <span class="flabel">Who got it right</span>
          <select id="f-verdict">
            <option value="all">Any</option>
            <option value="allright">All four correct</option>
            <option value="anywrong">At least one wrong</option>
            <option value="split">They disagreed</option>
            <option value="allwrong">All four wrong</option>
          </select>
        </div>
      </div>
      <div class="frow two">
        <div>
          <span class="flabel">Single model</span>
          <select id="f-focus"><option value="">Any model</option>{focus_opts}</select>
        </div>
        <div>
          <span class="flabel">&nbsp;</span>
          <select id="f-focusmode">
            <option value="wrong">got it wrong</option>
            <option value="right">got it right</option>
            <option value="only">was the only one right</option>
          </select>
        </div>
      </div>
      <div class="frow">
        <label class="check">
          <input type="checkbox" id="f-unstable"> Only cases where a model changed its answer between repeats
        </label>
        <label class="check">
          <input type="checkbox" id="f-multi"> Only calls with more than one product
        </label>
      </div>
      <div class="frow">
        <span class="flabel">Search transcript, labels or notes</span>
        <input type="search" id="f-q" placeholder="e.g. network, TVS, dilation">
      </div>
      <div class="frow">
        <button class="tbtn" id="reset" type="button">Reset filters</button>
      </div>
    </div>
    <div class="list">
      <div class="list-head">
        <span id="list-n"></span>
        <span class="legend">{legend}</span>
      </div>
      <div id="cases" role="listbox" aria-label="Test cases"></div>
    </div>
  </aside>
  <main id="detail"></main>
</div>

<script type="application/json" id="data">{blob}</script>
<script>{JS}</script>
<!-- runs: {runs} -->
</body>
</html>
"""

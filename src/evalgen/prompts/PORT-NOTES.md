# Port notes: retention prompt assets

What was copied out of True's production tree, where it came from, and every character
that changed on the way. Written so a reviewer can re-derive both files from the
production sources without running anything.

Production tree (tracked as a reference copy, read-only, never edited):
`production-reference/sentiment-batch-retention-main/`

| Asset | Source | sha256 |
|---|---|---|
| `retention_wrapper.txt` | `config/system_prompt/retention.yml`, the `system_prompt: \|` block scalar, dedented by 4, **plus the three fixes below** | `d3e5b4b36a2143ab86557bc882ba745fac6951d13f7628236b2f9379e39d96f0` |
| `retention_v9_16_body.txt` | `src/prompt.py` lines 4318-4409 inclusive, verbatim | `aca86f29771f3e28ca5d854f019c99c38494846eb04f6decc1ca59223217cb61` |

Both files: UTF-8, no BOM, LF, single trailing newline.

## Why these are files at all

`production-reference/` is tracked, but reading the prompt from it live at run time would
still be wrong: this snapshot is reviewed and sha256-pinned, so a later change to the
reference tree is a diff someone sees rather than a prompt that silently changes
underneath a scheduled run. The assets carry the prompt; the production tree is only
provenance.

## Boundaries, verified rather than assumed

- `prompt.py:4313` opens `prompt_v9_16 = """`; `:4314` is the **Role** line.
- `prompt.py:4318` is `**Analysis Requirements**:` — the first line of the body.
- `prompt.py:4409` is `5. recommendation: Suggestion how to keep client loyalty to brand`
  — the last. `:4411` starts `Output Format:`, which the wrapper already supplies.

That overlap is the point. The wrapper contributes Role/Situation/Objective, Output
Format, the Reminders and the example; the body contributes only the analysis rules.
Substituting the **whole** `prompt_v9_16` monolith into `{user_prompt}` instead of its
middle produces a prompt that states its Role twice and its Output Format twice. It runs,
returns JSON and scores — it is just not the prompt production sends. `tests/test_prompts.py`
asserts the Role line appears exactly once for this reason.

## How production joins them

`main.py:1157-1160` (identically `modules/fact_checker.py:341-347`):

```python
system_prompt = yaml.safe_load(open('./config/system_prompt/retention.yml'))['system_prompt']
prompt_text   = system_prompt.replace("{user_prompt}", user_prompt)
```

`user_prompt` is fetched from SharePoint at run time (`main.py:1140-1156`), so it is not
in the repository. The v9_16 body is the copy that was live when this harness was built.

## The three fixes, and why they are invisible in production

All three live in the wrapper's worked example. Vertex is called with **forced function
calling** — `main.py:1109-1114`, `"toolConfig": {"functionCallingConfig": {"mode": "ANY"}}`
— with `get_analysis_schema()` as the declaration (`main.py:1086`). The tool schema
decides the response shape, so Gemini never has to obey the example. This harness passes
the schema through `response_format`, which is advisory in a way `mode: ANY` is not. The
example stops being decoration and becomes the thing a weaker model copies. Everything
below therefore goes live for Qwen and has been dormant for Gemini.

### (a) The example JSON was syntactically invalid

`json.loads` on the original block:

```
JSONDecodeError: Expecting ',' delimiter: line 19 column 17 (char 586)
```

`network_issue`'s members were separated by newlines and nothing else. Seven commas were
missing (four in `network_issue`, three in its nested `area`). Line numbers are in the
dedented wrapper.

| Line | Before | After |
|---|---|---|
| 34 | `"issue_type": "Speed"` | `"issue_type": "Speed",` |
| 35 | `"sub_reason": "Complete signal loss in residential area since last week"` | same + `,` |
| 36 | `"problem_statement_list": ["เน็ตช้ามากจนดูวิดีโอไม่ได้เลย", "สัญญาณไม่เสถียรทำให้โทรออกบ่อยๆไม่ได้"]` | same + `,` |
| 37 | `"churn_probability": 75` | `"churn_probability": 75,` |
| 39 | `"area_tag_province": "province"` | `"area_tag_province": "province",` |
| 40 | `"area_tag_district": "district"` | `"area_tag_district": "district",` |
| 41 | `"area_tag_sub_district": "sub_district"` | `"area_tag_sub_district": "sub_district",` |

`area_tag_landmark` was already last in its object and correctly had no comma. Verified
with `json.loads` before (fails, above) and after (parses; top-level keys `product`,
`call_event_detection`, `recommendation`).

### (b) The example used a key the schema does not declare

**Read this one carefully: it is not the defect the brief predicted.** The brief expected
title-case enum values. Every enum-valued field in `retention.yml`'s example was dumped
and checked against `get_analysis_schema()`:

| Field | Example value | Schema enum | Verdict |
|---|---|---|---|
| `reason` (×3) | `network`, `save cost`, `dissatisfied service` | lowercase list, `main.py:972` | already correct |
| `retention_outcome` | `save` | `["churn","save","unknown","undefined"]`, `main.py:1018` | already correct |
| `issue_type` | `Speed` | `["Speed","Outage",...]`, `main.py:989` — capitalised **in the schema** | already correct |
| `call_event_detection` | `Market-Driven Events (เหตุการณ์ทางการตลาด)` | `main.py:1046` | already correct |

So there was no case fix to make. The title-case defect the brief describes is real, but
it lives in `prompt.py`'s own example (`:4292` `"Network"`, `:4296` `"Save Cost"`, `:4300`
`"Dissatisfied service"`) — which is outside the 4318-4409 body and is **not** what
production loads. `retention.yml` is the newer, already-lowercased copy, and it is the
one `main.py:1157` opens.

What *is* wrong in the yml example is the same class of bug — the example contradicts the
schema, and forced function calling hides it:

| | Before | After |
|---|---|---|
| reason-object key (×3: main, secondary, third) | `"Phrase": "คำพูดของลูกค้าที่สื่อถึงเหตุผล"` | `"keyword": "คำพูดของลูกค้าที่สื่อถึงเหตุผล"` |

`PRODUCT_REASON_SCHEMA` (`main.py:967-981`) declares exactly two properties, `reason` and
`keyword`, and lists **both** in `required`. The example emitted `Phrase` and no
`keyword`, so it did not satisfy the schema it is shown alongside. Under `mode: ANY` that
never mattered. Under `response_format` a model that copies the example emits an object
missing a required key — a `schema_violation` in `outcomes.classify`, scored as a
coverage loss against Qwen for a mistake the prompt made.

Known residue, deliberately not fixed: the **body** still calls this field `Phrase`
(`prompt.py:4383, 4385, 4387`). The body is a verbatim extract and editing its prose is a
prompt change, not a port. The example now agrees with the schema, which is the copy the
model is graded against.

### (c) The example emitted three products at once

Reminder #2 in the same wrapper says *"Output Json don't have to contain all product, only
product that mentioned in call"*, and the example then emitted `Postpaid`, `TOL` **and**
`TVS`. A model that copies the example over-reports products, and product is a scored
join key — `(call_id, phone_number, product)` — so a spurious product is a spurious row
that fails to match any ground-truth row.

Before: `product` had `Postpaid`, `TOL`, `TVS`.
After: `product` has `Postpaid` only.

`Postpaid` was kept because it is the block carrying a fully populated `network_issue`,
so dropping the other two loses no structure the example was demonstrating. The trailing
comma on Postpaid's closing brace was removed so `product` still terminates. The dropped
text was the `TOL` block (its `network_issue` carried the same seven missing commas) and
the `TVS` block (`"network_issue": null`).

## Not fixed, recorded instead

- **`prompt.py:4384, 4386` contain a Thai typo**, `ต้องเ็นคำพูด` for `ต้องเป็นคำพูด`
  ("must be speech from the customer side"). Present in production, carried through
  verbatim. Fixing it would change the prompt under test.
- **`Campaign-Drvien Events`** is misspelled in both the schema enum and the prompt. It is
  a literal enum member: correcting one copy and not the other would make the model's
  output unmatchable, so both keep the typo.
- **The schema's own descriptions still say "audio"** — `main.py:977`, *"List keywords or
  short phrases directly from the audio"*. `src/evalgen/schemas/retention.json` is a
  faithful transcription and was not rewritten. The transcript substitution list covers
  the prompt only. If a run shows models refusing on modality grounds, this is the next
  place to look.

## Schema transcription (`src/evalgen/schemas/retention.json`)

From `main.py:955-1056`, `get_analysis_schema()`. Verified by lifting that function out of
`main.py` with `ast`, executing it in isolation, applying the transform below and
comparing to the committed file — exact match.

- Vertex types lowered: `OBJECT`→`object`, `STRING`→`string`, `ARRAY`→`array`,
  `INTEGER`→`integer`.
- `"nullable": true` is OpenAPI 3.0, not JSON Schema, and an unknown keyword is ignored
  rather than rejected — leaving it would silently make `network_issue: null` invalid,
  which is exactly what the original TVS example emitted. Folded into a type union
  instead: `"type": ["object", "null"]` for `network_issue` (`main.py:985`) and
  `["string", "null"]` for the four `area_tag_*` fields (`main.py:1000-1003`).
- The shared Python dicts (`PRODUCT_REASON_SCHEMA`, `NETWORK_ISSUE_SCHEMA`,
  `PRODUCT_ANALYSIS_SCHEMA`) are expanded inline rather than expressed as `$defs`/`$ref`.
  That is what Vertex receives — Python evaluates the references before the request is
  built — and `$ref` support is uneven across providers, so expansion is both the
  faithful and the safe choice. It is why the file is long.
- `required` lists kept as-is: `["reason","keyword"]`, `["main","retention_outcome"]`,
  `["product","call_event_detection","recommendation"]`. Note that `keyword` is required
  while `secondary`/`third` are not.
- `additionalProperties: false` kept on the `product` object only, exactly as production
  has it (`main.py:1042`).
- The function declaration's `name` and `description` (`main.py:1028-1029`) are carried as
  JSON Schema `title` and `description` so the transcription loses nothing. The file *is*
  the schema: a caller building `response_format` wraps it, it does not unwrap the file.

This is **not** an OpenAI strict-mode schema. Strict mode additionally requires
`additionalProperties: false` on every object and every property in `required`, which
would change the contract production actually uses. Send it non-strict.

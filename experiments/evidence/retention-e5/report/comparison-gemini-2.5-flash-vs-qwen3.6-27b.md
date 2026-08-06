# gemini-2.5-flash vs qwen3.6-27b

**RECONCILED: NO.**

Decision: **FAIL** — parse reliability 359/414 is below 99%; quality BEHIND on call_result, reason; stability is BEHIND

| Slice | Dimension | d | Net | Band | Verdict |
|---|---|---:|---:|---:|---|
| phase_one | call_result | 22 | -18 | 12 | BEHIND |
| phase_one | reason | 34 | -14 | 14 | BEHIND |
| phase_one | product | 3 | +1 | — | UNDERPOWERED |
| phase_two | call_result | 1 | -1 | — | UNDERPOWERED |
| phase_two | reason | 13 | -5 | 9 | INDISTINGUISHABLE |
| phase_two | product | 1 | -1 | — | UNDERPOWERED |
| full | call_result | 23 | -19 | 13 | BEHIND |
| full | reason | 47 | -19 | 17 | BEHIND |
| full | product | 4 | +0 | — | UNDERPOWERED |

Item-level regressions: 56. See the paired JSON artifact for every hashed regression row.

# gemini-2.5-flash vs qwen3.6-35b-a3b

**RECONCILED: NO.**

Decision: **FAIL** — quality BEHIND on call_result, reason, product; stability is BEHIND

| Slice | Dimension | d | Net | Band | Verdict |
|---|---|---:|---:|---:|---|
| phase_one | call_result | 11 | -7 | 9 | INDISTINGUISHABLE |
| phase_one | reason | 25 | -15 | 13 | BEHIND |
| phase_one | product | 6 | -6 | 6 | BEHIND |
| phase_two | call_result | 4 | -4 | — | UNDERPOWERED |
| phase_two | reason | 15 | -9 | 11 | INDISTINGUISHABLE |
| phase_two | product | 4 | -4 | — | UNDERPOWERED |
| full | call_result | 15 | -11 | 11 | BEHIND |
| full | reason | 40 | -24 | 16 | BEHIND |
| full | product | 10 | -10 | 8 | BEHIND |

Item-level regressions: 55. See the paired JSON artifact for every hashed regression row.

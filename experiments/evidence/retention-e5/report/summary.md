# Experiment 5 summary

**RECONCILED: NO. This is harness output, not a migration verdict.**

Plan: `2823d3359f6ca6dee601f27b84672ef100971b609bdf38368a56990f2e323c8e`

| Arm | Parse valid | p50 latency | p95 latency | Cost lower bound |
|---|---:|---:|---:|---:|
| gemini-2.5-flash | 413/414 | 2.140s | 3.875s | $0.434660 |
| qwen3.6-27b | 359/414 | 4.140s | 8.141s | $0.485823 |
| qwen3.6-35b-a3b | 414/414 | 2.938s | 5.625s | $0.329825 |

## qwen3.6-27b vs gemini-2.5-flash

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

Item-level regressions: 56

| Item key | Dimension | Ground truth | Incumbent | Candidate | Error |
|---|---|---|---|---|---|
| ce895067afe77452 | call_result | save | save | <empty> | missing_output |
| e3caff81083c5f96 | call_result | save | save | <empty> | missing_output |
| 48e54217e1dd264b | call_result | save | save | <empty> | missing_output |
| bd0541ea4b93ecb9 | call_result | churn | churn | <empty> | missing_output |
| 1d8ab7497aedd269 | call_result | unknown | unknown | <empty> | missing_output |
| 44bac0542217ca28 | call_result | churn | churn | <empty> | missing_output |
| d0267c8b3e71825d | call_result | unknown | unknown | <empty> | missing_output |
| 0e563ac30d956674 | call_result | churn | churn | <empty> | missing_output |
| d215928fbabac123 | call_result | save | save | <empty> | missing_output |
| e73fac7e754355a6 | call_result | churn | churn | <empty> | missing_output |
| 145c6e0cdd595470 | call_result | save | save | <empty> | missing_output |
| 48d8be89aafaf103 | call_result | churn | churn | <empty> | missing_output |
| 8c15ef07575ca469 | call_result | churn | churn | <empty> | missing_output |
| ea6e753cab977992 | call_result | churn | churn | <empty> | missing_output |
| f62ccfa2c1e58ca5 | call_result | churn | churn | <empty> | missing_output |
| 0392b94975fde71f | call_result | save | save | <empty> | missing_output |
| 19ac4acc1901db71 | call_result | unknown | unknown | <empty> | missing_output |
| 0954c6a3bc9520ab | call_result | undefined | undefined | <empty> | missing_output |
| 0c36aba91d727501 | call_result | save | save | <empty> | missing_output |
| e0b491b0f9d18873 | call_result | churn | churn | <no prediction> | missing_output |
| 79a58f8e07174bdb | call_result | churn | churn | <no prediction> | missing_output |
| 334dd97404cd2340 | reason | save cost | save cost | promotion related, save cost | wrong_label |
| 2c060e92814d0ea7 | reason | dissatisfied service, network | dissatisfied service, network | dissatisfied service, network, save cost | wrong_label |
| 48e54217e1dd264b | reason | save cost | save cost | <empty> | missing_output |
| bd0541ea4b93ecb9 | reason | save cost | save cost | <empty> | missing_output |
| 44bac0542217ca28 | reason | dissatisfied service, network, promotion related | dissatisfied service, network, promotion related | <empty> | missing_output |
| d0267c8b3e71825d | reason | contract end, network, promotion related | contract end, network, promotion related | <empty> | missing_output |
| 0e563ac30d956674 | reason | device promotion related | device promotion related | <empty> | missing_output |
| d215928fbabac123 | reason | network | network | <empty> | missing_output |
| d3798b95ff997861 | reason | contract end | contract end | contract end, save cost | wrong_label |
| b2122a3912142832 | reason | promotion related | promotion related | promotion related, save cost | wrong_label |
| f1f1ce60abc1855b | reason | other | other | other, save cost | wrong_label |
| 145c6e0cdd595470 | reason | device promotion related | device promotion related | <empty> | missing_output |
| c36f0e37f5a024b5 | reason | device promotion related | device promotion related | device promotion related, promotion related | wrong_label |
| 9768e3ca1589a871 | reason | customer reason | customer reason | customer reason, other | wrong_label |
| b5b68ded5d412ead | reason | other | other | other, save cost | wrong_label |
| 180f8337de5fc80b | reason | device promotion related | device promotion related | device promotion related, save cost | wrong_label |
| 43b03029b2685006 | reason | sale upsell problem | sale upsell problem | dissatisfied service, sale upsell problem | wrong_label |
| eb59d1c4bd91be99 | reason | dissatisfied service | dissatisfied service | dissatisfied service, save cost | wrong_label |
| ea6e753cab977992 | reason | other | other | <empty> | missing_output |
| f62ccfa2c1e58ca5 | reason | device promotion related | device promotion related | <empty> | missing_output |
| 0392b94975fde71f | reason | other | other | <empty> | missing_output |
| 19ac4acc1901db71 | reason | device promotion related | device promotion related | <empty> | missing_output |
| 0c36aba91d727501 | reason | sale upsell problem | sale upsell problem | <empty> | missing_output |
| e0b491b0f9d18873 | reason | other | other | <no prediction> | missing_output |
| f634e4897c1a6083 | reason | network | network | network, other, save cost | wrong_label |
| 867a1f675a80b4e6 | reason | dissatisfied service, network, promotion related | dissatisfied service, network, promotion related | dissatisfied service, network, save cost | wrong_label |
| 774b618bc52a1ee3 | reason | promotion related | promotion related | promotion related, save cost | wrong_label |
| 1bb1d936aca6f84d | reason | contract end | contract end | contract end, save cost | wrong_label |
| b9ee10c48ac9e6b1 | reason | sale upsell problem | sale upsell problem | dissatisfied service, sale upsell problem | wrong_label |
| b6de5097b837733e | reason | network | network | dissatisfied service, network | wrong_label |
| 478ae7c84dc9cdac | reason | down sell not success | down sell not success | dissatisfied service, down sell not success | wrong_label |
| 79a58f8e07174bdb | reason | customer reason | customer reason | <no prediction> | missing_output |
| 919545f9e0e62a34 | reason | promotion related | promotion related | promotion related, sale upsell problem | wrong_label |
| e0b491b0f9d18873 | product | unknown | unknown | <no prediction> | missing_output |
| 79a58f8e07174bdb | product | unknown | unknown | <no prediction> | missing_output |

## qwen3.6-35b-a3b vs gemini-2.5-flash

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

Item-level regressions: 55

| Item key | Dimension | Ground truth | Incumbent | Candidate | Error |
|---|---|---|---|---|---|
| ce895067afe77452 | call_result | save | save | <no prediction> | missing_output |
| d2ab220e1bd1a817 | call_result | save | save | <no prediction> | missing_output |
| cdd8f4b09b86425c | call_result | undefined | undefined | save | wrong_label |
| 014cab6a3d986edf | call_result | save | save | <no prediction> | missing_output |
| 0954c6a3bc9520ab | call_result | undefined | undefined | save | wrong_label |
| 0c36aba91d727501 | call_result | save | save | <no prediction> | missing_output |
| e0b491b0f9d18873 | call_result | churn | churn | <no prediction> | missing_output |
| c1765f186990802e | call_result | undefined | undefined | save | wrong_label |
| 2ff13a40a4b2c12d | call_result | unknown | unknown | <no prediction> | missing_output |
| 66941673bcc88ad1 | call_result | unknown | unknown | <no prediction> | missing_output |
| 6cb29f4a202ec026 | call_result | unknown | unknown | <no prediction> | missing_output |
| 9c58b07db0cc943e | call_result | save | save | <no prediction> | missing_output |
| 79a58f8e07174bdb | call_result | churn | churn | <no prediction> | missing_output |
| ee61d34cef1e9b38 | reason | promotion related | promotion related | contract end, customer reason, promotion related | wrong_label |
| 2c060e92814d0ea7 | reason | dissatisfied service, network | dissatisfied service, network | dissatisfied service, network, save cost | wrong_label |
| 48e54217e1dd264b | reason | save cost | save cost | down sell not success, save cost | wrong_label |
| 44bac0542217ca28 | reason | dissatisfied service, network, promotion related | dissatisfied service, network, promotion related | dissatisfied service, network, save cost | wrong_label |
| d3798b95ff997861 | reason | contract end | contract end | contract end, device promotion related, save cost | wrong_label |
| b2122a3912142832 | reason | promotion related | promotion related | promotion related, save cost | wrong_label |
| 1a25259cb3691b59 | reason | promotion related | promotion related | contract end, promotion related, save cost | wrong_label |
| d2ab220e1bd1a817 | reason | network | network | <no prediction> | missing_output |
| 145c6e0cdd595470 | reason | device promotion related | device promotion related | device promotion related, save cost | wrong_label |
| 5289abb970e4f0ad | reason | other | other | other, save cost | wrong_label |
| c36f0e37f5a024b5 | reason | device promotion related | device promotion related | customer reason, device promotion related, down sell not success | wrong_label |
| 014cab6a3d986edf | reason | network | network | <no prediction> | missing_output |
| 7a66332231247ee7 | reason | network, save cost | network, save cost | device promotion related, network, save cost | wrong_label |
| 536d4a3a69a6f420 | reason | promotion related | promotion related | promotion related, save cost | wrong_label |
| 96aca6cedc44415a | reason | save cost | save cost | other, save cost | wrong_label |
| 43b03029b2685006 | reason | sale upsell problem | sale upsell problem | down sell not success, sale upsell problem, save cost | wrong_label |
| eb59d1c4bd91be99 | reason | dissatisfied service | dissatisfied service | dissatisfied service, save cost | wrong_label |
| 0c36aba91d727501 | reason | sale upsell problem | sale upsell problem | <no prediction> | missing_output |
| e0b491b0f9d18873 | reason | other | other | <no prediction> | missing_output |
| 2ff13a40a4b2c12d | reason | save cost | save cost | <no prediction> | missing_output |
| f634e4897c1a6083 | reason | network | network | network, save cost | wrong_label |
| d99b8e2134aa686d | reason | save cost | save cost | promotion related, save cost | wrong_label |
| 66941673bcc88ad1 | reason | <empty> | <empty> | <no prediction> | missing_output |
| 774b618bc52a1ee3 | reason | promotion related | promotion related | promotion related, save cost | wrong_label |
| b9ee10c48ac9e6b1 | reason | sale upsell problem | sale upsell problem | dissatisfied service, sale upsell problem | wrong_label |
| 30e2995528d6f277 | reason | save cost | save cost | down sell not success, save cost | wrong_label |
| b6de5097b837733e | reason | network | network | dissatisfied service, network, other | wrong_label |
| 9c58b07db0cc943e | reason | network | network | <no prediction> | missing_output |
| 478ae7c84dc9cdac | reason | down sell not success | down sell not success | dissatisfied service, promotion related, save cost | wrong_label |
| 79a58f8e07174bdb | reason | customer reason | customer reason | <no prediction> | missing_output |
| 919545f9e0e62a34 | reason | promotion related | promotion related | dissatisfied service, promotion related | wrong_label |
| 5aa8de94efc96bd8 | reason | other | other | save cost | wrong_label |
| ce895067afe77452 | product | postpaid | postpaid | <no prediction> | missing_output |
| d2ab220e1bd1a817 | product | postpaid | postpaid | <no prediction> | missing_output |
| 014cab6a3d986edf | product | postpaid | postpaid | <no prediction> | missing_output |
| 0c36aba91d727501 | product | unknown | unknown | <no prediction> | missing_output |
| e0b491b0f9d18873 | product | unknown | unknown | <no prediction> | missing_output |
| 2ff13a40a4b2c12d | product | unknown | unknown | <no prediction> | missing_output |
| 66941673bcc88ad1 | product | tvs | tvs | <no prediction> | missing_output |
| 6cb29f4a202ec026 | product | tvs | tvs | <no prediction> | missing_output |
| 9c58b07db0cc943e | product | postpaid | postpaid | <no prediction> | missing_output |
| 79a58f8e07174bdb | product | unknown | unknown | <no prediction> | missing_output |

## Load

| Arm | Concurrency | Parse valid | Calls/s | p50 | p95 | p99 |
|---|---:|---:|---:|---:|---:|---:|
| gemini-2.5-flash | 1 | 24/24 | 0.337 | 2.343s | 5.141s | 6.125s |
| gemini-2.5-flash | 4 | 24/24 | 1.411 | 2.141s | 4.266s | 4.407s |
| gemini-2.5-flash | 8 | 24/24 | 2.423 | 3.063s | 4.093s | 5.453s |
| qwen3.6-27b | 1 | 22/24 | 0.161 | 4.390s | 13.625s | 18.110s |
| qwen3.6-27b | 4 | 24/24 | 0.686 | 4.406s | 10.437s | 10.687s |
| qwen3.6-27b | 8 | 23/24 | 0.726 | 8.437s | 17.734s | 18.203s |
| qwen3.6-35b-a3b | 1 | 24/24 | 0.374 | 2.391s | 3.750s | 6.453s |
| qwen3.6-35b-a3b | 4 | 24/24 | 1.256 | 2.828s | 4.297s | 5.359s |
| qwen3.6-35b-a3b | 8 | 24/24 | 1.958 | 3.594s | 5.188s | 5.250s |

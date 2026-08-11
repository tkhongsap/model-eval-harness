# Retention Challenge v1 — research and dataset plan

**Status:** implemented; design matrix was fixed before transcript authoring
**Dataset ID:** `retention_challenge_v1`
**Target:** exactly 50 synthetic Thai calls, scored independently of frozen `retention_v1`–`v3`

## Why this is a separate pack

`retention_v1`–`v3` are cumulative and already cited by experiments. This pack does not
extend that byte-identical chain. Its items use `RTC-001`–`RTC-050` so a result cannot be
mistaken for a result on `RET-001`–`RET-138`. The pack keeps the same production Retention
label space and scorer grain, but concentrates its budget on difficult conversational
structure rather than broad baseline coverage.

No real call, customer identifier, account fact, or private operational script is used.
The conversations are original synthetic compositions. Public sources inform *situations
and interaction properties*, never wording.

## Research basis

Research was reviewed on 2026-08-11. The design implications below are the claims used;
the sources are not treated as label authorities. Labels remain governed only by the
committed production prompt citations carried by every item.

1. [NBTC complaint statistics, February 2025](https://nbtc.go.th/tcp/NBTC_TCP/media/Source/Media-of-Thumnail/Statistics-%E0%B9%80%E0%B8%94%E0%B8%AD%E0%B8%99-%E0%B8%81-%E0%B8%9E-68_1.pdf?ext=.pdf)
   list mobile data/voice quality, unconsented condition changes, unsolicited charged SMS,
   fixed-internet cancellation cost, inability to change promotions, and service failure
   among leading complaint issues. The pack combines these facts instead of giving each
   call one isolated keyword.
2. [True Online relocation guidance](https://www.true.th/help/trueonline/internet-relocation)
   says relocation depends on coverage and account status and restarts the service term.
   Relocation calls therefore contain plausible retention alternatives, contract talk,
   address uncertainty, and final decisions that differ from the opening request.
3. [True guidance for unusually high bills](https://www.true.th/help/billing/check-bill/bill-high)
   distinguishes recurring packs, direct carrier billing, excess usage, and roaming.
   The pack uses billing detail as either a licensed cancellation reason, a distractor,
   or a genuinely out-of-scope `undefined` call depending on what the customer decides.
4. [True roaming guidance](https://www.true.th/en/international/roaming) describes package
   expiry, pay-per-use exposure, and service suspension risk. These become realistic
   history and cost context; they do not create a new reason label.
5. [Mirage: A Diagnostic Framework for Evaluating the Realism of Synthetic Contact Center
   Dialogue Generation](https://aclanthology.org/2026.findings-acl.1261/) reports synthetic
   dialogue gaps in sentiment arcs, linguistic complexity, interaction style, disfluency,
   behavioral variation, and conversational properties. Each item below therefore has a
   planned interaction arc, not just an intent and response.
6. [Text-Based Detection of On-Hold Scripts in Contact Center Calls](https://arxiv.org/abs/2407.09849)
   treats putting a customer on hold, returning from hold, and irrelevant turns as
   distinct call phenomena. Holds in this pack have a reason and a return turn; they are
   not repeated filler.
7. [NatCS: Eliciting Natural Customer Support Dialogues](https://arxiv.org/abs/2305.03007)
   explains why short written task-oriented dialogue is not representative of natural
   customer support. The pack includes retelling, clarification, repair, interruptions,
   backchannels, topic return, and explicit end-state negotiation.

## Quantitative acceptance contract

- Exactly **50** items, `RTC-001`–`RTC-050`.
- Synthetic call IDs `5201`–`5250` and phones `0810000201`–`0810000250`.
- Five families of ten items each.
- At least 18 speaker turns per item; no turn above the repository's 120-codepoint cap.
- Every item contains at least two of: prior-contact history, hold/transfer, correction or
  restart, competing issue, agent offer, negated distractor, conditional decision,
  interruption/background event, or explicit topic return.
- At least 10 multi-product items and at least three calls with three product rows.
- All 19 production classes (4 product, 4 outcome, 11 reason) have support; every reason
  class has at least three supporting items.
- Every scored label has one unique verbatim evidence span and a production rule citation.
- Reason evidence appears in customer speech, never only in agent speech.
- `validate()` returns no problems; embedded ground truth and CSV agree exactly.
- Existing frozen packs, prompts, label spaces, and scorer code remain byte-unchanged.

## Design matrix — labels fixed before transcript authoring

`P` = Postpaid, `T` = TOL, `V` = TVS, `U` = unknown. Multiple rows separated by `;`.

| ID | Family | Ground truth rows | Primary complexity mechanism |
|---|---|---|---|
| RTC-001 | compound_history | P/save/network + promotion related | old billing story competes with current coverage failure; repair accepted |
| RTC-002 | compound_history | T/churn/promotion related + sale upsell problem | price changed after a sales promise; two prior contacts |
| RTC-003 | compound_history | V/churn/dissatisfied service + network | signal fault matters, but repeated technician no-shows also independently drive churn |
| RTC-004 | compound_history | P/save/save cost + promotion related | income shock versus a merely expensive package; lower plan accepted after hold |
| RTC-005 | compound_history | P/churn/device promotion related + contract end | completed device contract, competitor handset, and explicit final port |
| RTC-006 | compound_history | T/save/network + dissatisfied service | repeated tickets and no follow-up; supervisor repair appointment accepted |
| RTC-007 | compound_history | P/churn/sale upsell problem + dissatisfied service | unauthorized recurring pack plus failed service recovery |
| RTC-008 | compound_history | V/save/save cost + other | unused box and failed points redemption; suspension alternative accepted |
| RTC-009 | compound_history | P/churn/post to pre + promotion related | prepaid conversion remains churn despite staying with the brand |
| RTC-010 | compound_history | U/churn/customer reason | customer withholds product and reason while confirming competitor activation |
| RTC-011 | negotiation_reversal | P/save/promotion related | port request withdrawn only after a second, authorized offer |
| RTC-012 | negotiation_reversal | T/churn/down sell not success + promotion related | two cheaper offers remain above the requested amount |
| RTC-013 | negotiation_reversal | V/save/dissatisfied service | escalated apology and named technician change final decision |
| RTC-014 | negotiation_reversal | P/churn/network | customer briefly permits testing, then confirms port after another failure |
| RTC-015 | negotiation_reversal | P/save/contract end + device promotion related | ended device term, but customer explicitly asks for time to compare |
| RTC-016 | negotiation_reversal | T/churn/save cost | relocation option rejected because new residence includes internet |
| RTC-017 | negotiation_reversal | P/save/sale upsell problem | unauthorized add-on removed and refund case accepted |
| RTC-018 | negotiation_reversal | V/churn/promotion related | apparent price acceptance reversed when required channels remain absent |
| RTC-019 | negotiation_reversal | P/save/other | points restored during call; customer explicitly changes mind |
| RTC-020 | negotiation_reversal | P/churn/down sell not success + promotion related | prior and current discounts both rejected; competitor already booked |
| RTC-021 | multi_product | P/save/network; T/churn/network + save cost | shared outage history, divergent final decisions |
| RTC-022 | multi_product | T/save/network; V/churn/save cost | one technician visit retained, unused television service cancelled |
| RTC-023 | multi_product | P/churn/post to pre; V/save/promotion related | postpaid converts to prepaid while television price is retained |
| RTC-024 | multi_product | P/save/promotion related; T/save/dissatisfied service | one bundled concession saves both for different reasons |
| RTC-025 | multi_product | P/save/customer reason; T/churn/save cost; V/churn/save cost | moved household retains an essential mobile line and cancels two unused home services |
| RTC-026 | multi_product | P/churn/device promotion related + contract end; T/save/network | handset/contract churn alongside accepted broadband repair |
| RTC-027 | multi_product | P/save/promotion related; V/unknown/network | mobile offer accepted before unresolved TV call drops |
| RTC-028 | multi_product | T/churn/sale upsell problem; P/churn/dissatisfied service | two products cancelled for distinct service failures |
| RTC-029 | multi_product | P/save/network; V/churn/other | mobile repair accepted; removed content drives television churn |
| RTC-030 | multi_product | P/save/save cost; T/churn/save cost; V/churn/save cost | one budget conversation, three explicit product outcomes |
| RTC-031 | interaction_noise | P/save/network | hold-return sequence, child interruption, repeated evidence |
| RTC-032 | interaction_noise | T/churn/dissatisfied service | transfer forces retelling; customer rejects a fourth callback |
| RTC-033 | interaction_noise | V/save/sale upsell problem | overlapping correction identifies an unauthorized pack, then removal |
| RTC-034 | interaction_noise | P/unknown/promotion related | long hold and reconnect fail before a decision |
| RTC-035 | interaction_noise | U/churn/customer reason | agent mentions tempting labels; customer speech licenses neither |
| RTC-036 | interaction_noise | P/churn/network + promotion related | corrected dates and amounts, code-switching, decisive final port |
| RTC-037 | interaction_noise | T/save/network + dissatisfied service | noisy line and repeat history; scheduled repair accepted |
| RTC-038 | interaction_noise | V/churn/save cost | overlap and family interruption around an unused service |
| RTC-039 | interaction_noise | P/save/device promotion related | unavailable device/color but customer accepts a dated reservation |
| RTC-040 | interaction_noise | P/churn/other | flood-damaged home, background coordination, urgent cancellation |
| RTC-041 | boundary_outcome | P/undefined/(no reason) | bill investigation only; customer explicitly denies cancellation intent |
| RTC-042 | boundary_outcome | T/undefined/(no reason) | relocation information only, no retention effort or cancellation |
| RTC-043 | boundary_outcome | V/undefined/(no reason) | remote-control replacement only, explicit non-cancellation |
| RTC-044 | boundary_outcome | P/unknown/network + promotion related | cancellation discussed but line drops before any final decision |
| RTC-045 | boundary_outcome | T/unknown/dissatisfied service | non-owner reports failures but cannot authorize an outcome |
| RTC-046 | boundary_outcome | P/save/promotion related + post to pre | explicit indecision after asking about prepaid is `save` under the production rule |
| RTC-047 | boundary_outcome | U/churn/customer reason | vague service identity, withheld reason, completed competitor move |
| RTC-048 | boundary_outcome | P/churn/down sell not success + promotion related | final port confirmation distinguishes churn from prolonged bargaining |
| RTC-049 | boundary_outcome | T/save/sale upsell problem + dissatisfied service | investigation and callback accepted, so unresolved complaint is still save |
| RTC-050 | boundary_outcome | P/save/promotion related; T/churn/save cost; V/unknown/network | three products end in three different states before interruption |

## Review boundary

This pack is still synthetic text, not production audio. It cannot establish Thai
naturalness, ASR quality, diarisation performance, or production accuracy. A native Thai
speaker should review it before any result is used outside model screening. Every report
must retain `RECONCILED: NO`.

## As-built verification — 2026-08-11

- 50 items and 64 scored product rows; five families contain ten items each.
- Every item has 18 turns; the longest turn is 101 codepoints.
- 11 calls have multiple product rows and 3 calls have three product rows.
- All 19 production classes have support; minimum reason support is 3 distinct calls.
- Every evidence span occurs exactly once in its transcript; every reason span is in
  customer speech and not agent speech.
- `retention_challenge_v1.jsonl` sha256:
  `a3029a7081a1eb859938671d0d3f880ef97dabbb4bf55b2cf773ec184aa1f801`.
- `retention_challenge_v1.gt.csv` sha256:
  `ef0f11b1e6ccb6c60b5bfd43b5cb1b77ceb515c64a3e5650423d670101f04b8c`.
- `scripts/evalgen.py check` reports `OK. No problem found.`
- Dedicated regression tests: 7 passed. Full repository suite: 820 passed, 12 skipped.

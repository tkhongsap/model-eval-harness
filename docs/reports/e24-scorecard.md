
                                         END-TO-END PIPELINE SCORECARD                                          
                         138 calls scored in EVERY column  |  replicate policy 'first'                          

                                            Gemini 2.5 Flash         Typhoon + Qwen3.8        Qwen ASR + Qwen3.8
================================================================================================================
PIPELINE SHAPE                                                                                                  
  transcriber                            none - direct audio  Typhoon Whisper large-v3            Qwen3-ASR 1.7B
  labeller                                      (same model)           Qwen3.8-27B-fp8           Qwen3.8-27B-fp8
  model calls per item                                     1                         2                         2
  runtime                                         OpenRouter             Token Factory             Token Factory
  calls scored (common set)                              138                       138                         0
  items this arm ran                                     138                       138                         0
----------------------------------------------------------------------------------------------------------------
BUSINESS OUTCOME  (primary)                                                                                     
  weighted f1 - call_result                            0.570                     0.730                        --
  weighted f1 - reason                                 0.270                     0.272                        --
  weighted f1 - product                                0.807                     0.864                        --
  weighted precision - call_result                     0.734                     0.805                        --
  weighted precision - reason                          0.237                     0.243                        --
  weighted precision - product                         0.812                     0.835                        --
  weighted recall - call_result                        0.543                     0.710                        --
  weighted recall - reason                             0.478                     0.560                        --
  weighted recall - product                            0.833                     0.899                        --
  call accuracy - call_result                70/138  (50.7%)           95/138  (68.8%)                        --
  call accuracy - reason                     58/138  (42.0%)           65/138  (47.1%)                        --
  call accuracy - product                   109/138  (79.0%)          120/138  (87.0%)                        --
----------------------------------------------------------------------------------------------------------------
TRANSCRIPTION STAGE                                                                                             
  CER (normalised)                              no ASR stage              not recorded              not recorded
  WER (normalised)                              no ASR stage              not recorded              not recorded
  entity accuracy                               no ASR stage              not recorded              not recorded
  calls transcribed                             no ASR stage                   138/138                   120/138
  runaway failures                              no ASR stage              not recorded              not recorded
  scoreable after exclusion                     no ASR stage              not recorded              not recorded
----------------------------------------------------------------------------------------------------------------
LATENCY  (seconds)                                                                                              
  ASR stage, median                             no ASR stage                      41.3              not recorded
  label call, median                                     5.2                      18.3                        --
  label call, p95                                        7.4                      29.0                        --
  label call, max                                       10.2                      40.6                        --
  END TO END, median                                     5.2                      59.6                        --
  ASR real-time factor                          no ASR stage                     0.115              not recorded
----------------------------------------------------------------------------------------------------------------
TOKENS  (label call)                                                                                            
  input, median                                       11,615                     3,660                        --
  output, median                                         233                       246                        --
  input, total                                     4,733,890                 1,506,546                         0
  output, total                                       96,019                   103,757                         0
----------------------------------------------------------------------------------------------------------------
RELIABILITY                                                                                                     
  label calls made                                       414                       414                         0
  parse failures                                       3/414                     0/414                        --
  unstable items (of 3 reps)                          17/138                    33/138                       0/0
----------------------------------------------------------------------------------------------------------------
COST                                                                                                            
  metered cost, all calls                            $3.8554              internal GPU              internal GPU
  metered cost, per call                            $0.00938               not metered               not metered
================================================================================================================

  undefined were never predicted by any arm here, and are 8 of
  138 ground-truth rows -- so the highest weighted F1 reachable on this run is
  0.942, not 1.000.

  That is a CORPUS limitation, not a model one, and it is a KNOWN one rather than a
  discovery. `unknown` used to sit here too: the corpus built those calls from the
  indecision phrases retention_v9_16_body.txt:80 explicitly rules to be a `save`, so a
  spec-obeying model could never label them right. That was fixed on 2026-08-20 and
  `unknown` now scores 0.966.

  `undefined` resisted the same treatment and the reason is worth keeping. The spec
  (body:82) requires the FOCUS of the call to be outside retention; these calls are
  68-86 turns of a real retention conversation with one closing line saying otherwise,
  and the labeller answers `save` on 7 of 8. It is right to. Fixing it needs a scenario
  whose BODY is out of scope -- a new generator branch, not another sentence.

READING THIS TABLE
  Gemini has no transcription stage -- audio goes in and JSON comes out of
  ONE call -- so every ASR row is blank for it rather than zero.
  Typhoon and Qwen share the same labeller and differ only in the
  transcriber, so the gap between those two columns IS the ASR stage.
  Cost is metered only on OpenRouter; the internal arms run on company GPU
  with no per-call price, which is not the same as being free.
  Business figures follow the preregistered replicate-1 rule and are
  restricted to the calls EVERY column answered; latency and tokens pool
  every call actually made, which is why their denominators differ.
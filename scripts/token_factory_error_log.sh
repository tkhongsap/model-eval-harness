#!/usr/bin/env bash
# Capture a full, sendable error log for the Token Factory models.
#
# Written so the output can be pasted straight to the GPU team: every error body is printed
# in full (not truncated), each attempt is timestamped in both UTC and +07, and the API key
# is read from the environment or .env and never appears in the output.
#
# The control model matters. `qwen3.8-27b-fp8` is called first and on purpose: if it answers
# 200 while the other two fail, that single fact rules out the gateway, the key, TLS, the
# certificate and the VPN in one line, and stops the conversation becoming "is it your
# network?". If the control ALSO fails, the problem is on our side and the log says so.
#
# Usage:
#     bash scripts/token_factory_error_log.sh                 # print, and write the log file
#     bash scripts/token_factory_error_log.sh | tee out.txt   # if you want your own copy too
#
# Exit code: 0 if every model answered, 1 if any did not. So it can be polled:
#     until bash scripts/token_factory_error_log.sh >/dev/null 2>&1; do sleep 300; done

set -uo pipefail
cd "$(dirname "$0")/.."

HOSTNAME_TF="token-fac-api.truecorp.co.th"
IP_TF="10.94.154.102"
BASE="https://${HOSTNAME_TF}/v1"
CERT="configs/token-factory.crt.pem"
OUT="docs/token-factory-error-log-$(date -u +%Y%m%dT%H%M%SZ).txt"

# Key from the environment, else from .env. Never echoed.
if [ -z "${TOKEN_FACTORY_API_KEY:-}" ] && [ -f .env ]; then
  TOKEN_FACTORY_API_KEY="$(grep -m1 '^TOKEN_FACTORY_API_KEY=' .env | cut -d= -f2- | tr -d '"'"'"' \r')"
fi
if [ -z "${TOKEN_FACTORY_API_KEY:-}" ]; then
  echo "TOKEN_FACTORY_API_KEY is not set and was not found in .env" >&2
  exit 2
fi
if [ ! -f "$CERT" ]; then
  echo "pinned certificate not found at $CERT" >&2
  exit 2
fi

stamp() { date -u +"%Y-%m-%d %H:%M:%SZ"; }
# Git Bash often ships without tzdata, so TZ=Asia/Bangkok silently returns UTC and the log
# would claim +07 while showing UTC. Add the offset explicitly instead.
stamp_th() { date -u -d "+7 hours" +"%H:%M:%S +07"; }

# The failure count lives in a file, not a variable: the reporting block below runs inside
# a `| tee` pipeline, i.e. a subshell, so a plain variable is lost and the script would exit
# 0 while printing "2 of 3 failed" -- exactly the "an exit code is not a result" trap this
# repo already paid for once.
FAILFILE="$(mktemp)"; echo 0 > "$FAILFILE"
# The control model's own status decides which diagnosis the summary prints, so it is
# recorded separately rather than folded into the failure count.
CTLFILE="$(mktemp)"; echo "" > "$CTLFILE"
trap 'rm -f "$FAILFILE" "$CTLFILE"' EXIT

{
  echo "TOKEN FACTORY ERROR LOG"
  echo "generated   $(stamp) / $(stamp_th)"
  echo "endpoint    ${BASE}   (connect via ${IP_TF}, pinned self-signed cert)"
  echo "auth        TOKEN_FACTORY_API_KEY  (value never printed)"
  echo
  echo "=============================================================================="
  echo "1. CATALOG   GET /v1/models"
  echo "=============================================================================="
  curl -sS --cacert "$CERT" --resolve "${HOSTNAME_TF}:443:${IP_TF}" \
       "${BASE}/models" -H "Authorization: Bearer ${TOKEN_FACTORY_API_KEY}"
  echo
  echo

  echo "=============================================================================="
  echo "2. GENERATION   POST /v1/chat/completions   (control model first)"
  echo "=============================================================================="
  for model in qwen3.8-27b-fp8 gemma-4-12b-it qwen3-asr-1.7b; do
    note=""
    [ "$model" = "qwen3.8-27b-fp8" ] && note="   <-- control: expected to work"
    echo "------------------------------------------------------------------------------"
    echo "model  ${model}${note}"
    echo "time   $(stamp) / $(stamp_th)"
    body=$(curl -sS --cacert "$CERT" --resolve "${HOSTNAME_TF}:443:${IP_TF}" \
        -w $'\n__HTTP__%{http_code}__TIME__%{time_total}s' \
        "${BASE}/chat/completions" \
        -H "Authorization: Bearer ${TOKEN_FACTORY_API_KEY}" \
        -H 'Content-Type: application/json' \
        -d "{\"model\":\"${model}\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with: READY\"}],\"max_tokens\":16,\"temperature\":0}" 2>&1)
    code=$(printf '%s' "$body" | sed -n 's/.*__HTTP__\([0-9]*\)__TIME__.*/\1/p')
    secs=$(printf '%s' "$body" | sed -n 's/.*__TIME__\(.*\)$/\1/p')
    clean=$(printf '%s' "$body" | sed 's/__HTTP__[0-9]*__TIME__.*//')
    echo "status HTTP ${code:-?}   client latency ${secs:-?}"
    echo "response body (full, untruncated):"
    printf '%s\n' "$clean"
    [ "$model" = "qwen3.8-27b-fp8" ] && echo "${code:-000}" > "$CTLFILE"
    [ "${code:-0}" = "200" ] || echo $(( $(cat "$FAILFILE") + 1 )) > "$FAILFILE"
    echo
  done

  echo "=============================================================================="
  echo "3. AUDIO PATH   POST /v1/audio/transcriptions   (qwen3-asr-1.7b)"
  echo "=============================================================================="
  echo "Not in the published openapi.yaml, which defines only /v1/models, /v1/responses"
  echo "and /v1/chat/completions. Included so the team can see the gateway does route it."
  echo "Probe audio is a 0.2s synthetic silent WAV -- no evaluation or customer audio."
  echo "time   $(stamp) / $(stamp_th)"
  # mktemp -t yields a POSIX path Windows curl.exe cannot open; it died with curl(26)
  # before reaching the server and proved nothing. Use a plain relative path.
  TMPWAV="tfprobe-silence.wav"
  python - "$TMPWAV" <<'PYEOF'
import sys, wave, struct
with wave.open(sys.argv[1], "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    n = 3200
    w.writeframes(struct.pack("<%dh" % n, *([0] * n)))
PYEOF
  body=$(curl -sS --cacert "$CERT" --resolve "${HOSTNAME_TF}:443:${IP_TF}" \
      -w $'\n__HTTP__%{http_code}__TIME__%{time_total}s' \
      "${BASE}/audio/transcriptions" \
      -H "Authorization: Bearer ${TOKEN_FACTORY_API_KEY}" \
      -F "file=@./${TMPWAV};type=audio/wav" \
      -F "model=qwen3-asr-1.7b" \
      -F "response_format=json" 2>&1)
  rm -f "$TMPWAV"
  code=$(printf '%s' "$body" | sed -n 's/.*__HTTP__\([0-9]*\)__TIME__.*/\1/p')
  secs=$(printf '%s' "$body" | sed -n 's/.*__TIME__\(.*\)$/\1/p')
  clean=$(printf '%s' "$body" | sed 's/__HTTP__[0-9]*__TIME__.*//')
  echo "status HTTP ${code:-?}   client latency ${secs:-?}"
  echo "response body (full, untruncated):"
  printf '%s\n' "$clean"
  echo

  echo "=============================================================================="
  echo "SUMMARY"
  echo "=============================================================================="
  failures=$(cat "$FAILFILE")
  control=$(cat "$CTLFILE")
  if [ "$failures" -eq 0 ]; then
    echo "All 3 models answered. If this follows an outage, the backends are back --"
    echo "re-run the evaluation arm."
  elif [ "$control" = "000" ] || [ -z "$control" ]; then
    echo "${failures} of 3 models did not answer, and the CONTROL model did not answer"
    echo "either -- it failed before reaching the server (HTTP 000 = no connection)."
    echo
    echo "  >>> THIS LOG DOES NOT DIAGNOSE THE GPU BACKENDS. DO NOT SEND IT AS ONE. <<<"
    echo
    echo "Every request failed at TCP connect, which means this workstation could not reach"
    echo "the gateway at all. The overwhelmingly likely cause is the VPN being disconnected"
    echo "on our side. Reconnect, confirm 10.94.154.102:443 is reachable, and re-run."
    echo "Until the control model returns 200, nothing here says anything about whether the"
    echo "vLLM backends are healthy."
  elif [ "$control" = "200" ]; then
    echo "${failures} of 3 models did not answer, but the control (qwen3.8-27b-fp8)"
    echo "returned 200 in the same session."
    echo
    echo "That rules out the gateway, the API key, TLS, the pinned certificate and the VPN,"
    echo "and places the failure specifically on the backends the other models route to."
    echo
    echo "The 'Cannot connect to host ...' text is the GATEWAY reporting that it could not"
    echo "reach its own backend. It is not our client, our request shape or our network."
    echo "This log IS suitable to send to the GPU team."
  else
    echo "${failures} of 3 models did not answer, and the control returned HTTP ${control}"
    echo "rather than 200 or a connection failure."
    echo
    echo "That is neither the clean 'their backend' case nor the clean 'our network' case."
    echo "Read the bodies above before attributing this to anyone -- an auth, quota or"
    echo "gateway-level fault would look like this."
  fi
} 2>&1 | tee "$OUT"

echo
echo "log written to $OUT"
failures=$(cat "$FAILFILE")
[ "$failures" -eq 0 ] && exit 0 || exit 1

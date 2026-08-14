# Probe True's internal Token Factory: does the endpoint answer, and with which models?
#
# One question, asked before anything is wired to it -- the same role
# `scripts/gpu_endpoint_probe.py` plays for the Modellismz endpoint. It lists models and
# stops. It scores nothing, writes nothing, and is not part of the harness: nothing under
# `src/` imports it and it produces no artifact.
#
# Usage (PowerShell):
#     .\token-factory-probe.ps1
#
# It prompts for the key. Nothing is written to disk and nothing is echoed.
#
# ---------------------------------------------------------------------------------------
# Two things about this script a reader has to know, because both look like mistakes.
#
# 1. `--resolve` pins the hostname to an internal address. DNS for
#    `token-fac-api.truecorp.co.th` does not resolve from outside True's network, so the
#    name is mapped to the host directly. That means this script only works on-network,
#    and that the IP below is infrastructure detail -- one more reason this repository
#    stays private (AGENTS.md, "Open items").
#
# 2. `-k` disables TLS certificate verification, and that is a real cost, not a nit.
#    Connecting by pinned IP means the presented certificate will not match the name
#    being requested, so verification fails on a connection that is otherwise fine. `-k`
#    buys past that and simultaneously gives up the guarantee that the host on the other
#    end is the one intended -- on an internal network, against a hardcoded RFC1918
#    address, over a bearer token that is being sent regardless.
#
#    Left in because it is what makes the probe work today, and removing it without a
#    trusted certificate chain would just make the script fail. It is recorded rather
#    than silently accepted, and it should not be copied into anything that carries real
#    data or runs unattended. If this endpoint becomes a real evaluation arm, the right
#    fix is a certificate the client can verify -- not `-k` in more places.
#
# The key is read interactively, converted for the one call that needs it, and cleared.
# It is never a parameter, never an environment variable that outlives the script, and
# never written anywhere -- so this file is safe to commit, which is the point.
# ---------------------------------------------------------------------------------------

$env:TOKEN_FACTORY_BASE_URL = "https://token-fac-api.truecorp.co.th"

$secureKey = Read-Host "Token Factory API key" -AsSecureString
$apiKey = [System.Net.NetworkCredential]::new("", $secureKey).Password

curl.exe -k -sS `
  --resolve "token-fac-api.truecorp.co.th:443:10.94.154.102" `
  "$env:TOKEN_FACTORY_BASE_URL/v1/models" `
  -H "Authorization: Bearer $apiKey"

$apiKey = $null
$secureKey = $null

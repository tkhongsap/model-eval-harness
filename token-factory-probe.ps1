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
# 2. `--cacert` replaced `-k`, and the reason is worth reading before anyone puts `-k`
#    back. The original script disabled verification entirely. The diagnosis behind that
#    was wrong: the certificate does not fail because the pinned IP mismatches the name,
#    it fails because it is **self-signed**, so no public CA vouches for it.
#
#    Its SAN covers both `token-fac-api.truecorp.co.th` and `10.94.154.102`, so trusting
#    that one certificate -- and only it -- verifies cleanly. Verification here is now
#    STRONGER than the public-CA default: a substituted host fails even if it presents a
#    certificate some public CA signed. `-k` would accept anything at all, on a
#    connection carrying a bearer token.
#
#    Rotation breaks this deliberately. A changed certificate should stop the probe and
#    be re-reviewed, not be trusted silently. Re-pin with
#    `ssl.get_server_certificate(('10.94.154.102', 443))` and read the diff.
#
# The key is read interactively, converted for the one call that needs it, and cleared.
# It is never a parameter, never an environment variable that outlives the script, and
# never written anywhere -- so this file is safe to commit, which is the point.
# ---------------------------------------------------------------------------------------

$env:TOKEN_FACTORY_BASE_URL = "https://token-fac-api.truecorp.co.th"

$secureKey = Read-Host "Token Factory API key" -AsSecureString
$apiKey = [System.Net.NetworkCredential]::new("", $secureKey).Password

curl.exe -sS `
  --cacert "$PSScriptRoot\configs\token-factory.crt.pem" `
  --resolve "token-fac-api.truecorp.co.th:443:10.94.154.102" `
  "$env:TOKEN_FACTORY_BASE_URL/v1/models" `
  -H "Authorization: Bearer $apiKey"

$apiKey = $null
$secureKey = $null

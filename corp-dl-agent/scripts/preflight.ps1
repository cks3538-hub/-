# preflight.ps1 — Python 유무/경로/버전/64bit 만 진단한다. 설치하지 않으며 실행 정책·인증서·GPO·백신을 변경하지 않는다.
# 사용: powershell -File preflight.ps1 [-Python "C:\Program Files\Python312\python.exe"] [-Report <json>]
# 실행이 차단되면 회사가 승인한 실행 방법으로 00_Preflight.cmd 또는 python preflight.py 를 실행하세요.
param(
  [string]$Python = $env:PYTHON,
  [string]$Report = ""
)
$ErrorActionPreference = "Continue"
$probe = "import sys,struct,platform,json`nd={'version':platform.python_version(),'impl':platform.python_implementation(),'bits':struct.calcsize('P')*8,'exe':sys.executable}`ntry:`n import venv; d['venv']=True`nexcept Exception:`n d['venv']=False`ntry:`n import ensurepip; d['ensurepip']=True`nexcept Exception:`n d['ensurepip']=False`nprint(json.dumps(d))"

Write-Host ("OS: " + [System.Environment]::OSVersion.VersionString + " / 64-bit OS: " + [System.Environment]::Is64BitOperatingSystem + " / arch: " + $env:PROCESSOR_ARCHITECTURE)

$candidates = @()
if ($Python) { $candidates += @{ label = "PYTHON 환경변수"; exe = $Python; args = @() } }
if (Get-Command py -ErrorAction SilentlyContinue) { $candidates += @{ label = "py -3.12"; exe = "py"; args = @("-3.12") } }
Get-Command python -All -ErrorAction SilentlyContinue | ForEach-Object {
  if ($_.Source -and ($_.Source -notmatch "\\WindowsApps\\")) { $candidates += @{ label = "PATH"; exe = $_.Source; args = @() } }
}

$found = @()
foreach ($cand in $candidates) {
  try {
    $out = & $cand.exe @($cand.args + @("-I", "-c", $probe)) 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $out) { Write-Host ("  [실행 불가] " + $cand.label + ": " + $cand.exe); continue }
    $d = $out | ConvertFrom-Json
    $ver = [version]$d.version
    $ok = ($d.impl -eq "CPython") -and ($ver -ge [version]"3.11") -and ($d.bits -eq 64) -and $d.venv -and $d.ensurepip
    $status = if ($ok) { "OK" } else { "부적합" }
    Write-Host ("  [" + $status + "] " + $cand.label + ": " + $d.exe + " (" + $d.impl + " " + $d.version + ", " + $d.bits + "-bit, venv=" + $d.venv + ", ensurepip=" + $d.ensurepip + ")")
    $found += @{ label = $cand.label; exe = $d.exe; version = $d.version; bits = $d.bits; venv = $d.venv; ensurepip = $d.ensurepip; ok = $ok }
  } catch {
    Write-Host ("  [실행 불가] " + $cand.label + ": " + $cand.exe)
  }
}

$suitable = @($found | Where-Object { $_.ok })
if ($Report) {
  $doc = @{ generated_at = (Get-Date).ToUniversalTime().ToString("s") + "Z"; os = [System.Environment]::OSVersion.VersionString; is_64bit_os = [System.Environment]::Is64BitOperatingSystem; candidates = $found; status = $(if ($suitable.Count -gt 0) { "PASS" } else { "BLOCKED_PREREQUISITE" }) }
  $doc | ConvertTo-Json -Depth 5 | Set-Content -Path $Report -Encoding UTF8
  Write-Host ("보고서: " + $Report)
}
if ($suitable.Count -gt 0) {
  Write-Host ("결과: PASS — 사용할 Python: " + $suitable[0].exe)
  Write-Host ("  다음: set PYTHON=" + $suitable[0].exe + " 후 00_Preflight.cmd -> 01_Verify.cmd -> 02_Install.cmd")
  exit 0
} else {
  Write-Host "결과: BLOCKED_PREREQUISITE — 적합한 Python (CPython 3.12 x64, venv/ensurepip 포함) 을 찾지 못했습니다."
  Write-Host "  회사 승인 절차로 Python 3.12 (64-bit) 를 설치한 뒤 PYTHON 환경변수로 경로를 지정하세요. 이 스크립트는 자동 설치하지 않습니다."
  exit 8
}

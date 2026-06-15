# ============================================================================
# WILDCHANCE ENGINE - scheduler setup (Windows / PowerShell)
# ============================================================================
# Creates three Scheduled Tasks matching the engine's cadence:
#   Tier 1  every 6h    -> live prices + recompute signals
#   Tier 2  daily 06:30  -> myfxbook retail + faireconomy calendar
#   Tier 3  Fri 21:00    -> CFTC COT + gold COMEX COT
#
# Credentials are stored as USER environment variables (setx), NOT in any file.
#
# Usage (run PowerShell as your normal user, not admin, for per-user tasks):
#   1) edit the CONFIG block
#   2) Set-ExecutionPolicy -Scope Process Bypass    # if scripts are blocked
#   3) .\setup_schedule.ps1 install
#      .\setup_schedule.ps1 remove
#      .\setup_schedule.ps1 test
# ============================================================================

param([Parameter(Position=0)][string]$Action = "help")

# ---------------- CONFIG (edit these) ----------------
$EngineDir   = "$HOME\wildchance"          # folder with wildchance_scraper.py
$PythonBin   = "python"                     # or full path to python.exe
$TwelveKey   = ""                           # your Twelve Data key
$MfxEmail    = ""                           # myfxbook email
$MfxPassword = ""                           # myfxbook password
# -----------------------------------------------------

$Script = Join-Path $EngineDir "wildchance_scraper.py"
$Prefix = "WildchanceEngine"

function Set-Creds {
  if ($TwelveKey)   { setx TWELVEDATA_KEY    "$TwelveKey"   | Out-Null }
  if ($MfxEmail)    { setx MYFXBOOK_EMAIL    "$MfxEmail"    | Out-Null }
  if ($MfxPassword) { setx MYFXBOOK_PASSWORD "$MfxPassword" | Out-Null }
  Write-Host "Credentials stored as user environment variables (setx)."
  Write-Host "Open a NEW terminal for them to take effect."
}

function New-EngineTask($name, $tier, $trigger) {
  $action = New-ScheduledTaskAction -Execute $PythonBin `
            -Argument "`"$Script`" --tier $tier" -WorkingDirectory $EngineDir
  $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd
  Register-ScheduledTask -TaskName "$Prefix-$name" -Action $action `
            -Trigger $trigger -Settings $settings -Force | Out-Null
  Write-Host "  created task $Prefix-$name ($tier)"
}

switch ($Action) {
  "install" {
    if (-not (Test-Path $Script)) { Write-Error "Not found: $Script. Fix `$EngineDir."; break }
    Set-Creds
    # Tier 1: every 6h, repeating from now
    $t6 = New-ScheduledTaskTrigger -Once -At (Get-Date) `
          -RepetitionInterval (New-TimeSpan -Hours 6)
    New-EngineTask "6h" "6h" $t6
    # Tier 2: daily 06:30
    $td = New-ScheduledTaskTrigger -Daily -At 6:30am
    New-EngineTask "daily" "daily" $td
    # Tier 3: Friday 21:00
    $tw = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At 9:00pm
    New-EngineTask "weekly" "weekly" $tw
    Write-Host "Installed. View in Task Scheduler under tasks named $Prefix-*"
  }
  "remove" {
    Get-ScheduledTask -TaskName "$Prefix-*" -ErrorAction SilentlyContinue |
      Unregister-ScheduledTask -Confirm:$false
    Write-Host "Removed all $Prefix-* tasks."
  }
  "test" {
    if (-not (Test-Path $Script)) { Write-Error "Not found: $Script."; break }
    Push-Location $EngineDir
    $env:TWELVEDATA_KEY=$TwelveKey; $env:MYFXBOOK_EMAIL=$MfxEmail; $env:MYFXBOOK_PASSWORD=$MfxPassword
    foreach ($tier in @("weekly","daily","6h")) {
      Write-Host "== $tier =="; & $PythonBin $Script --tier $tier
    }
    Pop-Location
  }
  default {
    Write-Host "usage: .\setup_schedule.ps1 {install|remove|test}"
  }
}

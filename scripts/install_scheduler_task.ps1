# Auto Publishing scheduler를 Windows 작업 스케줄러에 등록한다.
#
# 1회만 실행:
#   powershell -ExecutionPolicy Bypass -File scripts\install_scheduler_task.ps1
#
# 멱등 — 이미 등록된 작업이 있으면 제거 후 재등록한다.

$ErrorActionPreference = "Stop"

$projectDir = (Resolve-Path "$PSScriptRoot\..").Path
$batPath    = Join-Path $projectDir "scripts\run_scheduler.bat"
$taskName   = "AutoPublishingScheduler"

if (-not (Test-Path $batPath)) {
    Write-Error "run_scheduler.bat not found at: $batPath"
    exit 1
}

$action  = New-ScheduledTaskAction -Execute $batPath -WorkingDirectory $projectDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

$settings.DisallowStartIfOnBatteries = $false
$settings.StopIfGoingOnBatteries     = $false
$settings.AllowHardTerminate         = $true

# 현재 사용자로 인터랙티브 실행 (Chrome 브라우저 자동화 필요)
$principal = New-ScheduledTaskPrincipal `
    -UserId    $env:USERNAME `
    -LogonType Interactive `
    -RunLevel  Limited

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Write-Host "Removing existing task '$taskName'..."
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName    $taskName `
    -Action      $action `
    -Trigger     $trigger `
    -Settings    $settings `
    -Principal   $principal `
    -Description "Auto Publishing pipeline scheduler"

Write-Host ""
Write-Host "Registered: $taskName"
Write-Host "  Trigger:  At logon of '$env:USERNAME'"
Write-Host "  Action:   $batPath"
Write-Host "  Logs:     $projectDir\scheduler.log"
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  Start now:  Start-ScheduledTask     -TaskName '$taskName'"
Write-Host "  Stop:       Stop-ScheduledTask      -TaskName '$taskName'"
Write-Host "  Status:     Get-ScheduledTask       -TaskName '$taskName'"
Write-Host "  Remove:     Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false"

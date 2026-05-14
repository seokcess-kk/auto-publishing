# =====================================================================
# Windows 작업 스케줄러 등록 — Auto Publishing
#
# 등록하는 두 작업:
#   1. AutoPublishing_Scheduler  — 부팅 시 scheduler_runner 자동 시작
#   2. AutoPublishing_Watchdog   — 매 5분 워치독 실행 (stale 감지)
#
# 사용법 (관리자 PowerShell):
#     powershell -ExecutionPolicy Bypass -File tools\install_task_scheduler.ps1
#
# 제거:
#     powershell -ExecutionPolicy Bypass -File tools\install_task_scheduler.ps1 -Uninstall
# =====================================================================

param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$projectDir = (Resolve-Path "$PSScriptRoot\..").Path

# 작업 스케줄러는 사용자 셸의 PATH 를 그대로 못 받는다. python.exe 풀 경로를
# 명시적으로 찾아 등록. Get-Command 가 첫 매칭(보통 사용자 셸의 python)을 반환.
$pyCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pyCmd) {
    Write-Error "python.exe 를 PATH 에서 찾지 못했습니다. 'python --version' 이 동작하는지 먼저 확인하세요."
    exit 1
}
# WindowsApps 의 0KB stub 회피 — 실제 .exe 가 아니면 다음 후보 사용
if ($pyCmd.Source -match 'WindowsApps' -and (Get-Item $pyCmd.Source).Length -lt 10240) {
    $all = where.exe python 2>$null
    foreach ($p in $all) {
        if ($p -notmatch 'WindowsApps') { $pyCmd = Get-Command $p; break }
    }
}
$pythonExe = $pyCmd.Source

$schedulerName = "AutoPublishing_Scheduler"
$watchdogName  = "AutoPublishing_Watchdog"

# --- Uninstall 분기 ---------------------------------------------------
if ($Uninstall) {
    foreach ($n in @($schedulerName, $watchdogName)) {
        if (Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $n -Confirm:$false
            Write-Host "✓ 작업 제거: $n" -ForegroundColor Yellow
        }
    }
    Write-Host "`n제거 완료." -ForegroundColor Green
    exit 0
}

# --- 사전 확인 -------------------------------------------------------
Write-Host "=== Auto Publishing 작업 스케줄러 등록 ===" -ForegroundColor Cyan
Write-Host "프로젝트:  $projectDir"
Write-Host "Python:    $pythonExe`n"

if (-not (Test-Path "$projectDir\pipelines\scheduler_runner.py")) {
    Write-Error "scheduler_runner.py 가 보이지 않습니다. 프로젝트 경로 확인 필요."
    exit 1
}
if (-not (Test-Path "$projectDir\tools\watchdog.py")) {
    Write-Error "tools\watchdog.py 가 보이지 않습니다."
    exit 1
}

# --- 1) Scheduler (부팅 시 자동 시작) -------------------------------
$schedAction  = New-ScheduledTaskAction `
    -Execute $pythonExe `
    -Argument "-m pipelines.scheduler_runner" `
    -WorkingDirectory $projectDir

$schedTrigger = New-ScheduledTaskTrigger -AtStartup
$schedTrigger.Delay = "PT1M"  # 부팅 후 1분 지연 (네트워크 안정화 대기)

$schedSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Days 365) `
    -MultipleInstances IgnoreNew

$schedPrincipal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Highest

if (Get-ScheduledTask -TaskName $schedulerName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $schedulerName -Confirm:$false
    Write-Host "(기존 $schedulerName 제거)"
}

Register-ScheduledTask `
    -TaskName $schedulerName `
    -Action $schedAction `
    -Trigger $schedTrigger `
    -Settings $schedSettings `
    -Principal $schedPrincipal `
    -Description "Auto Publishing scheduler_runner — 부팅 시 자동 시작" | Out-Null

Write-Host "✓ 등록: $schedulerName  (부팅 +1분 후 시작)" -ForegroundColor Green

# --- 2) Watchdog (매 5분) -------------------------------------------
$wdAction  = New-ScheduledTaskAction `
    -Execute $pythonExe `
    -Argument "tools\watchdog.py" `
    -WorkingDirectory $projectDir

# RepetitionDuration 을 TimeSpan.MaxValue 로 설정하면 ISO 8601 duration
# (P99999999DT...) 가 작업 스케줄러에 의해 거부된다. 미지정 시 Windows 10+
# 에서는 기본 무한 반복으로 동작.
$wdTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)

$wdSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 3) `
    -MultipleInstances IgnoreNew

if (Get-ScheduledTask -TaskName $watchdogName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $watchdogName -Confirm:$false
    Write-Host "(기존 $watchdogName 제거)"
}

Register-ScheduledTask `
    -TaskName $watchdogName `
    -Action $wdAction `
    -Trigger $wdTrigger `
    -Settings $wdSettings `
    -Principal $schedPrincipal `
    -Description "Auto Publishing watchdog — 5분 단위 헬스체크" | Out-Null

Write-Host "✓ 등록: $watchdogName  (5분 간격)" -ForegroundColor Green

# --- 즉시 시작 -------------------------------------------------------
Write-Host "`n--- scheduler_runner 즉시 시작 ---" -ForegroundColor Cyan
Start-ScheduledTask -TaskName $schedulerName
Start-Sleep -Seconds 2

# 상태 확인
$state = (Get-ScheduledTask -TaskName $schedulerName).State
Write-Host "현재 상태: $state"

Write-Host "`n=== 완료 ===" -ForegroundColor Green
Write-Host "확인:"
Write-Host "  Get-ScheduledTask -TaskName $schedulerName,$watchdogName | Format-Table TaskName, State, LastRunTime"
Write-Host "  (또는 `"작업 스케줄러`" GUI → 작업 스케줄러 라이브러리에서 확인)"
Write-Host "`n제거:"
Write-Host "  powershell -ExecutionPolicy Bypass -File tools\install_task_scheduler.ps1 -Uninstall"

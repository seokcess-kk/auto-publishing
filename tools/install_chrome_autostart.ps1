# Windows 작업 스케줄러에 'AutoPublishing_Chrome' 작업 등록.
#
# 부팅 (또는 로그온) 시 본인 평소 Chrome 을 자동 시작 → tistory bridge extension
# 이 항상 polling 가능한 상태가 됨.
#
# 사용법 (관리자 PowerShell):
#   powershell -ExecutionPolicy Bypass -File tools\install_chrome_autostart.ps1
#
# 제거:
#   powershell -ExecutionPolicy Bypass -File tools\install_chrome_autostart.ps1 -Uninstall

param([switch]$Uninstall)

$taskName = "AutoPublishing_Chrome"
$chromeExe = "C:\Program Files\Google\Chrome\Application\chrome.exe"

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "✓ 제거 완료: $taskName" -ForegroundColor Yellow
    }
    exit 0
}

if (-not (Test-Path $chromeExe)) {
    Write-Error "Chrome 미발견: $chromeExe"
    exit 1
}

# 부팅 시 본인 평소 프로필로 Chrome 시작 — 백그라운드 모드 가정 (window 안 뜸)
# --no-startup-window: 시작 시 창 안 띄움 (백그라운드만)
$action = New-ScheduledTaskAction `
    -Execute $chromeExe `
    -Argument "--no-startup-window"

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Days 365)

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "(기존 $taskName 제거)"
}

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Auto Publishing — Chrome 백그라운드 시작 (extension polling 유지)" | Out-Null

Write-Host "✓ 등록: $taskName  (로그온 시 Chrome 백그라운드 시작)" -ForegroundColor Green
Write-Host ""
Write-Host "다음을 추가로 설정하세요:"
Write-Host "  1. chrome://settings/system → '백그라운드 앱 실행 허용' ON"
Write-Host "  2. 확장(Auto Publishing) 핀 고정 + 활성 상태 확인"
Write-Host ""
Write-Host "지금 즉시 시작: Start-ScheduledTask -TaskName $taskName"

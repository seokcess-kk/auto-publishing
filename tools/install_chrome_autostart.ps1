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

$projectDir = (Resolve-Path "$PSScriptRoot\..").Path
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

# 로그온 시 PowerShell wrapper 를 거쳐 Chrome 시작.
# wrapper 가 WScript.Shell.Run 으로 SW_SHOWMINNOACTIVE (= 7) 강제 — 부팅 시
# 창 안 뜨고 background process 만 살아있음. Chrome 116+ 가 --start-minimized
# 를 가끔 무시하던 회귀를 우회.
$wrapper = Join-Path $projectDir "tools\chrome_background_launcher.ps1"
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$wrapper`""

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

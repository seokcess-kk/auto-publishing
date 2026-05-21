# Chrome 을 minimized 로 시작 — 부팅 시 화면 거슬리지 않고 extension polling 유지.
# 작업 스케줄러 (AutoPublishing_Chrome) 가 호출.
#
# 평소 사용 프로필 자동 감지:
#   %LOCALAPPDATA%\Google\Chrome\User Data\Local State 의 profile.last_used 사용.
#   프로필 선택창 없이 그 프로필로 바로 진입.

$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$ud = "$env:LOCALAPPDATA\Google\Chrome\User Data"

if (-not (Test-Path $chrome)) {
    Write-Host "[ap-chrome] Chrome 미발견: $chrome"
    exit 1
}

# 평소 사용 프로필 자동 감지 (Local State 의 last_used)
$profileDir = "Default"
$ls = Join-Path $ud "Local State"
if (Test-Path $ls) {
    try {
        $j = Get-Content $ls -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($j.profile.last_used) {
            $profileDir = $j.profile.last_used
        }
    } catch {}
}

# WScript.Shell.Run 의 WindowStyle=7 = SW_SHOWMINNOACTIVE
# 창 안 띄우고 background 로 시작 + 포커스 안 가져감.
# --profile-directory: 프로필 선택창 없이 평소 쓰는 프로필로 직진.
# --no-first-run: 첫 실행 wizard 비활성.
$args = "--profile-directory=`"$profileDir`" --no-first-run --no-default-browser-check"

$shell = New-Object -ComObject WScript.Shell
$shell.Run("`"$chrome`" $args", 7, $false) | Out-Null

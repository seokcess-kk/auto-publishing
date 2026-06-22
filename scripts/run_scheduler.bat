@echo off
REM Auto Publishing scheduler launcher (used by Windows Task Scheduler)

setlocal
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"

REM 스크립트 위치 기준으로 프로젝트 루트(상위 폴더)로 이동 후 상대경로 사용.
REM "%~dp0..\.venv\..." 형태의 경로 조합은 일부 환경에서 "지정된 경로를
REM 찾을 수 없습니다" 로 실패하므로, cd 로 cwd 를 옮긴 뒤 상대경로로 호출한다.
cd /d "%~dp0.."

echo. >> scheduler.log
echo ===== %DATE% %TIME% Scheduler started ===== >> scheduler.log

REM venv 의 python 을 명시적으로 사용 (작업 스케줄러는 venv 활성화를 안 받음)
".venv\Scripts\python.exe" -u -m pipelines.scheduler_runner >> scheduler.log 2>&1

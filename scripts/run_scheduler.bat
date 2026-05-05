@echo off
REM Auto Publishing scheduler launcher (used by Windows Task Scheduler)

setlocal
set "PROJECT_DIR=%~dp0.."
set "PYTHONIOENCODING=utf-8"

cd /d "%PROJECT_DIR%"

echo. >> scheduler.log
echo ===== %DATE% %TIME% Scheduler started ===== >> scheduler.log

python -u -m pipelines.scheduler_runner >> scheduler.log 2>&1

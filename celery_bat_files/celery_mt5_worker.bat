@echo off
setlocal EnableExtensions
set "PAUSE_ON_EXIT=1"
if /I "%~1"=="--no-pause" (
    set "PAUSE_ON_EXIT="
    shift
)
set "RETURN_CODE=0"
for %%i in ("%~dp0..") do set "PROJECT_ROOT=%%~fi"
for %%i in ("%PROJECT_ROOT%\..") do set "REPO_ROOT=%%~fi"
set "VENV_DIR=%PROJECT_ROOT%\.venv"

if not exist "%VENV_DIR%\Scripts\python.exe" if exist "%REPO_ROOT%\mt5_env\Scripts\python.exe" set "VENV_DIR=%REPO_ROOT%\mt5_env"

pushd "%PROJECT_ROOT%" || exit /b 1

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Virtual environment not found. Run: python -m venv "%PROJECT_ROOT%\.venv">&2
    set "RETURN_CODE=1"
    goto finish
)

powershell -NoProfile -Command "$proc = @(Get-CimInstance Win32_Process -Filter \"CommandLine LIKE '%%celery -A config worker%%'\"); $mt5 = @($proc | Where-Object { $_.CommandLine -match '(--queues=mt5_execution|-Q\s+mt5_execution)(\s|$)' }); if ($mt5.Count) { exit 5 } else { exit 0 }"
set "PROC_CHECK=%ERRORLEVEL%"
if "%PROC_CHECK%"=="5" (
    echo Dedicated MT5 Celery worker already running. Stop it before starting a new one.
    set "RETURN_CODE=1"
    goto finish
)
if not "%PROC_CHECK%"=="0" if not "%PROC_CHECK%"=="5" (
    echo [%date% %time%] Warning: Unable to verify existing MT5 worker (code %PROC_CHECK%). Continuing anyway.
)

call "%VENV_DIR%\Scripts\activate.bat"

set "LOG_FILE=%PROJECT_ROOT%\celery_mt5_worker.log"
call :log Starting dedicated serialized MT5 worker || goto :log_error

python -m celery -A config worker --loglevel=info --queues=mt5_execution --pool=solo --concurrency=1 --prefetch-multiplier=1 --hostname=mt5@%%h >> "%LOG_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

call :log Dedicated MT5 worker exited with code %EXIT_CODE%
set "RETURN_CODE=%EXIT_CODE%"
goto finish

:log_error
echo [%date% %time%] Unable to write to "%LOG_FILE%".
echo Another process is likely still running and holding the log file open.^
 Close the existing MT5 worker or any editor tailing the log, then run this script again.
set "RETURN_CODE=1"
goto finish

:log
>>"%LOG_FILE%" echo [%date% %time%] %*
exit /b %ERRORLEVEL%

:finish
popd 2>nul
if defined PAUSE_ON_EXIT pause
endlocal & exit /b %RETURN_CODE%

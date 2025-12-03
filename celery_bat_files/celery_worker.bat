@echo off
setlocal EnableExtensions
set "PAUSE_ON_EXIT=1"
if /I "%~1"=="--no-pause" (
    set "PAUSE_ON_EXIT="
    shift
)
set "RETURN_CODE=0"
rem Resolve project and repository roots relative to this script
for %%i in ("%~dp0..") do set "PROJECT_ROOT=%%~fi"
for %%i in ("%PROJECT_ROOT%\..") do set "REPO_ROOT=%%~fi"

pushd "%PROJECT_ROOT%" || exit /b 1

if not exist "%REPO_ROOT%\mt5_env\Scripts\activate.bat" (
    echo Virtual environment not found in "%REPO_ROOT%\mt5_env".>&2
    set "RETURN_CODE=1"
    goto finish
)

powershell -NoProfile -Command "$proc = Get-CimInstance Win32_Process -Filter \"CommandLine LIKE '%%celery -A config worker%%'\"; if ($proc) { exit 5 } else { exit 0 }"
set "PROC_CHECK=%ERRORLEVEL%"
if "%PROC_CHECK%"=="5" (
    echo Celery worker already running. Stop it before starting a new one.
    set "RETURN_CODE=1"
    goto finish
)
if not "%PROC_CHECK%"=="0" if not "%PROC_CHECK%"=="5" (
    echo [%date% %time%] Warning: Unable to verify existing Celery worker (code %PROC_CHECK%). Continuing anyway.
)

call "%REPO_ROOT%\mt5_env\Scripts\activate.bat"

set "LOG_FILE=%REPO_ROOT%\celery_worker.log"
call :log Starting Celery worker || goto :log_error

python -m celery -A config worker --loglevel=info --pool=threads --concurrency=4 >> "%LOG_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

call :log Celery worker exited with code %EXIT_CODE%

set "RETURN_CODE=%EXIT_CODE%"
goto finish

:log_error
echo [%date% %time%] Unable to write to "%LOG_FILE%".
echo Another process is likely still running and holding the log file open.^
 Close the existing Celery worker or any editor tailing the log, then run this script again.
set "RETURN_CODE=1"
goto finish

:log
>>"%LOG_FILE%" echo [%date% %time%] %*
exit /b %ERRORLEVEL%

:finish
popd 2>nul
if defined PAUSE_ON_EXIT pause
endlocal & exit /b %RETURN_CODE%

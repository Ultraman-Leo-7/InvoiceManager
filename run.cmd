@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
echo ========================================
echo Invoice Extractor - Folder Sync
echo ========================================
echo.
py -3 --version >nul 2>&1
if not errorlevel 1 goto USE_PY
python --version >nul 2>&1
if not errorlevel 1 goto USE_PYTHON
echo ERROR: Python 3 was not found.
echo Please install Python 3 and try again.
echo.
pause
exit /b 1
:USE_PY
set "PY=py -3"
goto CHECK_DEPS
:USE_PYTHON
set "PY=python"
goto CHECK_DEPS
:CHECK_DEPS
echo [1/3] Python found.
%PY% --version
echo [2/3] Checking dependencies...
%PY% -c "import fitz, openpyxl" >nul 2>&1
if not errorlevel 1 goto RUN
echo Installing pymupdf and openpyxl...
%PY% -m pip install pymupdf openpyxl
if not errorlevel 1 goto RUN
echo.
echo ERROR: Dependency installation failed.
echo.
pause
exit /b 1
:RUN
echo [3/3] Syncing invoice folder...
echo.
%PY% "%~dp0invoice_extract.py"
set ERR=%ERRORLEVEL%
echo.
if "%ERR%"=="0" echo Finished successfully.
if not "%ERR%"=="0" echo ERROR: Script failed with exit code %ERR%.
echo.
pause
exit /b %ERR%

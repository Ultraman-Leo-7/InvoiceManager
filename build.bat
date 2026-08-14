@echo off
chcp 65001 >nul
setlocal

python -m pip install -r requirements-dev.txt
if errorlevel 1 goto :error

python tools\generate_icon.py
if errorlevel 1 goto :error

pyinstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --icon "assets\InvoiceManager.ico" ^
    --name "InvoiceManager-Windows-x64" ^
    app.py
if errorlevel 1 goto :error

echo.
echo Build complete:
echo dist\InvoiceManager-Windows-x64.exe
goto :end

:error
echo.
echo Build failed. See the error messages above.
exit /b 1

:end
pause

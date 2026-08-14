@echo off
chcp 65001 >nul

python -m pip install -r requirements-dev.txt

pyinstaller ^
    --noconfirm ^
    --clean ^
    --windowed ^
    --name 发票管理工具 ^
    --add-data "invoice_extract.py;." ^
    --add-data "jd_qq.py;." ^
    --add-data "purchase_tracker.py;." ^
    app.py

pause

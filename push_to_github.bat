@echo off
echo ==============================================
echo   Alfred AI Agenti - GitHub'ga yuklash
echo ==============================================
git push -u origin main
echo.
if %errorlevel% equ 0 (
    echo [MUVAFFAQIYATLI] Barcha kodlar GitHub'ga yuklandi!
) else (
    echo [DIQQAT] Agar xatolik bersa, avval https://github.com/new manzilida
    echo 'alfred-job-agent' nomli yangi repository ochganingizga ishonch hosil qiling!
)
pause

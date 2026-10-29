@echo off
setlocal EnableDelayedExpansion
pushd "%~dp0"

echo ================================================================
echo   CodeDetector - Baslatiliyor
echo   Analiz Motoru: Gemma 4
echo ================================================================
echo.

REM 1. Python yorumlayicisini bul
set "PY_CMD="
for %%P in (
    "%LocalAppData%\Programs\Python\Python312\python.exe"
    "%LocalAppData%\Programs\Python\Python311\python.exe"
    "%LocalAppData%\Programs\Python\Python310\python.exe"
    "C:\Program Files\Python312\python.exe"
    "C:\Program Files\Python311\python.exe"
) do (
    if exist "%%~P" (
        set "PY_CMD="%%~P""
        goto :found_py
    )
)

where python >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        set "PY_CMD=python"
        goto :found_py
    )
)

where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        set "PY_CMD=py -3"
        goto :found_py
    )
)

:found_py
if "!PY_CMD!"=="" (
    echo [HATA] Python 3.10 veya uzeri bulunamadi.
    echo Otomatik kurulum icin 'setup.bat' dosyasini calistirabilir
    echo veya https://www.python.org/downloads/ adresinden Python kurabilirsiniz.
    echo.
    pause
    exit /b 1
)

REM 2. Sanal ortami [.venv] olustur
if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Python sanal ortami [.venv] olusturuluyor...
    !PY_CMD! -m venv .venv
    if !ERRORLEVEL! NEQ 0 (
        echo [HATA] Sanal ortam olusturulamadi.
        pause
        exit /b 1
    )
)

REM 3. Bagimliliklari yukle / dogrula
echo [2/3] Python paketleri kontrol ediliyor...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo [HATA] requirements.txt paketleri yuklenemedi. Internet baglantinizi kontrol edin.
    pause
    exit /b 1
)

REM 4. .env yoksa bilgilendir
if not exist ".env" (
    echo [BILGI] .env dosyasi bulunamadi; varsayilan ayarlar kullaniliyor [GEMMA_MODEL=gemma4:e2b].
)

REM 5. Flask uygulamasini baslat
echo [3/3] Web sunucusu baslatiliyor...
echo       Durdurmak icin Ctrl+C tuslarina basin.
echo.
".venv\Scripts\python.exe" app.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [HATA] Uygulama beklenmeyen bir kodla kapandi.
    pause
    exit /b 1
)

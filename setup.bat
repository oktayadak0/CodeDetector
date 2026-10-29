@echo off
setlocal EnableDelayedExpansion
pushd "%~dp0"

echo ======================================================================
echo   CodeDetector - Otomatik Kurulum Sihirbazi
echo   Analiz motoru: Gemma 4
echo ======================================================================
echo.

REM Katilimsiz mod kontrolu: setup.bat /y  veya  setup.bat --check
set "AUTO_YES=0"
set "CHECK_ONLY=0"
if /I "%~1"=="/y" set "AUTO_YES=1"
if /I "%~1"=="--yes" set "AUTO_YES=1"
if /I "%~1"=="--check" set "CHECK_ONLY=1"

REM ======================================================================
REM [1/6] PYTHON KONTROLU VE KURULUMU
REM ======================================================================
echo [1/6] Python 3.10+ kurulumu kontrol ediliyor...
call :find_python
if not "!PY_CMD!"=="" goto :python_ok

echo       Python 3.10+ bulunamadi.
if "!CHECK_ONLY!"=="1" exit /b 1
set "INSTALL_PY=E"
if "!AUTO_YES!"=="0" (
    set /p "INSTALL_PY=      Python 3.12 winget ile otomatik kurulsun mu? [E/H] [Varsayilan: E]: "
)
if /I "!INSTALL_PY!"=="H" (
    echo [HATA] Python olmadan devam edilemez: https://www.python.org/downloads/
    pause
    exit /b 1
)
echo       Python 3.12 indiriliyor ve kuruluyor...
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
call :find_python
if "!PY_CMD!"=="" (
    echo [HATA] Python kuruldu ancak bulunamadi. Terminali kapatip setup.bat dosyasini tekrar calistirin.
    pause
    exit /b 1
)

:python_ok
echo       Bulunan Python: !PY_CMD!

REM ======================================================================
REM [2/6] SANAL ORTAM [.venv] VE PAKET KURULUMU
REM ======================================================================
echo.
echo [2/6] Python sanal ortami [.venv] ve bagimliliklar hazirlaniyor...
if not exist ".venv\Scripts\python.exe" (
    !PY_CMD! -m venv .venv
    if !ERRORLEVEL! NEQ 0 (
        echo [HATA] .venv sanal ortami olusturulamadi.
        pause
        exit /b 1
    )
)

".venv\Scripts\python.exe" -m pip install --upgrade pip --disable-pip-version-check -q
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo [HATA] Python paketleri [requirements.txt] yuklenemedi.
    pause
    exit /b 1
)
echo       Python paketleri basariyla kuruldu.

REM ======================================================================
REM [3/6] .env YAPILANDIRMA DOSYASI
REM ======================================================================
echo.
echo [3/6] Ortam yapilandirmasi [.env] kontrol ediliyor...
if not exist ".env" (
    copy /y ".env.example" ".env" >nul
    echo       .env.example dosyasindan .env olusturuldu.
) else (
    echo       Mevcut .env dosyasi korundu.
)

if "!CHECK_ONLY!"=="1" goto :step_verify

REM ======================================================================
REM [4/6] OLLAMA KONTROLU VE KURULUMU
REM ======================================================================
echo.
echo [4/6] Ollama calisma ortami kontrol ediliyor...
call :find_ollama
if not "!OLLAMA_EXE!"=="" goto :ollama_ok

echo       Ollama sistemde bulunamadi.
echo       Not: Gercek Gemma 4 analizi icin Ollama gereklidir.
set "INSTALL_OL=E"
if "!AUTO_YES!"=="0" (
    set /p "INSTALL_OL=      Ollama simdi winget ile kurulsun mu? [E/H] [Varsayilan: E]: "
)
if /I not "!INSTALL_OL!"=="H" (
    echo       Ollama indiriliyor ve kuruluyor...
    winget install -e --id Ollama.Ollama --accept-package-agreements --accept-source-agreements
    call :find_ollama
)

:ollama_ok
if not "!OLLAMA_EXE!"=="" (
    echo       Bulunan Ollama: !OLLAMA_EXE!
)

REM ======================================================================
REM [5/6] GEMMA 4 MODEL KONTROLU VE KULLANICI ONAYLI INDIRME
REM ======================================================================
echo.
echo [5/6] Gemma 4 modeli kontrol ediliyor...
if "!OLLAMA_EXE!"=="" (
    echo [BILGI] Ollama kurulu olmadigi icin Gemma 4 model indirme adimi atlandi.
    echo         Daha sonra Ollama kurup 'ollama pull gemma4:e2b' komutunu calistirabilirsiniz.
    goto :step_verify
)

REM Ollama servisi calismiyorsa arka planda baslat
".venv\Scripts\python.exe" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:11434/api/version', timeout=2)" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo       Ollama servisi baslatiliyor...
    start "" /B "!OLLAMA_EXE!" serve >nul 2>&1
    timeout /t 3 /nobreak >nul
)

REM Kurulu bir gemma4 modeli var mi kontrol et
"!OLLAMA_EXE!" list 2>nul | findstr /I "gemma4" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo       Kurulu Gemma 4 modeli bulundu:
    "!OLLAMA_EXE!" list | findstr /I "NAME gemma4"
    goto :step_verify
)

echo.
echo       Henuz kurulu bir Gemma 4 modeli bulunamadi.
echo       Indirmek istediginiz Gemma 4 modelini secin [buyuk indirme boyutuna sahiptir]:
echo         [1] gemma4:e2b  [~4.6 GB - Onerilen: 4 GB+ VRAM veya 8-16 GB RAM icin]
echo         [2] gemma4:e4b  [~6.6 GB - 6-8 GB+ VRAM bulunan sistemler icin]
echo         [3] Simdilik indirme [Daha sonra 'ollama pull gemma4:e2b' ile indirebilirsiniz]
echo.
set "MODEL_CHOICE=1"
if "!AUTO_YES!"=="0" (
    set /p "MODEL_CHOICE=      Seciminiz [1/2/3] [Varsayilan: 1]: "
    if "!MODEL_CHOICE!"=="" set "MODEL_CHOICE=1"
)

if "!MODEL_CHOICE!"=="1" goto :pull_e2b
if "!MODEL_CHOICE!"=="2" goto :pull_e4b
echo       Model indirme adimi atlandi.
goto :step_verify

:pull_e2b
echo       gemma4:e2b indiriliyor [~4.6 GB]...
"!OLLAMA_EXE!" pull gemma4:e2b
".venv\Scripts\python.exe" -c "p='.env'; t=open(p,encoding='utf-8').read(); open(p,'w',encoding='utf-8').write(t.replace('GEMMA_MODEL=gemma4:e4b','GEMMA_MODEL=gemma4:e2b'))"
goto :step_verify

:pull_e4b
echo       gemma4:e4b indiriliyor [~6.6 GB]...
"!OLLAMA_EXE!" pull gemma4:e4b
".venv\Scripts\python.exe" -c "p='.env'; t=open(p,encoding='utf-8').read(); open(p,'w',encoding='utf-8').write(t.replace('GEMMA_MODEL=gemma4:e2b','GEMMA_MODEL=gemma4:e4b'))"
goto :step_verify

REM ======================================================================
REM [6/6] KURULUM TESTI VE BASLATMA
REM ======================================================================
:step_verify
echo.
echo [6/6] Otomatik testler calistirilarak kurulum dogrulaniyor...
".venv\Scripts\python.exe" -m pytest -q
if %ERRORLEVEL% NEQ 0 (
    echo [UYARI] Bazi testler basarisiz oldu. Yukaridaki ciktiyi kontrol edin.
    if "!CHECK_ONLY!"=="1" exit /b 1
) else (
    echo       Tum kurulum testleri basariyla gecti.
)

if "!CHECK_ONLY!"=="1" exit /b 0

echo.
echo ======================================================================
echo   Kurulum Tamamlandi!
echo   Uygulamayi istediginiz zaman 'run.bat' ile baslatabilirsiniz.
echo   Web adresi: http://127.0.0.1:5000
echo ======================================================================
echo.

set "START_NOW=E"
if "!AUTO_YES!"=="0" (
    set /p "START_NOW=CodeDetector simdi baslatilsin ve tarayicida acilsin mi? [E/H] [Varsayilan: E]: "
)
if /I not "!START_NOW!"=="H" (
    start "" http://127.0.0.1:5000
    ".venv\Scripts\python.exe" app.py
)
exit /b 0

REM ======================================================================
REM YARDIMCI ALT YORDAMLAR
REM ======================================================================
:find_python
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
        exit /b 0
    )
)
where python >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        set "PY_CMD=python"
        exit /b 0
    )
)
where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        set "PY_CMD=py -3"
        exit /b 0
    )
)
exit /b 0

:find_ollama
set "OLLAMA_EXE="
where ollama >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "OLLAMA_EXE=ollama"
    exit /b 0
)
if exist "%LocalAppData%\Programs\Ollama\ollama.exe" (
    set "OLLAMA_EXE=%LocalAppData%\Programs\Ollama\ollama.exe"
    exit /b 0
)
if exist "C:\Program Files\Ollama\ollama.exe" (
    set "OLLAMA_EXE=C:\Program Files\Ollama\ollama.exe"
    exit /b 0
)
exit /b 0

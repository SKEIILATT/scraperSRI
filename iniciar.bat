@echo off
setlocal enabledelayedexpansion
REM ============================================================
REM SRI Scraper - Banco Guayaquil
REM Crea el entorno virtual (si hace falta), instala dependencias
REM y arranca la aplicacion.
REM ============================================================
REM NOTA: enabledelayedexpansion es necesario porque PYTHON_EXE se
REM setea (via "call :find_python") y se lee dentro del mismo bloque
REM "if not exist (...)". Sin delayed expansion, %PYTHON_EXE% dentro
REM de ese bloque se sustituye ANTES de que la llamada se ejecute
REM (cmd.exe procesa cada bloque "(...)" como una sola unidad), y
REM siempre aparece vacio. Por eso aqui se usa !PYTHON_EXE!.

cd /d "%~dp0"

set VENV_NEW=0

if not exist ".venv\Scripts\python.exe" (
    call :find_python
    if not defined PYTHON_EXE (
        echo [ERROR] No se encontro Python instalado en este equipo.
        echo.
        echo Instale Python 3.10 o superior desde https://www.python.org/downloads/
        echo IMPORTANTE: durante la instalacion marque la casilla "Add python.exe to PATH".
        echo Luego vuelva a ejecutar este archivo.
        pause
        exit /b 1
    )

    echo Creando entorno virtual .venv usando: "!PYTHON_EXE!" !PYTHON_ARGS! ...
    "!PYTHON_EXE!" !PYTHON_ARGS! -m venv .venv
    if errorlevel 1 (
        echo [ERROR] No se pudo crear el entorno virtual con "!PYTHON_EXE!" !PYTHON_ARGS!.
        pause
        exit /b 1
    )

    ".venv\Scripts\python.exe" --version >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] El entorno virtual se creo pero el interprete de Python
        echo dentro de .venv no funciona ^(instalacion de Python danada o incompleta^).
        echo.
        echo Reinstale Python desde https://www.python.org/downloads/, borre la
        echo carpeta ".venv" de este proyecto y vuelva a ejecutar este archivo.
        rmdir /s /q ".venv" >nul 2>&1
        pause
        exit /b 1
    )
    set VENV_NEW=1
)

if not exist ".env" (
    echo [ERROR] No se encontro el archivo .env
    echo Copie .env.example como .env y complete los valores.
    pause
    exit /b 1
)

echo Instalando dependencias (requirements.txt) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERROR] Fallo la instalacion de dependencias.
    pause
    exit /b 1
)

if "%VENV_NEW%"=="1" (
    echo Instalando navegador Chromium para Playwright ...
    ".venv\Scripts\python.exe" -m playwright install chromium
)

echo.
echo Iniciando SRI Scraper...
".venv\Scripts\python.exe" main.py

if errorlevel 1 (
    echo.
    echo [ERROR] La aplicacion finalizo con errores.
    pause
)

exit /b 0

:find_python
REM Busca un interprete de Python utilizable, en orden de preferencia.
REM Deja el resultado en PYTHON_EXE (ejecutable, sin comillas) y PYTHON_ARGS
REM (argumentos extra, ej. "-3" para el lanzador "py"). Se usan dos variables
REM separadas -en vez de una sola con comillas embebidas- porque mezclar
REM comillas dentro de "set "VAR=..."" es fragil en cmd.exe.
set "PYTHON_EXE="
set "PYTHON_ARGS="

py -3 --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_EXE=py"
    set "PYTHON_ARGS=-3"
    goto :eof
)

python --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_EXE=python"
    goto :eof
)

python3 --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_EXE=python3"
    goto :eof
)

echo No se encontro Python en el PATH. Buscando en ubicaciones comunes...
REM %ProgramFiles(x86)% tiene parentesis en el nombre; usarlo directo dentro
REM de un bloque for(...) confunde al parser de cmd.exe. Se copia a una
REM variable sin parentesis antes de entrar al bloque.
set "PF86=%ProgramFiles(x86)%"
for %%D in (
    "%LocalAppData%\Programs\Python"
    "%ProgramFiles%"
    "%PF86%"
) do call :search_dir "%%~D"
if defined PYTHON_EXE goto :eof

echo No se encontro Python en ubicaciones comunes.
echo Buscando en todo el disco %SystemDrive%\, esto puede tardar varios minutos...
call :search_dir "%SystemDrive%"

goto :eof

:search_dir
REM %1 = carpeta donde buscar python.exe recursivamente
if defined PYTHON_EXE goto :eof
for /f "usebackq delims=" %%P in (`dir /b /s /a-d "%~1\python.exe" 2^>nul`) do (
    "%%P" --version >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=%%P"
        goto :eof
    )
)
goto :eof

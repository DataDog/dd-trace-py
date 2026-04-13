@echo off
setlocal enabledelayedexpansion

REM Test a built wheel inside the Windows Docker image.
REM Usage: windows-docker-test.bat <UV_PYTHON>
REM   UV_PYTHON: Python spec (e.g. cpython-3.12-windows-x86_64)

set UV_PYTHON_SPEC=%1
if "%UV_PYTHON_SPEC%"=="" (
    echo ERROR: UV_PYTHON argument required ^(e.g. cpython-3.12-windows-x86_64^)
    exit /b 1
)

set UV_LINK_MODE=copy

echo === Finding Python: %UV_PYTHON_SPEC% ===
for /f "usebackq tokens=*" %%i in (`uv python find %UV_PYTHON_SPEC%`) do set PYTHON_EXE=%%i
if "%PYTHON_EXE%"=="" (
    echo ERROR: Could not find Python %UV_PYTHON_SPEC%
    exit /b 1
)
echo Python: %PYTHON_EXE%

echo === Creating venv ===
uv venv --python="%PYTHON_EXE%" C:\testvenv
if errorlevel 1 (
    echo ERROR: uv venv failed
    exit /b 1
)

echo === Installing wheel ===
for %%w in (C:\workspace\dist\*.whl) do (
    uv pip install --python C:\testvenv\Scripts\python.exe "%%w"
    if errorlevel 1 (
        echo ERROR: wheel install failed
        exit /b 1
    )
)

echo === Running smoke test ===
C:\testvenv\Scripts\python.exe C:\workspace\tests\smoke_test.py
if errorlevel 1 (
    echo ERROR: smoke test failed
    exit /b 1
)

echo === Smoke test passed ===

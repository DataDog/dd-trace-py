@echo off
setlocal enabledelayedexpansion

REM Build a wheel inside the Windows Docker image.
REM Usage: windows-docker-build.bat <VC_ARCH>
REM   VC_ARCH: "amd64" or "x64_x86" (for x86 cross-compile)
REM
REM Expects UV_PYTHON env var to be set (e.g. cpython-3.12-windows-x86_64)

set VC_ARCH=%1
if "%VC_ARCH%"=="" (
    echo ERROR: VC_ARCH argument required ^(amd64 or x64_x86^)
    exit /b 1
)

REM Find vcvarsall.bat via vswhere
for /f "usebackq tokens=*" %%i in (`"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe" -property installationPath -latest`) do set VS_PATH=%%i
set VCVARSALL=%VS_PATH%\VC\Auxiliary\Build\vcvarsall.bat

if not exist "%VCVARSALL%" (
    echo ERROR: vcvarsall.bat not found at %VCVARSALL%
    exit /b 1
)

echo === Activating MSVC environment: %VC_ARCH% ===
call "%VCVARSALL%" %VC_ARCH%
if errorlevel 1 (
    echo ERROR: vcvarsall.bat failed
    exit /b 1
)

REM Required for setuptools/distutils MSVC detection on Python 3.12+
set DISTUTILS_USE_SDK=1
set MSSdk=1

echo === Building wheel ===
uv build --wheel --out-dir C:\workspace\dist C:\workspace
if errorlevel 1 (
    echo ERROR: uv build failed
    exit /b 1
)

echo === Stripping source files from wheel ===
for %%w in (C:\workspace\dist\*.whl) do (
    uv run --no-project C:\workspace\scripts\zip_filter.py "%%w" *.c *.cpp *.cc *.h *.hpp *.pyx *.md
    if errorlevel 1 (
        echo ERROR: zip_filter.py failed
        exit /b 1
    )
)

echo === Build complete ===
dir C:\workspace\dist\*.whl

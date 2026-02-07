@echo off
setlocal enabledelayedexpansion

set ROOT_DIR=%~dp0..
set DATA_DIR=%ROOT_DIR%\app\engine_tokenizer_data

if not exist "%DATA_DIR%" (
  echo engine_tokenizer_data not found: %DATA_DIR%
  exit /b 1
)

set TAG=
if /I "%PROCESSOR_ARCHITECTURE%"=="AMD64" set TAG=win_amd64

if "%TAG%"=="" (
  echo Unsupported architecture for pruning: %PROCESSOR_ARCHITECTURE%
  exit /b 1
)

echo Keeping tokenizer bundle tag: %TAG%
for %%F in ("%DATA_DIR%\lindera_python_ipadic-*.whl") do (
  echo %%~nF | findstr /I "%TAG%" >nul
  if errorlevel 1 (
    echo Removing %%F
    del /F /Q "%%F"
  )
)

echo Done.

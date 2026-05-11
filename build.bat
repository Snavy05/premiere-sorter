@echo off
REM build.bat — Local Windows build script for SteadyCut
REM Usage: build.bat

echo ===================================================
echo   SteadyCut — Windows Build
echo ===================================================
echo.

REM 1. Ensure build tools are present
echo ^> Installing/upgrading build tools...
pip install --upgrade pyinstaller py7zr
if %ERRORLEVEL% neq 0 goto :error

REM 2. Clean previous artifacts
echo ^> Cleaning previous build...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

REM 3. Run PyInstaller
echo ^> Building with PyInstaller...
pyinstaller steadycut.spec
if %ERRORLEVEL% neq 0 goto :error

REM 4. Verify
if exist "dist\SteadyCut\SteadyCut.exe" (
    echo.
    echo [OK]  Build succeeded: dist\SteadyCut\SteadyCut.exe
) else (
    echo [FAIL] dist\SteadyCut\SteadyCut.exe not found
    goto :error
)

REM 5. Zip for distribution
echo.
set /p ZIP_IT="Zip for distribution? [y/N] "
if /i "%ZIP_IT%"=="y" (
    powershell -Command "Compress-Archive -Path dist\SteadyCut -DestinationPath dist\SteadyCut-Windows-x64.zip -Force"
    if %ERRORLEVEL% neq 0 goto :error
    echo [OK]  Zipped: dist\SteadyCut-Windows-x64.zip
)

echo.
echo ===================================================
echo   Done!
echo ===================================================
goto :eof

:error
echo.
echo Build failed. Check the output above for errors.
exit /b 1

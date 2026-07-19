@echo off
REM vibe-local Windows launcher wrapper
REM Launches vibe-local.ps1 with ExecutionPolicy bypass
REM Set UTF-8 code page to prevent character garbling (文字化け対策)
chcp 65001 >nul 2>&1
powershell.exe -ExecutionPolicy Bypass -File "%~dp0vibe-local.ps1" %*

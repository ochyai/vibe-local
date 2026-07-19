@echo off
REM vibe-local Windows installer wrapper
REM Launches install.ps1 with ExecutionPolicy bypass
powershell.exe -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*

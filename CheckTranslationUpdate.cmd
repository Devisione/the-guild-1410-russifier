@echo off
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File "%~dp0check_translation_update.ps1" -Root "%~dp0." %*
exit /b 0

@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\Couch\dev\ai-orchestrator\.runtime\ai-resource-governor\bin\hermes.ps1" %*
exit /b %ERRORLEVEL%

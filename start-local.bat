@echo off
rem Builds and starts CAMVIEW Command Center in Docker (no sign-in, this computer only).
cd /d "%~dp0"
if not exist ".env" (
  echo No .env file found next to this script.
  echo Copy backend\.env.example to .env and fill in CAMVIEW_API_KEY, then run this again.
  pause
  exit /b 1
)
docker compose -f docker-compose.local.yml up -d --build
if errorlevel 1 (
  echo.
  echo Docker could not start the app. Is Docker Desktop running?
  pause
  exit /b 1
)
echo.
echo CAMVIEW Command Center is starting.
echo   Administrators (this computer, no sign-in):  http://localhost:5000
echo   Client portal (network, sign-in required):    http://%COMPUTERNAME%:5001
timeout /t 5 >nul
start "" http://localhost:5000

@echo off
rem Deletes the LOCAL database (reviews, tickets, settings made in the app, audit trail) and starts fresh.
rem Camview's own alarms are not touched; they are read again on the next start.
cd /d "%~dp0"
echo This deletes all local data of CAMVIEW Command Center (reviews, tickets, app settings, audit trail).
set /p ok=Type YES to continue:
if /i not "%ok%"=="YES" (
  echo Cancelled.
  exit /b 0
)
docker compose -f docker-compose.local.yml down -v
echo Local data deleted. Run start-local.bat to start fresh.
pause

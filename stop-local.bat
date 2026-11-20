@echo off
rem Stops CAMVIEW Command Center (the local database is kept).
cd /d "%~dp0"
docker compose -f docker-compose.local.yml down

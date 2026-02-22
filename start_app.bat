@echo off
echo Starting Movie Manager Web Server...
cd /d "%~dp0"
python movie_manager/web_server.py
pause

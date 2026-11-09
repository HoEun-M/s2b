@echo off
rem Daily crawl (Task Scheduler, 2 AM): G2B + S2B last 7 days, then upload to GitHub
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
python -u daily_crawl.py

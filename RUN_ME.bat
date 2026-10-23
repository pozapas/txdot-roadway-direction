@echo off
rem Double-click to open the TxDOT travel-direction window.
rem You can also drag the TxDOT .zip or .shp file onto this file.
setlocal EnableDelayedExpansion
cd /d "%~dp0"

rem Candidate Pythons: Anaconda / Miniforge / Miniconda first, then any python on PATH.
set "LIST="
for %%P in (
  "%LOCALAPPDATA%\anaconda3\python.exe"
  "%USERPROFILE%\anaconda3\python.exe"
  "%ProgramData%\anaconda3\python.exe"
  "%LOCALAPPDATA%\miniforge3\python.exe"
  "%USERPROFILE%\miniforge3\python.exe"
  "%USERPROFILE%\miniconda3\python.exe"
  "%LOCALAPPDATA%\miniconda3\python.exe"
) do if exist %%P set "LIST=!LIST! %%P"
rem Skip the Microsoft Store stub in WindowsApps, which is not a real Python.
for /f "delims=" %%P in ('where python 2^>nul ^| findstr /v /i /l "WindowsApps"') do set "LIST=!LIST! "%%P""

rem Use the first one that already has the packages.
set "PY=" & set "BASE="
for %%P in (!LIST!) do (
  if not defined BASE set "BASE=%%~P"
  if not defined PY (
    "%%~P" -c "import geopandas, pyogrio, shapefile, pyproj" >nul 2>&1 && set "PY=%%~P"
  )
)

if not defined PY (
  echo.
  if not defined BASE (
    echo No Python was found. Install Anaconda from https://www.anaconda.com/download
    echo and then double-click RUN_ME.bat again.
    pause & exit /b 1
  )
  echo Python found: !BASE!
  echo but it is missing some packages the tool needs ^(see requirements.txt^).
  set /p ANS="Install them now with pip? (Y/N) "
  if /i not "!ANS!"=="Y" exit /b 1
  "!BASE!" -m pip install -r requirements.txt
  if errorlevel 1 (pause & exit /b 1)
  set "PY=!BASE!"
)

echo Using Python: %PY%
"%PY%" txdot_direction_gui.py %1
if errorlevel 1 pause

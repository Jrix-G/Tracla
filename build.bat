@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title Construction de Transcripteur

echo ============================================================
echo  Construction de Transcripteur.exe
echo ============================================================
echo.

rem --- 1. Python present ? -----------------------------------------------
set "PY="
py -3.11 --version >nul 2>&1 && set "PY=py -3.11"
if not defined PY ( py -3 --version >nul 2>&1 && set "PY=py -3" )
if not defined PY ( python --version >nul 2>&1 && set "PY=python" )

if not defined PY (
  echo [X] Python n'est pas installe sur cet ordinateur.
  echo.
  echo     Installe Python 3.11 depuis https://www.python.org/downloads/
  echo     Pense a cocher "Add python.exe to PATH" pendant l'installation,
  echo     puis relance ce fichier build.bat.
  echo.
  pause
  exit /b 1
)

for /f "tokens=2" %%v in ('%PY% --version 2^>^&1') do set "VER=%%v"
echo [1/5] Python detecte : %VER%  (%PY%)

rem --- 2. Environnement virtuel -------------------------------------------
if not exist ".venv\Scripts\python.exe" (
  echo [2/5] Creation de l'environnement virtuel...
  %PY% -m venv .venv || goto :erreur
) else (
  echo [2/5] Environnement virtuel deja present.
)
set "VPY=.venv\Scripts\python.exe"

rem --- 3. Dependances ------------------------------------------------------
echo [3/5] Installation des dependances (quelques minutes la premiere fois)...
"%VPY%" -m pip install --upgrade pip --quiet || goto :erreur
"%VPY%" -m pip install -r requirements.txt --quiet || goto :erreur
"%VPY%" -m pip install pyinstaller==6.11.1 --quiet || goto :erreur

rem --- 4. Nettoyage --------------------------------------------------------
echo [4/5] Nettoyage des constructions precedentes...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

rem --- 5. PyInstaller ------------------------------------------------------
echo [5/5] Construction de l'executable...
"%VPY%" -m PyInstaller ^
  --noconfirm --clean --onedir --console ^
  --name Transcripteur ^
  --add-data "ui;ui" ^
  --collect-all faster_whisper ^
  --collect-all ctranslate2 ^
  --collect-all av ^
  --collect-all tokenizers ^
  --collect-all onnxruntime ^
  --collect-all huggingface_hub ^
  --collect-submodules waitress ^
  --hidden-import waitress ^
  --hidden-import flask ^
  --exclude-module torch ^
  --exclude-module tkinter ^
  --exclude-module matplotlib ^
  --exclude-module scipy ^
  app.py || goto :erreur

if not exist "dist\Transcripteur\Transcripteur.exe" goto :erreur

echo.
echo ============================================================
echo  Termine.
echo  Executable : dist\Transcripteur\Transcripteur.exe
echo  Livrable   : zippe tout le dossier dist\Transcripteur\
echo ============================================================
echo.
pause
exit /b 0

:erreur
echo.
echo [X] La construction a echoue. Lis le message d'erreur ci-dessus.
echo     Cause la plus frequente : pas de connexion Internet pendant
echo     l'installation des dependances.
echo.
pause
exit /b 1

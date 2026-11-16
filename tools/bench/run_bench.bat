@echo off
REM Essai Qt / web local sur ce PC. Usage : double-clic non prevu, lancer depuis une invite de commandes :
REM   tools\bench\run_bench.bat "D:\rushes\cam1.mp4" "D:\rushes\cam2.mp4" "D:\rushes\cam3.mp4"
REM Ne modifie aucun media. Resultats dans %USERPROFILE%\kolnoa_bench (REPORT.md et results.json).
setlocal
if "%~1"=="" (
  echo Donne 1 a 9 videos, une par camera. Exemple :
  echo   tools\bench\run_bench.bat "D:\rushes\cam1.mp4" "D:\rushes\cam2.mp4" "D:\rushes\cam3.mp4"
  exit /b 1
)
where python >nul 2>nul || (echo Python 3.10+ est requis : https://www.python.org/downloads/ & exit /b 1)
where ffmpeg >nul 2>nul || (echo ffmpeg et ffprobe doivent etre dans le PATH : https://www.gyan.dev/ffmpeg/builds/ & exit /b 1)
python -m pip install --quiet --user psutil numpy PySide6 || (echo Installation des dependances impossible & exit /b 1)
python "%~dp0bench_run.py" --media %* --out "%USERPROFILE%\kolnoa_bench"
echo.
echo Envoie le fichier %USERPROFILE%\kolnoa_bench\REPORT.md
endlocal

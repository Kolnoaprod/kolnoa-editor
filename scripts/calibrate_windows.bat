@echo off
rem Calibration de la synchro sur les rushes du lecteur G: (LECTURE SEULE : les originaux ne sont jamais modifies).
rem Prerequis : Python 3.10+, "pip install numpy", ffmpeg et ffprobe dans le PATH. A lancer depuis la racine du depot.
rem Resultats : %OUT%\mairie et %OUT%\jeremy (report.md, report.json, results.csv, inventory.csv). Rien n'est envoye nulle part.
setlocal
set PYTHONPATH=src
set ROOT=G:\Mon Drive\KOLNOA\MARIAGE YOHANNA ET ARTHUR 07-10092026
if "%OUT%"=="" set OUT=C:\Kolnoa\calibration

echo === 1/4 Plan MAIRIE (aucun media decode) ===
python -m kolnoa_editor.cli sync-calibrate "%ROOT%" --scenario mairie --out "%OUT%\mairie" --dry-run || goto :err
echo === 2/4 Analyse MAIRIE ===
python -m kolnoa_editor.cli sync-calibrate "%ROOT%" --scenario mairie --out "%OUT%\mairie" --with-hint || goto :err
echo === 3/4 Plan JEREMY (aucun media decode) ===
python -m kolnoa_editor.cli sync-calibrate "%ROOT%" --scenario jeremy --out "%OUT%\jeremy" --dry-run || goto :err
echo === 4/4 Analyse JEREMY (camera, audio, drone) ===
python -m kolnoa_editor.cli sync-calibrate "%ROOT%" --scenario jeremy --out "%OUT%\jeremy" --with-hint --max-video-seconds 600 || goto :err
echo Termine. Ouvrez %OUT%\mairie\report.md et %OUT%\jeremy\report.md
goto :eof
:err
echo ERREUR : voir le message ci-dessus (code %errorlevel%).
exit /b 1

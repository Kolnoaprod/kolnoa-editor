@echo off
REM Fabrique KolnoaBenchmark.exe sur Windows (Python 3.11+). Normalement inutile : GitHub le fabrique automatiquement.
python -m pip install pyinstaller PySide6 psutil numpy
if not exist ffmpeg_bin mkdir ffmpeg_bin
echo Place ffmpeg.exe et ffprobe.exe dans tools\bench\exe\ffmpeg_bin avant de continuer.
cd /d %~dp0
python -m PyInstaller --noconfirm --onefile --windowed --name KolnoaBenchmark ^
  --paths .. --add-data "..\bench_web.html;." --add-binary "ffmpeg_bin\ffmpeg.exe;bin" --add-binary "ffmpeg_bin\ffprobe.exe;bin" ^
  --hidden-import bench_qt --hidden-import bench_run --hidden-import bench_common ^
  --collect-submodules PySide6.QtMultimedia --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.Qt3DCore ^
  ..\bench_gui.py

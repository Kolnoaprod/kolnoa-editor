@echo off
REM Fabrique KolnoaEditorTest.exe sur Windows (Python 3.11+). Normalement inutile : GitHub le fabrique automatiquement (zip dans « Releases »).
cd /d %~dp0\..\..
python -m pip install pyinstaller PySide6 numpy psutil
if not exist tools\winapp\ffmpeg_bin mkdir tools\winapp\ffmpeg_bin
echo Place ffmpeg.exe et ffprobe.exe dans tools\winapp\ffmpeg_bin avant de continuer.
python -m PyInstaller --noconfirm --onefile --windowed --name KolnoaEditorTest --paths src ^
  --add-binary "tools\winapp\ffmpeg_bin\ffmpeg.exe;bin" --add-binary "tools\winapp\ffmpeg_bin\ffprobe.exe;bin" ^
  --hidden-import kolnoa_editor.player.bench --hidden-import kolnoa_editor.player.suite ^
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.Qt3DCore tools\winapp\kolnoa_test_app.py

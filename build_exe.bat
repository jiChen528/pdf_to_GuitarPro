@echo off
rem Build a single-file PDF2GP.exe (output to repo root)
rem Prerequisites: npm install (alphaTab) and pip install pymupdf pyinstaller tkinterdnd2
chcp 65001 >nul
pyinstaller --noconfirm --clean --onefile --windowed --name PDF2GP --distpath . ^
  --add-data "gpengine/alphatab_writer.js;." ^
  --add-data "node_modules/@coderline/alphatab/dist/alphaTab.js;." ^
  --collect-all tkinterdnd2 ^
  gui.py
echo.
echo Done: PDF2GP.exe

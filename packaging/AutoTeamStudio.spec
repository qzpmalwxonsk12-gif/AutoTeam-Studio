# Build the GUI onedir and a self-contained worker in the same distributable.
from pathlib import Path
import shutil

root = Path(SPECPATH).parent
gui = Analysis([str(root / 'src/autoteam_studio/__main__.py')], pathex=[str(root / 'src')],
    datas=[(str(root / 'docs/USER_GUIDE.md'), 'docs')],
    hiddenimports=['autoteam_studio.core.api', 'autoteam_studio.core.setup', 'autoteam_studio.smoke'],
    excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtQml', 'tkinter'])
gui_pyz = PYZ(gui.pure)
gui_exe = EXE(gui_pyz, gui.scripts, [], exclude_binaries=True, name='AutoTeamStudio',
    console=False, debug=False, upx=False)
gui_collection = COLLECT(gui_exe, gui.binaries, gui.datas, strip=False, upx=False, name='AutoTeamStudio')

worker = Analysis([str(root / 'src/autoteam_studio/worker/__main__.py')], pathex=[str(root / 'src')],
    hiddenimports=['autoteam_studio.worker.engine', 'autoteam_studio.worker.fake_cli', 'autoteam_studio.worker.journal'],
    excludes=['PySide6', 'tkinter'])
worker_pyz = PYZ(worker.pure)
worker_exe = EXE(worker_pyz, worker.scripts, worker.binaries, worker.datas,
    name='AutoTeamStudioWorker', console=True, debug=False, upx=False)
# EXE normalizes names to basenames; copy the completed worker into the GUI bundle.
worker_directory = Path(DISTPATH) / 'AutoTeamStudio' / 'worker'
worker_directory.mkdir(parents=True, exist_ok=True)
shutil.copy2(worker_exe.name, worker_directory / 'AutoTeamStudioWorker.exe')

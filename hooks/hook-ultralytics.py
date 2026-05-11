# PyInstaller hook for ultralytics — collects all data files and submodules
# that ultralytics loads dynamically at runtime (YAML configs, assets, etc.)
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas         = collect_data_files("ultralytics")
hiddenimports = collect_submodules("ultralytics")

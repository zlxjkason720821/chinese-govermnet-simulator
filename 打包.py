"""把游戏打包成单个 exe。

    python 打包.py

产物在 dist/ 下，只有一个文件，拷给别人就能玩，不需要装 Python。

要点：
  data/ 下的 yaml 必须一起打进去（--add-data），否则运行时找不到制度规则；
  存档不写在 exe 旁边，写到用户目录（见 gongpu/paths.py）——
  exe 可能被放在 Program Files 之类没有写权限的地方。
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "公仆之心"

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm", "--clean",
    "--onefile",                 # 单文件，拷一个就够
    "--windowed",                # 不弹控制台黑框
    "--name", NAME,
    "--paths", str(ROOT),
    "--add-data", "%s%s%s" % (ROOT / "data", os.pathsep, "data"),
    # PySide6 只用到这三个模块，其余不打进去，能省一大截体积
    "--exclude-module", "PySide6.QtWebEngineCore",
    "--exclude-module", "PySide6.QtWebEngineWidgets",
    "--exclude-module", "PySide6.Qt3DCore",
    "--exclude-module", "PySide6.QtMultimedia",
    "--exclude-module", "PySide6.QtQuick",
    "--exclude-module", "PySide6.QtQml",
    "--exclude-module", "matplotlib",
    "--exclude-module", "numpy",
    "--exclude-module", "pytest",
    str(ROOT / "app" / "main.py"),
]

print(" ".join(cmd))
r = subprocess.run(cmd, cwd=ROOT)
if r.returncode:
    sys.exit(r.returncode)

exe = ROOT / "dist" / (NAME + ".exe")
if exe.exists():
    mb = exe.stat().st_size / 1024 / 1024
    print("\n打包完成：%s（%.0f MB）" % (exe, mb))
else:
    print("\n没找到产物，检查上面的输出")

"""数据与存档目录。

源码运行和打包成 exe 之后，这两个目录的位置不一样：

  源码    data/ 和 saves/ 就在项目里
  打包    data/ 被 PyInstaller 解到临时目录（sys._MEIPASS），只读；
          存档不能写在那儿，要放到用户目录下

所有模块都从这里取路径，别再各自用 __file__ 往上推。
"""
import os
import sys
from pathlib import Path

APP_NAME = "公仆之心"

FROZEN = getattr(sys, "frozen", False)


def _base():
    if FROZEN:
        # PyInstaller 单文件模式把打包的资源解到 _MEIPASS
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


DATA = _base() / "data"


def _save_root():
    if not FROZEN:
        return Path(__file__).resolve().parent.parent / "saves"
    # 打包之后存档要写到用户目录：exe 可能被放在 Program Files 之类没有写权限的地方
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    root = Path(local) if local else Path.home()
    return root / APP_NAME / "saves"


SAVES = _save_root()

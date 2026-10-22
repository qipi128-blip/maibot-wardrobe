"""把插件目录以包的形式加载，方便 unittest 直接导入。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PACKAGE_NAME = "maibot_wardrobe"
PACKAGE_DIR = Path(__file__).resolve().parents[1]


def load_package() -> None:
    if PACKAGE_NAME in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        PACKAGE_NAME,
        PACKAGE_DIR / "__init__.py",
        submodule_search_locations=[str(PACKAGE_DIR)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载插件包")
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE_NAME] = module
    spec.loader.exec_module(module)

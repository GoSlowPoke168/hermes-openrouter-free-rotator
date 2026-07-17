"""Load the plugin package under a synthetic importable name.

The plugin directory name contains hyphens (hermes loads it via importlib
with a custom module name, so relative imports work); tests mirror that.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = "hermes_openrouter_free_rotator"

if PKG not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        PKG, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = module
    spec.loader.exec_module(module)

"""Load the plugin package under a synthetic importable name.

The plugin directory name contains hyphens (hermes loads it via importlib
with a custom module name, so relative imports work); tests mirror that.
"""

import contextlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = "hermes_openrouter_free_rotator"

if PKG not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        PKG, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = module
    spec.loader.exec_module(module)


class Host:
    """A stand-in Hermes install: fake config IO, real plugin code.

    Only the host boundary and the network are faked, so build_plan,
    build_catalog_plan and apply_all all execute for real — that integration
    is where the orchestration bugs have actually been.
    """

    def __init__(self, tmp_path):
        self.dir = tmp_path
        self.config_path = tmp_path / "config.yaml"
        self.config_path.write_text("placeholder\n", encoding="utf-8")
        self.cfg = {
            "model": {"provider": "openrouter", "default": "old/model:free"},
            "fallback_providers": [],
        }
        self.saved_configs = []      # every save_config call, in order
        self.state = {}
        self.saved_states = []
        self.lock_free = True
        self.cache_cleared = []
        self.curated = [{"id": "paid/curated", "description": "recommended"}]

    # -- what the plugin sees as "the config on disk" --
    def load_config(self):
        import copy

        return copy.deepcopy(self.cfg)

    def save_config(self, cfg):
        import copy

        self.cfg = copy.deepcopy(cfg)
        self.saved_configs.append(copy.deepcopy(cfg))

    @property
    def config_written(self) -> bool:
        return bool(self.saved_configs)

    @property
    def default(self):
        return (self.cfg.get("model") or {}).get("default")

    @property
    def chain(self):
        return [
            f"{e.get('provider')}/{e.get('model')}"
            for e in self.cfg.get("fallback_providers") or []
        ]


@pytest.fixture
def host(tmp_path, monkeypatch):
    """Install the fake host and redirect the plugin's state dir into tmp_path."""
    from hermes_openrouter_free_rotator import cli, configsync
    from hermes_openrouter_free_rotator import state as state_mod

    h = Host(tmp_path)

    cfg_mod = types.ModuleType("hermes_cli.config")
    cfg_mod.load_config = h.load_config
    cfg_mod.load_config_readonly = h.load_config
    cfg_mod.save_config = h.save_config
    cfg_mod.get_config_path = lambda: h.config_path

    fb_mod = types.ModuleType("hermes_cli.fallback_config")
    fb_mod.get_fallback_chain = lambda cfg: list(cfg.get("fallback_providers") or [])

    mc_mod = types.ModuleType("hermes_cli.model_catalog")
    mc_mod.get_catalog = lambda: {"providers": {"openrouter": {"models": h.curated}}}

    models_mod = types.ModuleType("hermes_cli.models")
    models_mod.clear_provider_models_cache = h.cache_cleared.append
    models_mod.OPENROUTER_MODELS = []

    pkg = types.ModuleType("hermes_cli")
    for name, mod in [
        ("hermes_cli", pkg), ("hermes_cli.config", cfg_mod),
        ("hermes_cli.fallback_config", fb_mod),
        ("hermes_cli.model_catalog", mc_mod), ("hermes_cli.models", models_mod),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)

    # State and manifest live under tmp_path, never the real ~/.hermes.
    monkeypatch.setattr(state_mod, "state_dir", lambda: h.dir)
    monkeypatch.setattr(configsync, "state_dir", lambda: h.dir)
    monkeypatch.setattr(state_mod, "load_state", lambda: dict(h.state))

    def save_state(s):
        h.state = dict(s)
        h.saved_states.append(dict(s))

    monkeypatch.setattr(state_mod, "save_state", save_state)

    @contextlib.contextmanager
    def fake_lock():
        yield h.lock_free

    monkeypatch.setattr(state_mod, "sync_lock", fake_lock)
    monkeypatch.setattr(cli, "_catalog_will_be_honored", lambda: (True, ""))
    return h

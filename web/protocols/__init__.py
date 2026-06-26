"""Protocol registry + dynamic loader."""
import importlib
import importlib.util
import sys
from pathlib import Path
from web.protocol import Protocol


REGISTRY: dict = {
    "neurapy": "web.protocols.neurapy:NeuraPYProtocol",
}


def load(name_or_path: str) -> Protocol:
    """Resolve a protocol by registry name or 'path/to/file.py:ClassName'.

    Raises ValueError on miss (with available names listed).
    """
    if not name_or_path:
        available = ", ".join(sorted(REGISTRY)) or "(none registered)"
        raise ValueError(f"--protocol required. Available: {available}")

    is_path_form = ":" in name_or_path and (
        name_or_path.endswith(".py") or "/" in name_or_path or name_or_path.startswith(".")
    )
    if is_path_form:
        path_str, _, cls_name = name_or_path.partition(":")
        path = Path(path_str).resolve()
        if not path.exists():
            raise ValueError(f"protocol file not found: {path}")
        spec = importlib.util.spec_from_file_location(path.stem, str(path))
        if spec is None or spec.loader is None:
            raise ValueError(f"cannot load spec from {path}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        cls = getattr(mod, cls_name, None)
        if cls is None:
            raise ValueError(f"class {cls_name!r} not found in {path}")
        return cls()

    if name_or_path in REGISTRY:
        mod_path, _, cls_name = REGISTRY[name_or_path].partition(":")
        mod = importlib.import_module(mod_path)
        return getattr(mod, cls_name)()

    available = ", ".join(sorted(REGISTRY))
    raise ValueError(f"unknown protocol {name_or_path!r}. Available: {available}")

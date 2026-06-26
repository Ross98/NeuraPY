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

    Cross-platform: Windows paths like 'C:\\path\\file.py:Class' are split
    on the LAST colon (first colon is the drive letter separator).

    Raises ValueError on miss (with available names listed).
    """
    if not name_or_path:
        available = ", ".join(sorted(REGISTRY)) or "(none registered)"
        raise ValueError(f"--protocol required. Available: {available}")

    # Fast path: registry lookup
    if name_or_path in REGISTRY:
        mod_path, cls_name = REGISTRY[name_or_path].rsplit(":", 1)
        mod = importlib.import_module(mod_path)
        return getattr(mod, cls_name)()

    # Slow path: file:Class form. Split on LAST colon (handles Windows drive letters).
    if ":" in name_or_path:
        idx = name_or_path.rfind(":")
        path_str = name_or_path[:idx]
        cls_name = name_or_path[idx + 1:]
        # Must look like a real path: ends with .py, or has a path separator
        # (forward slash on POSIX, backslash on Windows).
        if path_str.endswith(".py") or "/" in path_str or "\\" in path_str:
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

    available = ", ".join(sorted(REGISTRY))
    raise ValueError(f"unknown protocol {name_or_path!r}. Available: {available}")

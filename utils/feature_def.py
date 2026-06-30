from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import yaml
import pathlib


@dataclass(frozen=True)
class FeatureKey:
    name: str
    timeframe: Optional[str] = None
    params: Tuple[Tuple[str, Any], ...] = field(default_factory=tuple)
    source: str = "close"
    version: str = "1.0"

    def canonical(self) -> Tuple:
        return (self.name, self.timeframe, self.params, self.source, self.version)


@dataclass
class FeatureDescriptor:
    key: FeatureKey
    description: str = ""
    owner: str = ""
    dependencies: List[FeatureKey] = field(default_factory=list)
    provenance: Dict[str, Any] = field(default_factory=dict)


def _make_feature_key(d: Dict[str, Any]) -> FeatureKey:
    params = d.get("params") or {}
    # canonical order for params
    params_tuple = tuple(sorted(tuple(params.items())))
    return FeatureKey(
        name=d["name"],
        timeframe=d.get("timeframe"),
        params=params_tuple,
        source=d.get("source", "close"),
        version=str(d.get("version", "1.0")),
    )


def load_feature_registry(path: str = "storage/feature_registry.yaml") -> Dict[Tuple, FeatureDescriptor]:
    p = pathlib.Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Feature registry not found: {path}")
    raw = yaml.safe_load(p.read_text()) or {}
    out: Dict[Tuple, FeatureDescriptor] = {}
    for feature_name, versions in raw.items():
        for ver, meta in versions.items():
            entry = {
                "name": feature_name,
                "timeframe": meta.get("timeframe"),
                "params": meta.get("params", {}),
                "source": meta.get("source", "close"),
                "version": ver,
            }
            key = _make_feature_key(entry)
            deps = []
            for dep in meta.get("dependencies", []):
                # dep may be simple like "ATR14" or a dict
                if isinstance(dep, str):
                    deps.append(FeatureKey(name=dep))
                elif isinstance(dep, dict):
                    deps.append(_make_feature_key(dep))
            desc = FeatureDescriptor(
                key=key,
                description=meta.get("description", ""),
                owner=meta.get("owner", ""),
                dependencies=deps,
                provenance=meta.get("provenance", {}),
            )
            out[key.canonical()] = desc
    return out

"""
V-Sekai post-export hook for Blender's USD exporter.

Copyright 2026 The openusd-fabric authors.
SPDX-License-Identifier: MIT

Blender's native USD exporter does not apply custom API schemas, so this
hook runs after the export to stamp the V-Sekai schemas onto the right
prims and write back the v_sekai:* attributes that downstream tools
(idtx-flow for Godot, the schema mapper for Unity) consume.

Two entry points:

* CLI form, headless and CI-friendly:
      blender --background --python blender/post_export_hook.py -- \
          --in path/to/exported.usda
  Arguments past `--` are read via sys.argv after Blender strips its own
  flags. The script edits the file in place by default; pass --out to
  write a separate stage.

* In-process form, called from a Python addon's `wm.usd_export`
  post-handler:
      from openusd_fabric_blender import post_export_hook
      post_export_hook.apply_v_sekai_schemas(stage)

Plugin discovery: USD looks at PXR_PLUGINPATH_NAME for codeless schemas.
This script auto-sets it to ../schema relative to its own location if the
variable is unset, so the standalone CLI form works without any wrapper.

Phase 1 scope (CHI-251):
* Apply VSekaiMToonAPI to material prims flagged as MToon.
* Apply VSekaiSpringBoneAPI to joints flagged as springbone roots.
* Apply VSekaiSpringBoneColliderAPI to joints flagged as colliders.

Detection of "flagged as ..." is currently a stub — the rules live with
the Blender side of the V-Sekai authoring pipeline and will fill in as
the asset conventions stabilise. The plumbing here is what gets reused;
the predicates are what change.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable

try:
    from pxr import Sdf, Usd, UsdGeom, UsdShade
except ImportError as exc:
    raise SystemExit(
        "pxr (OpenUSD Python bindings) is not importable. Run this script "
        "from inside Blender (blender --background --python ...) or from a "
        "Python environment where `pip install usd-core` succeeded."
    ) from exc


SCHEMA_DIR_DEFAULT = (Path(__file__).resolve().parent.parent / "schema")


def ensure_plugin_path(schema_dir: Path = SCHEMA_DIR_DEFAULT) -> None:
    """Prepend the V-Sekai schema directory to PXR_PLUGINPATH_NAME if absent.

    USD only consults PXR_PLUGINPATH_NAME at plugin-registry warmup, which
    happens on the first UsdStage::Open or Usd.Stage.Open call. This must
    run before any stage is opened in the process, hence module top of
    every callsite calling into apply_v_sekai_schemas().
    """
    schema_dir = schema_dir.resolve()
    if not (schema_dir / "plugInfo.json").exists():
        raise FileNotFoundError(
            f"V-Sekai plugInfo.json not found at {schema_dir}. "
            "Pass --schema-dir or set PXR_PLUGINPATH_NAME manually."
        )
    current = os.environ.get("PXR_PLUGINPATH_NAME", "")
    parts = [p for p in current.split(os.pathsep) if p]
    if str(schema_dir) in parts:
        return
    parts.insert(0, str(schema_dir))
    os.environ["PXR_PLUGINPATH_NAME"] = os.pathsep.join(parts)


def _iter_material_prims(stage: Usd.Stage) -> Iterable[Usd.Prim]:
    for prim in stage.Traverse():
        if prim.IsA(UsdShade.Material):
            yield prim


def _iter_joint_prims(stage: Usd.Stage) -> Iterable[Usd.Prim]:
    # USD encodes skeletons via UsdSkel. Joints are entries in the Skeleton
    # prim's `joints` attribute. For springbones/colliders we surface each
    # joint as a virtual prim path so consumers can attach API schemas to
    # the canonical joint paths. For Phase 1 the walker yields any prim
    # under a Skeleton — concrete VRM-bone mapping fills in later.
    for prim in stage.Traverse():
        if prim.GetTypeName() == "Skeleton" or prim.GetTypeName() == "SkelRoot":
            yield prim


def _is_mtoon_material(prim: Usd.Prim) -> bool:
    """Predicate: should this material carry VSekaiMToonAPI?

    Stub. Real rules belong with the V-Sekai material-naming convention;
    expected to inspect surface-shader inputs, custom data, or a
    purpose-built `v_sekai:mtoon_marker` flag emitted by the Blender-side
    authoring add-on.
    """
    return False


def _is_springbone_root(prim: Usd.Prim) -> bool:
    """Predicate: should this joint carry VSekaiSpringBoneAPI?"""
    return False


def _is_springbone_collider(prim: Usd.Prim) -> bool:
    """Predicate: should this joint carry VSekaiSpringBoneColliderAPI?"""
    return False


def _apply_api(prim: Usd.Prim, api_name: str) -> None:
    """Apply a single-apply API schema to a prim if not already applied.

    Uses the generic ApplyAPI(TfType) path because the codeless schema
    has no generated C++ class to call ApplyAPI(VSekaiMToonAPI) on.
    """
    if prim.HasAPI(api_name):
        return
    prim.ApplyAPI(api_name)


def apply_v_sekai_schemas(stage: Usd.Stage) -> dict[str, int]:
    """Apply V-Sekai API schemas across the stage. Returns counts per API."""
    counts = {
        "VSekaiMToonAPI": 0,
        "VSekaiSpringBoneAPI": 0,
        "VSekaiSpringBoneColliderAPI": 0,
    }

    for prim in _iter_material_prims(stage):
        if _is_mtoon_material(prim):
            _apply_api(prim, "VSekaiMToonAPI")
            counts["VSekaiMToonAPI"] += 1

    for prim in _iter_joint_prims(stage):
        if _is_springbone_root(prim):
            _apply_api(prim, "VSekaiSpringBoneAPI")
            counts["VSekaiSpringBoneAPI"] += 1
        if _is_springbone_collider(prim):
            _apply_api(prim, "VSekaiSpringBoneColliderAPI")
            counts["VSekaiSpringBoneColliderAPI"] += 1

    return counts


def _user_args() -> list[str]:
    """Return CLI args trailing Blender's own flag block.

    Blender forwards anything after a literal `--` to the script. Inside a
    plain `python` invocation there is no `--` separator, so we fall back
    to sys.argv[1:].
    """
    if "--" in sys.argv:
        return sys.argv[sys.argv.index("--") + 1:]
    return sys.argv[1:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--in", dest="input_path", required=True,
                        help="Path to the .usda stage exported by Blender.")
    parser.add_argument("--out", dest="output_path", default=None,
                        help="Where to write the stamped stage. "
                             "Defaults to overwriting the input.")
    parser.add_argument("--schema-dir", dest="schema_dir", default=None,
                        help="Directory containing plugInfo.json. "
                             "Defaults to ../schema next to this script.")
    args = parser.parse_args(_user_args())

    schema_dir = Path(args.schema_dir) if args.schema_dir else SCHEMA_DIR_DEFAULT
    ensure_plugin_path(schema_dir)

    stage = Usd.Stage.Open(args.input_path)
    if stage is None:
        print(f"error: could not open {args.input_path}", file=sys.stderr)
        return 1

    counts = apply_v_sekai_schemas(stage)

    out_path = args.output_path or args.input_path
    if out_path == args.input_path:
        stage.GetRootLayer().Save()
    else:
        stage.GetRootLayer().Export(out_path)

    summary = ", ".join(f"{name}={n}" for name, n in counts.items())
    print(f"openusd-fabric: applied V-Sekai schemas → {summary} → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

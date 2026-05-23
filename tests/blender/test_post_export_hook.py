# Copyright 2026 The openusd-fabric authors.
# SPDX-License-Identifier: MIT
#
# Tests for blender/post_export_hook.py. We don't need a real Blender
# install — the hook reads userProperties:* attributes from a .usda
# and stamps V-Sekai API schemas + their attributes. The fixture below
# builds a synthetic stage that mimics what Blender would produce after
# the V-Sekai pre-export RNA-to-id_property mirror has run.

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make blender/post_export_hook.py importable as a module.
_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "blender"))

try:
    from pxr import Sdf, Usd, UsdGeom, UsdShade
except ImportError:
    pytest.skip("pxr (OpenUSD Python bindings) not available", allow_module_level=True)

import post_export_hook  # noqa: E402


@pytest.fixture(autouse=True, scope="module")
def _register_schema():
    """USD only consults PXR_PLUGINPATH_NAME at plugin-registry warmup,
    which happens on the first UsdStage::Open. Set it before any test
    in this module touches a stage.
    """
    post_export_hook.ensure_plugin_path()


def _make_stage(path: Path) -> Usd.Stage:
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)

    # Avatar root.
    root = UsdGeom.Xform.Define(stage, "/Avatar")
    stage.SetDefaultPrim(root.GetPrim())

    # A material that the pre-export mirror has marked as MToon, with
    # representative MToon factor values stored under userProperties:*.
    mat_path = "/Avatar/Materials/Face_MToon"
    mat = UsdShade.Material.Define(stage, mat_path)
    mp = mat.GetPrim()
    mp.CreateAttribute("userProperties:v_sekai:mtoon", Sdf.ValueTypeNames.Bool).Set(True)
    mp.CreateAttribute("userProperties:v_sekai:mtoon:shadeColorFactor",
                       Sdf.ValueTypeNames.Color3f).Set((0.5, 0.5, 0.5))
    mp.CreateAttribute("userProperties:v_sekai:mtoon:shadingToonyFactor",
                       Sdf.ValueTypeNames.Float).Set(0.9)
    mp.CreateAttribute("userProperties:v_sekai:mtoon:outlineWidthMode",
                       Sdf.ValueTypeNames.Token).Set("worldCoordinates")

    # A material that is NOT marked — should be left alone.
    plain_mat = UsdShade.Material.Define(stage, "/Avatar/Materials/Body_PBR")

    # A springbone-root Xform sibling of the Skeleton.
    sb_root = UsdGeom.Xform.Define(stage, "/Avatar/SpringBones/Hair_L_0")
    sbp = sb_root.GetPrim()
    sbp.CreateAttribute("userProperties:v_sekai:springBone",
                        Sdf.ValueTypeNames.Bool).Set(True)
    sbp.CreateAttribute("userProperties:v_sekai:springBone:stiffness",
                        Sdf.ValueTypeNames.Float).Set(1.0)
    sbp.CreateAttribute("userProperties:v_sekai:springBone:drag",
                        Sdf.ValueTypeNames.Float).Set(0.4)
    sbp.CreateAttribute("userProperties:v_sekai:springBone:hitRadius",
                        Sdf.ValueTypeNames.Float).Set(0.02)

    # A collider Xform sibling.
    col = UsdGeom.Xform.Define(stage, "/Avatar/SpringBones/Head_Collider")
    cp = col.GetPrim()
    cp.CreateAttribute("userProperties:v_sekai:springBoneCollider",
                       Sdf.ValueTypeNames.Bool).Set(True)
    cp.CreateAttribute("userProperties:v_sekai:springBone:collider:shape",
                       Sdf.ValueTypeNames.Token).Set("sphere")
    cp.CreateAttribute("userProperties:v_sekai:springBone:collider:radius",
                       Sdf.ValueTypeNames.Float).Set(0.05)

    stage.GetRootLayer().Save()
    return stage


def test_mtoon_api_stamped(tmp_path: Path) -> None:
    p = tmp_path / "stage.usda"
    _make_stage(p)
    stage = Usd.Stage.Open(str(p))
    counts = post_export_hook.apply_v_sekai_schemas(stage)

    mat_prim = stage.GetPrimAtPath("/Avatar/Materials/Face_MToon")
    assert mat_prim.HasAPI("VSekaiMToonAPI"), "VSekaiMToonAPI not applied to marked material"
    plain_prim = stage.GetPrimAtPath("/Avatar/Materials/Body_PBR")
    assert not plain_prim.HasAPI("VSekaiMToonAPI"), "VSekaiMToonAPI applied to unmarked material"
    assert counts["VSekaiMToonAPI"] == 1, f"unexpected MToon count: {counts}"


def test_mtoon_attributes_stamped(tmp_path: Path) -> None:
    p = tmp_path / "stage.usda"
    _make_stage(p)
    stage = Usd.Stage.Open(str(p))
    post_export_hook.apply_v_sekai_schemas(stage)

    mat_prim = stage.GetPrimAtPath("/Avatar/Materials/Face_MToon")
    shade = mat_prim.GetAttribute("v_sekai:mtoon:shadeColorFactor")
    assert shade and shade.IsValid(), "v_sekai:mtoon:shadeColorFactor missing"
    sx, sy, sz = shade.Get()
    assert (sx, sy, sz) == (0.5, 0.5, 0.5), f"shadeColorFactor value drifted: {shade.Get()}"

    toony = mat_prim.GetAttribute("v_sekai:mtoon:shadingToonyFactor")
    assert toony and toony.Get() == pytest.approx(0.9)

    outline = mat_prim.GetAttribute("v_sekai:mtoon:outlineWidthMode")
    assert outline and str(outline.Get()) == "worldCoordinates"


def test_springbone_api_stamped(tmp_path: Path) -> None:
    p = tmp_path / "stage.usda"
    _make_stage(p)
    stage = Usd.Stage.Open(str(p))
    counts = post_export_hook.apply_v_sekai_schemas(stage)

    sb = stage.GetPrimAtPath("/Avatar/SpringBones/Hair_L_0")
    assert sb.HasAPI("VSekaiSpringBoneAPI")
    assert sb.GetAttribute("v_sekai:springBone:stiffness").Get() == pytest.approx(1.0)
    assert sb.GetAttribute("v_sekai:springBone:drag").Get() == pytest.approx(0.4)
    assert sb.GetAttribute("v_sekai:springBone:hitRadius").Get() == pytest.approx(0.02)
    assert counts["VSekaiSpringBoneAPI"] == 1


def test_collider_api_stamped(tmp_path: Path) -> None:
    p = tmp_path / "stage.usda"
    _make_stage(p)
    stage = Usd.Stage.Open(str(p))
    counts = post_export_hook.apply_v_sekai_schemas(stage)

    col = stage.GetPrimAtPath("/Avatar/SpringBones/Head_Collider")
    assert col.HasAPI("VSekaiSpringBoneColliderAPI")
    assert str(col.GetAttribute("v_sekai:springBone:collider:shape").Get()) == "sphere"
    assert col.GetAttribute("v_sekai:springBone:collider:radius").Get() == pytest.approx(0.05)
    assert counts["VSekaiSpringBoneColliderAPI"] == 1


def test_idempotent_reapply(tmp_path: Path) -> None:
    """Running the hook twice must not change the result."""
    p = tmp_path / "stage.usda"
    _make_stage(p)
    stage = Usd.Stage.Open(str(p))
    counts_a = post_export_hook.apply_v_sekai_schemas(stage)
    counts_b = post_export_hook.apply_v_sekai_schemas(stage)
    assert counts_a == counts_b, "hook is not idempotent"


def test_falsy_marker_skips(tmp_path: Path) -> None:
    """A marker attribute set to False / 0 / '' must NOT trigger application."""
    p = tmp_path / "stage.usda"
    _make_stage(p)
    stage = Usd.Stage.Open(str(p))
    # Mark the plain material with mtoon=False — must NOT pick it up.
    plain = stage.GetPrimAtPath("/Avatar/Materials/Body_PBR")
    plain.CreateAttribute("userProperties:v_sekai:mtoon",
                          Sdf.ValueTypeNames.Bool).Set(False)
    counts = post_export_hook.apply_v_sekai_schemas(stage)
    assert not plain.HasAPI("VSekaiMToonAPI")
    assert counts["VSekaiMToonAPI"] == 1, "False marker should not trigger application"

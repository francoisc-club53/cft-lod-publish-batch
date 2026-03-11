"""
Clean rewrite scaffold for LOD publish batch.

TODO goals:
1) Keep behavior the same as current production script.
2) Make each step testable and easy to reason about.
3) Fail safely per asset, continue batch.
"""

import datetime
import json
import os
import re
from os import rename

import maya.cmds as cmds
from cft_sandbox import import_utils
from cft_sandbox import publish_utils as pub_util
from cft_sandbox import texture_utils

# Constants/config
LOD_KEYS = ["lod0", "lod1", "lod2"]
REQUIRED_CONFIG_FIELDS = ["game", "level", "asset_map_path"]


def get_current_dir():
    try:
        return os.path.dirname(__file__)
    except NameError:
        # Interactive fallback
        return r"E:\work\c53\test\cft-lod-publish-batch\scripts"


CURRENT_DIR = get_current_dir()
CONFIG_PATH = os.path.join(CURRENT_DIR, "global_config.json")
LOG_PATH = os.path.join(CURRENT_DIR, "publish.log")
REPORT_PATH = os.path.join(CURRENT_DIR, "publish_report.json")


def load_json(path):
    with open(path, "r") as f:
        return json.load(f)


def load_config(config_path=CONFIG_PATH):
    return load_json(config_path)


def validate_config(config):
    missing = [key for key in REQUIRED_CONFIG_FIELDS if not config.get(key)]
    if missing:
        raise ValueError(f"Missing required config fields: {', '.join(missing)}")

    asset_map_path = config.get("asset_map_path")
    if not os.path.isfile(asset_map_path):
        raise ValueError(f"asset_map_path does not exist: {asset_map_path}")

    return True

# TODO: logging/reporting
# - init publish.log
# - unified log helpers: info/warn/error with item index
# - in-memory run report model
# - write publish_report.json at end




def iter_asset_map_items(asset_map):
    """
    Yield (item_name, item_data) for both object and array asset maps.
    """
    if isinstance(asset_map, dict):
        for name, data in asset_map.items():
            yield name, data
        return

    if isinstance(asset_map, list):
        for idx, data in enumerate(asset_map):
            yield f"item_{idx}", data
        return

    raise ValueError(f"Unsupported asset_map type: {type(asset_map).__name__}")


def purge_non_default_shaders():
    """
    Remove scene shader/file utility nodes that can cause stale texture reuse.
    Keep Maya defaults intact.
    """
    protected = {"lambert1", "particleCloud1", "standardSurface1", "initialShadingGroup", "initialParticleSE"}
    node_types = [
        "shadingEngine",
        "file",
        "place2dTexture",
        "lambert",
        "blinn",
        "phong",
        "aiStandardSurface",
        "standardSurface",
    ]
    for t in node_types:
        nodes = cmds.ls(type=t) or []
        for n in nodes:
            if n in protected:
                continue
            try:
                if cmds.objExists(n):
                    cmds.delete(n)
            except Exception:
                pass


def choose_best_shader_key(mesh_name, shader_map):
    """
    Pick the most likely shader prefix for a mesh name from construct_shaders_from_folder return data.
    """
    if not shader_map:
        return None
    keys = list(shader_map.keys())
    mesh_short = (mesh_name or "").split("|")[-1]
    mesh_lower = mesh_short.lower()

    for k in keys:
        if k.lower() == mesh_lower:
            return k
    for k in keys:
        if mesh_lower.startswith(k.lower()) or k.lower().startswith(mesh_lower):
            return k

    lod_match = re.search(r"_lod\d+$", mesh_lower)
    if lod_match:
        lod_token = lod_match.group(0)
        lod_keys = [k for k in keys if lod_token in k.lower()]
        if len(lod_keys) == 1:
            return lod_keys[0]
        if lod_keys:
            lod_keys.sort(key=lambda x: abs(len(x) - len(mesh_short)))
            return lod_keys[0]

    keys.sort(key=lambda x: abs(len(x) - len(mesh_short)))
    return keys[0] if keys else None


def assign_best_shader_from_folder(mesh_name, folder_path):
    """
    Build shader map from folder, then assign the best matching shader to mesh.
    """
    shader_map = texture_utils.construct_shaders_from_folder(folder_path, apply_to_selected=False)
    best_key = choose_best_shader_key(mesh_name, shader_map)
    if not best_key:
        print(f"  - warn: no shader candidates found for {mesh_name}")
        return False

    shader_name = shader_map.get(best_key)
    if not shader_name or not cmds.objExists(shader_name):
        print(f"  - warn: shader missing for key {best_key}")
        return False

    shading_groups = cmds.listConnections(f"{shader_name}.outColor", type="shadingEngine") or []
    if not shading_groups:
        print(f"  - warn: no shading group for shader {shader_name}")
        return False

    cmds.sets(mesh_name, edit=True, forceElement=shading_groups[0])
    print(f"    - shader assigned: {mesh_name} -> {shader_name}")
    return True

# TODO: path/meta helpers
# - normalize path helper
# - resolve relative vs absolute paths
# - read primary .meta from asset-map item
# - extract asset/variant from meta file (source_mesh/output_mesh/texture_path)
# - define strict fallback policy (if any)

# TODO: maya scene helpers
# - open new scene per item
# - import base asset with usd->fbx fallback
# - ensure mayaUsdPlugin loaded when needed
# - cleanup transforms (soft edges, delete history, freeze)
# - remove base shading nodes before LOD material build
# - delete base node + empty parents after LOD import

# TODO: LOD import flow
# - load lod0/lod1/lod2 meta files
# - resolve and import OBJ meshes
# - rename to <prefix>_LOD0..2
# - transfer custom attrs from base to LOD0
# - run prep-from-selection on LOD0
# - apply materials from texture folder if present

# TODO: publish flow
# - select imported LOD nodes
# - publish with pub_util.publish_asset(...)
# - capture publish placement + publish directory
# - handle publish failures without stopping batch

# TODO: orchestration
# - process_item(item_index, item_data) -> item result
# - main() loops all items and writes summary
# - __main__ entrypoint


def main():
    config = load_config()
    validate_config(config)

    game = config.get("game")
    level = config.get("level")
    allowed_lod_keys = set(config.get("lod_keys", ["lod0", "lod1", "lod2", "lod3", "lod4", "lod5"]))
    asset_map_path = config["asset_map_path"]
    asset_map = load_json(asset_map_path)
    asset_map_dir = os.path.dirname(asset_map_path)

    print(f"[CONFIG] game={game} level={level}")

    for item_name, item_data in iter_asset_map_items(asset_map):
        # Isolate each asset to avoid stale materials/shading networks from prior items.
        cmds.file(new=True, force=True)
        # TODO: implement per-item processing here.
        print(f"[ITEM] {item_name}")
        if isinstance(item_data, dict):
            asset = item_data.get("asset")
            variant = item_data.get("variant")
            
            print(f"  - asset: {asset}")
            print(f"  - variant: {variant}")
            
            # import base 
            base_name = import_utils.do_general_import(game, level, asset, variant, "usd", versioned_dir=None)
            base_name = cmds.ls(base_name[0], shortNames=True)[0] if isinstance(base_name, (list, tuple)) and base_name else base_name
            base_name_new = cmds.rename(base_name, f"{base_name}_base")
            lod0_name =  None
            lod0_texture_path = None
            lod_publish_list = []
            
            print(f"  - base: {base_name_new}")
            if not base_name:
                raise RuntimeError(f"do_general_import returned {base_name!r}")
            #process lods
            for field_name, field_value in item_data.items():
                if field_name not in allowed_lod_keys:
                    continue
                print(f"  - {field_name}: {field_value}")
                
                if isinstance(field_value, str) and field_value.lower().endswith(".meta"):
                    meta_path = field_value if os.path.isabs(field_value) else os.path.join(asset_map_dir, field_value)
                    
                    if os.path.isfile(meta_path):
                        meta_data = load_json(meta_path)
                        
                        if isinstance(meta_data, dict):
                            
                            #import lods
                            look_variant = meta_data.get("look_variant")
                            imported_nodes = cmds.file(
                                meta_data["output_mesh"],
                                i=True,
                                type="OBJ",
                                options="mo=1",
                                pr=True,
                                returnNewNodes=True,
                            )
                            
                            #filter import to transform nodes
                            imported_transforms = cmds.ls(imported_nodes, long=True, type="transform") if imported_nodes else []
                            if not imported_transforms:
                                print(f"  - skipped {field_name}: no imported transform nodes")
                                continue
                            
                            lod_name_temp = imported_transforms[0]
                            lod_name = f"{variant}_{look_variant}_{field_name.upper()}"
                            lod_name = cmds.rename(lod_name_temp, lod_name)
                            lod_long_name = (cmds.ls(lod_name, long=True) or [lod_name])[0]
                            print(f"    - {field_name}: {lod_long_name}")
                            
                            # special loging for lod0
                            if field_name == "lod0":
                                # add custom attar
                                pub_util.transfer_custom_attrs(base_name_new, lod_long_name)
                                parent_nodes = cmds.listRelatives(base_name_new, parent=True, fullPath=True, type="transform") or []
                                if parent_nodes:
                                    cmds.delete(parent_nodes)
                                lod0_name = lod_name
                                lod0_texture_path = meta_data.get("texture_path")
                              
                            # add to publish list
                            lod_publish_list.append(lod_long_name)
                            
                            # for sub_key, sub_value in meta_data.items():
                            #     print(f"    - {sub_key}: {sub_value}")
            
            # publish Todo I think i need to set the default lod and run prep from selection
            if lod0_name and lod_publish_list:
                # Build one material set from LOD0 texture folder and assign it to all LOD meshes.
                if isinstance(lod0_texture_path, str) and lod0_texture_path:
                    purge_non_default_shaders()
                    shader_map = texture_utils.construct_shaders_from_folder(lod0_texture_path, apply_to_selected=False)
                    shared_key = choose_best_shader_key(lod0_name, shader_map)
                    shared_shader = shader_map.get(shared_key) if shared_key else None
                    if shared_shader and cmds.objExists(shared_shader):
                        shared_sg_list = cmds.listConnections(f"{shared_shader}.outColor", type="shadingEngine") or []
                        if shared_sg_list:
                            shared_sg = shared_sg_list[0]
                            for lod_mesh in lod_publish_list:
                                try:
                                    texture_utils.remove_shading_nodes_from_mesh(lod_mesh)
                                except Exception as e:
                                    print(f"  - warn: failed clearing shading on {lod_mesh}: {e}")
                                cmds.sets(lod_mesh, edit=True, forceElement=shared_sg)
                                print(f"    - shared shader assigned: {lod_mesh} -> {shared_shader}")
                        else:
                            print(f"  - skipped shared shader assign: no shading group on {shared_shader}")
                    else:
                        print("  - skipped shared shader assign: no shader built from lod0 texture path")
                else:
                    print("  - skipped shared shader assign: missing/invalid lod0 texture_path")

                cmds.select(lod_publish_list, replace=True)
                publish_dir = pub_util.publish_asset(
                    game,
                    level,
                    lod0_name,
                    data_types=["fbx", "usd"],
                    collision_type="auto",
                )
                print(publish_dir)
            else:
                print("  - skipped publish: missing lod0 or lod selection")
            cmds.file(new=True, force=True)
             

if __name__ == "__main__":
    main()

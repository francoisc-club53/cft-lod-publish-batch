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
            base_name_new = cmds.rename(base_name, {base_name + '_base'})
            lod0_name =  None
            
            print(f"  - base: {base_name_new}")
            if not base_name:
                raise RuntimeError(f"do_general_import returned {base_name!r}")
            #process lods
            for field_name, field_value in item_data.items():
                if field_name not in allowed_lod_keys:
                    continue
                print(f"  - {field_name}: {field_value}")
                #imported_nodes = cmds.file(lod0_output_mesh_path,i=True,type="OBJ",options="mo=1",pr=True,returnNewNodes=True)
                if isinstance(field_value, str) and field_value.lower().endswith(".meta"):
                    meta_path = field_value if os.path.isabs(field_value) else os.path.join(asset_map_dir, field_value)
                    if os.path.isfile(meta_path):
                        meta_data = load_json(meta_path)
                        
                        if isinstance(meta_data, dict):
                            
                            #import lods
                            look_variant = meta_data.get("look_variant")
                            lod_nodes = cmds.file(meta_data["output_mesh"],i=True,type="OBJ",options="mo=1",pr=True,returnNewNodes=True)
                            lod_name_temp = cmds.ls(lod_nodes[0], shortNames=True)[0] if isinstance(lod_nodes, (list, tuple)) and lod_nodes else lod_nodes
                            lod_name = f"{variant}_{look_variant}_{field_name.upper()}"
                            cmds.rename(lod_name_temp, lod_name)
                            if field_name == "lod0":
                                # add custom attar
                                pub_util.transfer_custom_attrs(base_name_new, lod_name)
                                cmds.delete( cmds.listRelatives(base_name_new, parent=True, fullPath=True, type="transform"))
                                lod0_name = lod_name
                            
                            # texture
                            cmds.select(lod_name, r=True)
                            texture_utils.construct_shaders_from_folder(meta_data["texture_path"], True)
                            
                            for sub_key, sub_value in meta_data.items():
                                print(f"    - {sub_key}: {sub_value}")
            
            # publish Todo I think i need to set the default lod and run prep from selection
            cmds.select(lod0_name, replace=True)
            publish_dir = pub_util.publish_asset(
                game,
                level,
                lod0_name,
                data_types=["fbx", "usd"],
                collision_type="auto",
            )
            print(publish_dir)

if __name__ == "__main__":
    main()

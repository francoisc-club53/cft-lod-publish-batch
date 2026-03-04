import os
import json
import re
import datetime
import maya.cmds as cmds
from cft_sandbox import import_utils
from cft_sandbox import publish_utils as pub_util
from cft_sandbox import texture_utils

# 1. Load Global Configuration
try:
    current_dir = os.path.dirname(__file__)
except NameError: # This can happen if the script is run interactively
    current_dir = r"E:\work\c53\test\cft-lod-publish-batch\scripts"

config_path = os.path.join(current_dir, 'global_config.json')
with open(config_path, 'r') as f:
    config = json.load(f)

game = config.get("game")
level = config.get("level")
asset_map_path = config.get("asset_map_path")
import_type = config.get("import_type", "usd")
path_pattern = re.compile(config.get("path_pattern", ""), re.IGNORECASE)

# Initialize Error Log File
log_file_path = os.path.join(current_dir, 'publish.log')
publish_report_json_path = os.path.join(current_dir, 'publish_report.json')
with open(log_file_path, 'w') as log_f:
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_f.write(f"--- Import Error Log: {timestamp} ---\n\n")
    startup_log_path = os.path.normpath(os.path.abspath(log_file_path)).replace("\\", "/")
    log_f.write(f"[INFO] Log file path: {startup_log_path}\n")

def log_error(msg):
    with open(log_file_path, 'a') as log_f:
        log_f.write(msg + "\n")


def log_line(msg):
    print(msg)
    with open(log_file_path, 'a') as log_f:
        log_f.write(msg + "\n")


def norm_path(path_value):
    if path_value is None:
        return None
    return os.path.normpath(str(path_value)).replace("\\", "/")


def log_step(index, stage, msg):
    log_line(f"[{index:03d}] {stage}: {msg}")


def log_warn(index, stage, msg):
    line = f"[{index:03d}] WARN {stage}: {msg}"
    log_line(line)


def log_fail(index, stage, msg):
    line = f"[{index:03d}] ERROR {stage}: {msg}"
    log_line(line)


def write_item_report(index, asset_tag, status, reasons, paths):
    log_line(f"[{index:03d}] RESULT: {status} {asset_tag}")
    log_line(f"[{index:03d}] PATH source: {norm_path(paths.get('source_path'))}")
    log_line(f"[{index:03d}] PATH base_meta: {norm_path(paths.get('base_meta_path'))}")
    log_line(f"[{index:03d}] PATH texture_dir: {norm_path(paths.get('tex_dir'))}")
    log_line(f"[{index:03d}] PATH publish_placement: {paths.get('publish_placement')}")
    log_line(f"[{index:03d}] PATH publish_dir: {norm_path(paths.get('publish_dir'))}")

    lod_meta_paths = paths.get("lod_meta_paths", {})
    for lod_key in ["lod0", "lod1", "lod2"]:
        log_line(f"[{index:03d}] PATH {lod_key}_meta: {norm_path(lod_meta_paths.get(lod_key))}")

    lod_mesh_paths = paths.get("lod_mesh_paths", {})
    for lod_key in ["lod0", "lod1", "lod2"]:
        log_line(f"[{index:03d}] PATH {lod_key}_mesh: {norm_path(lod_mesh_paths.get(lod_key))}")

    if reasons:
        for reason in reasons:
            log_line(f"[{index:03d}] WHY: {reason}")
    log_line(f"[{index:03d}] {'-' * 70}")


def delete_node_and_empty_parents(node):
    if not node or not cmds.objExists(node):
        return []

    deleted_nodes = []
    parent_chain = []
    current = node
    while True:
        parents = cmds.listRelatives(current, parent=True, fullPath=True) or []
        if not parents:
            break
        parent = parents[0]
        parent_chain.append(parent)
        current = parent

    cmds.delete(node)
    deleted_nodes.append(node)

    for parent in parent_chain:
        if not cmds.objExists(parent):
            continue
        children = cmds.listRelatives(parent, children=True, fullPath=True) or []
        if children:
            break
        cmds.delete(parent)
        deleted_nodes.append(parent)

    return deleted_nodes


def run_general_import_with_guards(game, level, asset, variant, preferred_type):
    import_order = [preferred_type]
    if preferred_type == "usd":
        import_order.append("fbx")

    errors = []
    for file_type in import_order:
        try:
            if file_type == "usd":
                if not cmds.pluginInfo("mayaUsdPlugin", query=True, loaded=True):
                    cmds.loadPlugin("mayaUsdPlugin")

            result = import_utils.do_general_import(game, level, asset, variant, file_type, versioned_dir=None)
            if not result:
                raise RuntimeError(f"do_general_import returned {result!r}")
            return result, file_type
        except Exception as e:
            errors.append(f"{file_type}: {e}")

    raise RuntimeError("All import attempts failed -> " + " | ".join(errors))


def get_texture_folder_from_base_meta(data_item, map_base_dir):
    base_meta_rel = None
    if isinstance(data_item, dict):
        base_meta_rel = data_item.get("hires") or data_item.get("source_mesh")
    elif isinstance(data_item, str):
        base_meta_rel = data_item

    if not base_meta_rel:
        return None

    meta_path = base_meta_rel if os.path.isabs(base_meta_rel) else os.path.join(map_base_dir, base_meta_rel)
    meta_path = os.path.normpath(meta_path)
    if not os.path.isfile(meta_path):
        return None

    try:
        with open(meta_path, "r") as f:
            meta_data = json.load(f)
    except Exception:
        return None

    texture_path = meta_data.get("texture_path")
    if not isinstance(texture_path, str) or not texture_path:
        return None

    texture_folder = texture_path if os.path.isabs(texture_path) else os.path.join(os.path.dirname(meta_path), texture_path)
    texture_folder = os.path.normpath(texture_folder)
    png_folder = os.path.join(texture_folder, "png")
    # Prefer the png subfolder because construct_shaders_from_folder only scans direct .png files.
    if os.path.isdir(png_folder):
        return png_folder
    if os.path.isdir(texture_folder):
        return texture_folder
    return None


def run_prep_from_selection_on_node(node_name, asset_name=None, variant_name=None):
    if not node_name or not cmds.objExists(node_name):
        return False

    short_name = node_name.split("|")[-1]
    cmds.select(short_name, replace=True)
    custom_attrs = pub_util.get_custom_attrs_from_node(short_name)
    if custom_attrs is None:
        pub_util.add_default_asset_attrs(short_name)

    if asset_name:
        cmds.setAttr(f"{short_name}.assetType", asset_name, type="string")
    if variant_name:
        cmds.setAttr(f"{short_name}.variant", variant_name, type="string")
    cmds.setAttr(f"{short_name}.defaultVariant", short_name, type="string")
    return True


# 2. Load Asset Map
with open(asset_map_path, 'r') as f:
    asset_map = json.load(f)
asset_map_dir = os.path.dirname(asset_map_path)

# 3. Process Asset Map
items = asset_map if isinstance(asset_map, list) else list(asset_map.values())
log_line(f"[INFO] Found {len(items)} items to process.")
run_report = []

for index, item in enumerate(items):
    cmds.file(new=True, force=True) # Start a new scene for each asset
    lod_name_prefix = None
    base_attr_source = None
    base_delete_target = None
    tex_dir = None
    lod_nodes = []
    lod0_node = None
    item_reasons = []
    publish_succeeded = False
    base_meta_rel = None
    report_paths = {
        "source_path": None,
        "base_meta_path": None,
        "tex_dir": None,
        "publish_placement": None,
        "publish_dir": None,
        "lod_meta_paths": {"lod0": None, "lod1": None, "lod2": None},
        "lod_mesh_paths": {"lod0": None, "lod1": None, "lod2": None},
    }
    
    source_path = ""
    data = item[0] if isinstance(item, list) and item else item # Handle cases where item might be a list containing the actual data
    
    if isinstance(data, dict):
        # Prioritize known keys for the primary asset/variant identification
        source_path = data.get("hires", data.get("source_mesh"))
        
        # If no primary path, use one of the LODs to identify the asset/variant
        if not source_path:
            for lod_key in ["lod0", "lod1", "lod2"]:
                val = data.get(lod_key)
                if isinstance(val, str) and path_pattern.search(val):
                    source_path = val
                    break
    elif isinstance(data, str):
        source_path = data
        base_meta_rel = data if data.lower().endswith(".meta") else None

    if isinstance(data, dict):
        base_meta_rel = data.get("hires") or data.get("source_mesh")
    if base_meta_rel:
        base_meta_path = base_meta_rel if os.path.isabs(base_meta_rel) else os.path.join(asset_map_dir, base_meta_rel)
        report_paths["base_meta_path"] = norm_path(base_meta_path)

    if not isinstance(source_path, str) or not source_path:
        reason = f"Skipping item: could not extract path. Item={data}"
        log_warn(index, "INPUT", reason)
        item_reasons.append(reason)
        write_item_report(index, "UNKNOWN", "FAIL", item_reasons, report_paths)
        run_report.append({"index": index, "asset_tag": "UNKNOWN", "status": "FAIL", "reasons": item_reasons, "paths": report_paths})
        continue
    report_paths["source_path"] = norm_path(source_path)

    # Use Regex to get asset/variant from the path itself
    match = path_pattern.search(source_path)
    if not match:
        reason = f"Skipping item: pattern mismatch for path {source_path}"
        log_warn(index, "INPUT", reason)
        item_reasons.append(reason)
        write_item_report(index, "UNKNOWN", "FAIL", item_reasons, report_paths)
        run_report.append({"index": index, "asset_tag": "UNKNOWN", "status": "FAIL", "reasons": item_reasons, "paths": report_paths})
        continue

    asset = match.group("asset")
    variant = match.group("variant")
    asset_tag = f"{asset}/{variant}"

    # Only import the base asset if it was actually in the JSON as hires/source_mesh or if the item itself was a string path
    has_base_import = (isinstance(data, dict) and ("hires" in data or "source_mesh" in data)) or isinstance(data, str)
    tex_dir = get_texture_folder_from_base_meta(data, asset_map_dir)
    report_paths["tex_dir"] = norm_path(tex_dir)
    if tex_dir:
        log_step(index, "MAT", f"LOD material source: {norm_path(tex_dir)}")
    else:
        reason = "No texture folder found from base meta; LODs will keep default materials"
        log_warn(index, "MAT", reason)
        item_reasons.append(reason)

    if has_base_import:
        log_step(index, "BASE", f"Importing {asset_tag} as {import_type}")
        try:
            import_result, used_import_type = run_general_import_with_guards(
                game, level, asset, variant, import_type
            )
            if used_import_type != import_type:
                log_step(index, "BASE", f"Fallback import type used: {used_import_type}")
            base_asset_nodes = import_result if isinstance(import_result, (list, tuple)) else [import_result]
            base_asset_nodes = [node for node in base_asset_nodes if isinstance(node, str) and cmds.objExists(node)]
            
            # Apply soft normals and delete history
            base_transforms = cmds.ls(base_asset_nodes, long=True, type="transform") or []
            if base_transforms:
                # Use the imported base transform name as the LOD naming prefix.
                base_name = base_transforms[0].split("|")[-1]
                lod_name_prefix = re.sub(r"_LOD\d+$", "", base_name, flags=re.IGNORECASE)
                base_attr_source = base_transforms[0]
                base_delete_target = base_attr_source
                expected_lod0_name = f"{lod_name_prefix}_LOD0"
                if base_name.lower() == expected_lod0_name.lower():
                    renamed_base = cmds.rename(base_attr_source, f"{lod_name_prefix}_BASE")
                    base_attr_source = cmds.ls(renamed_base, long=True)[0]
                    base_delete_target = base_attr_source
                    base_transforms = [base_attr_source]
                    log_step(index, "BASE", f"Renamed base to avoid LOD0 collision: {base_name} -> {renamed_base}")
                cmds.select(base_transforms, replace=True)
                cmds.polySoftEdge(angle=30)
                cmds.delete(base_transforms, constructionHistory=True)
                cmds.makeIdentity(base_transforms, apply=True, t=1, r=1, s=1, n=0)
                cmds.select(clear=True)
                # Remove base shading networks so LOD material build does not reuse publish shaders by name.
                for base_node in base_transforms:
                    try:
                        texture_utils.remove_shading_nodes_from_mesh(base_node)
                    except Exception as e:
                        log_warn(index, "BASE", f"Could not remove base shading nodes from {base_node}: {e}")
                log_step(index, "BASE", "Cleared base shading networks")
            else:
                log_warn(index, "BASE", "No base transforms found for cleanup")
            
        except Exception as e:
            reason = f"Failed processing {asset_tag}: {e}"
            log_fail(index, "BASE", reason)
            item_reasons.append(reason)
    else:
        log_step(index, "BASE", f"No base import for {asset_tag}; proceeding with LODs only")
    
    # Load LODs from json
    if isinstance(data, dict):
        base_dir = asset_map_dir
        
        for i, lod_key in enumerate(["lod0", "lod1", "lod2"]):
            lod_meta_rel_path = data.get(lod_key)
            
            if not lod_meta_rel_path:
                continue
                
            lod_meta_abs_path = os.path.join(base_dir, lod_meta_rel_path)
            report_paths["lod_meta_paths"][lod_key] = lod_meta_abs_path
            
            if os.path.exists(lod_meta_abs_path) and lod_meta_abs_path.lower().endswith('.meta'):
                try:
                    with open(lod_meta_abs_path, 'r') as f:
                        lod_meta_data = json.load(f)
                        lod_mesh_path = lod_meta_data.get("output_mesh") or lod_meta_data.get("source_mesh")
                        report_paths["lod_mesh_paths"][lod_key] = norm_path(lod_mesh_path)
                        
                        if lod_mesh_path and os.path.exists(lod_mesh_path):
                            log_step(index, lod_key.upper(), f"Importing mesh {norm_path(lod_mesh_path)}")
                            imported_nodes = cmds.file(lod_mesh_path, i=True, type="OBJ", options="mo=1", pr=True, returnNewNodes=True)
                            
                            # Rename imported LOD mesh
                            if imported_nodes:
                                # Assuming the first node is the mesh transform
                                if not lod_name_prefix:
                                    lod_name_prefix = f"{asset}_{variant}"
                                lod_name = f"{lod_name_prefix}_LOD{i}"
                                lod_name = cmds.rename(imported_nodes[0], lod_name)
                                lod_long_name = (cmds.ls(lod_name, long=True) or [lod_name])[0]
                                lod_nodes.append(lod_long_name)
                                if i == 0:
                                    lod0_node = lod_long_name
                                log_step(index, lod_key.upper(), f"Renamed {imported_nodes[0]} -> {lod_name}")

                                if i == 0 and base_attr_source and cmds.objExists(base_attr_source):
                                    pub_util.transfer_custom_attrs(base_attr_source, lod_long_name)
                                    log_step(index, "ATTR", f"Transferred custom attrs {base_attr_source} -> {lod_long_name}")

                                if tex_dir and os.path.isdir(tex_dir):
                                    cmds.select(lod_long_name, replace=True)
                                    texture_utils.construct_shaders_from_folder(tex_dir, True)
                                    log_step(index, "MAT", f"Applied material to {lod_name}")

                                if i == 0:
                                    if run_prep_from_selection_on_node(lod_long_name, asset_name=asset, variant_name=variant):
                                        log_step(index, "PREP", f"Ran Prep from Selection flow on {lod_name}")
                                    else:
                                        log_warn(index, "PREP", f"Could not run Prep from Selection flow on {lod_name}")
                                
                                # Apply soft normals and delete history to LOD
                                cmds.select(lod_long_name, replace=True)
                                cmds.polySoftEdge(angle=30)
                                cmds.delete(ch=True) # Delete construction history
                                cmds.makeIdentity(apply=True, t=1, r=1, s=1, n=0) # Freeze transformations
                                cmds.select(clear=True)
                                
                            else:
                                reason = f"{lod_key}: no nodes returned from import"
                                log_warn(index, lod_key.upper(), "No nodes returned from import")
                                item_reasons.append(reason)
                        else:
                            reason = f"{lod_key}: mesh path missing/invalid: {norm_path(lod_mesh_path)}"
                            log_warn(index, lod_key.upper(), f"Mesh path missing/invalid: {norm_path(lod_mesh_path)}")
                            item_reasons.append(reason)
                except Exception as e:
                    reason = f"{lod_key}: failed reading meta {norm_path(lod_meta_abs_path)}: {e}"
                    log_fail(index, lod_key.upper(), f"Failed reading meta {norm_path(lod_meta_abs_path)}: {e}")
                    item_reasons.append(reason)
            else:
                reason = f"{lod_key}: meta file missing/invalid: {norm_path(lod_meta_abs_path)}"
                log_warn(index, lod_key.upper(), f"Meta file missing/invalid: {norm_path(lod_meta_abs_path)}")
                item_reasons.append(reason)

    if base_delete_target and cmds.objExists(base_delete_target):
        deleted_nodes = delete_node_and_empty_parents(base_delete_target)
        if deleted_nodes:
            log_step(index, "BASE", f"Deleted base hierarchy: {', '.join(deleted_nodes)}")
        else:
            log_warn(index, "BASE", f"Base delete requested but nothing removed: {base_delete_target}")

    if lod0_node and lod_nodes:
        try:
            lod0_publish_name = lod0_node.split("|")[-1]
            report_paths["publish_placement"] = lod0_publish_name
            cmds.select(lod_nodes, replace=True)
            publish_dir = pub_util.publish_asset(
                game,
                level,
                lod0_publish_name,
                data_types=["fbx", "usd"],
                collision_type="auto"
            )
            report_paths["publish_dir"] = norm_path(publish_dir)
            log_step(index, "PUBLISH", f"Publish directory: {norm_path(publish_dir)}")
            log_step(index, "PUBLISH", f"Published using placement mesh {lod0_publish_name}")
            publish_succeeded = True
        except Exception as e:
            reason = f"publish failed for {asset_tag}: {e}"
            log_fail(index, "PUBLISH", f"Failed publishing {asset_tag}: {e}")
            item_reasons.append(reason)
    else:
        reason = "Skipped publish: missing LOD0 or LOD selection"
        log_warn(index, "PUBLISH", reason)
        item_reasons.append(reason)

    status = "PASS" if publish_succeeded else "FAIL"
    write_item_report(index, asset_tag, status, item_reasons, report_paths)
    run_report.append({"index": index, "asset_tag": asset_tag, "status": status, "reasons": item_reasons, "paths": report_paths})
    log_step(index, "DONE", f"Completed {asset_tag} ({status})")

passed = [r for r in run_report if r["status"] == "PASS"]
failed = [r for r in run_report if r["status"] == "FAIL"]
log_line(f"[SUMMARY] Passed: {len(passed)} | Failed: {len(failed)} | Total: {len(run_report)}")
for r in failed:
    log_line(f"[SUMMARY] FAIL [{r['index']:03d}] {r['asset_tag']}")
    for reason in r["reasons"]:
        log_line(f"[SUMMARY] WHY: {reason}")

publish_json = {
    "generated_at": datetime.datetime.now().isoformat(),
    "log_file": norm_path(os.path.abspath(log_file_path)),
    "items": [],
}

for r in run_report:
    publish_json["items"].append({
        "index": r["index"],
        "asset": r["asset_tag"],
        "status": r["status"],
        "publish_dir": r["paths"].get("publish_dir"),
        "publish_placement": r["paths"].get("publish_placement"),
        "reasons": r["reasons"],
    })

with open(publish_report_json_path, "w") as f:
    json.dump(publish_json, f, indent=2)

log_line(f"[SUMMARY] Publish JSON: {norm_path(os.path.abspath(publish_report_json_path))}")

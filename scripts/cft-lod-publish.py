import datetime
import json
import os
import re

import maya.cmds as cmds
from cft_sandbox import import_utils
from cft_sandbox import publish_utils as pub_util
from cft_sandbox import texture_utils

LOD_KEYS = ["lod0", "lod1", "lod2"]


def get_current_dir():
    try:
        return os.path.dirname(__file__)
    except NameError:
        # This can happen if the script is run interactively.
        return r"E:\work\c53\test\cft-lod-publish-batch\scripts"


CURRENT_DIR = get_current_dir()
CONFIG_PATH = os.path.join(CURRENT_DIR, "global_config.json")
LOG_FILE_PATH = os.path.join(CURRENT_DIR, "publish.log")
PUBLISH_REPORT_JSON_PATH = os.path.join(CURRENT_DIR, "publish_report.json")


def norm_path(path_value):
    if path_value is None:
        return None
    return os.path.normpath(str(path_value)).replace("\\", "/")


def init_log_file():
    with open(LOG_FILE_PATH, "w") as log_f:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_f.write(f"--- Import Error Log: {timestamp} ---\n\n")
        startup_log_path = norm_path(os.path.abspath(LOG_FILE_PATH))
        log_f.write(f"[INFO] Log file path: {startup_log_path}\n")


def log_line(msg):
    print(msg)
    with open(LOG_FILE_PATH, "a") as log_f:
        log_f.write(msg + "\n")


def log_step(index, stage, msg):
    log_line(f"[{index:03d}] {stage}: {msg}")


def log_warn(index, stage, msg):
    log_line(f"[{index:03d}] WARN {stage}: {msg}")


def log_fail(index, stage, msg):
    log_line(f"[{index:03d}] ERROR {stage}: {msg}")


def load_json(path):
    with open(path, "r") as f:
        return json.load(f)


def resolve_path(path_value, base_dir):
    if not isinstance(path_value, str) or not path_value:
        return None
    if os.path.isabs(path_value):
        return os.path.normpath(path_value)
    return os.path.normpath(os.path.join(base_dir, path_value))


def new_report_paths():
    return {
        "source_path": None,
        "base_meta_path": None,
        "tex_dir": None,
        "publish_placement": None,
        "publish_dir": None,
        "lod_meta_paths": {"lod0": None, "lod1": None, "lod2": None},
        "lod_mesh_paths": {"lod0": None, "lod1": None, "lod2": None},
    }


def write_item_report(index, asset_tag, status, reasons, paths):
    log_line(f"[{index:03d}] RESULT: {status} {asset_tag}")
    log_line(f"[{index:03d}] PATH source: {norm_path(paths.get('source_path'))}")
    log_line(f"[{index:03d}] PATH base_meta: {norm_path(paths.get('base_meta_path'))}")
    log_line(f"[{index:03d}] PATH texture_dir: {norm_path(paths.get('tex_dir'))}")
    log_line(f"[{index:03d}] PATH publish_placement: {paths.get('publish_placement')}")
    log_line(f"[{index:03d}] PATH publish_dir: {norm_path(paths.get('publish_dir'))}")

    lod_meta_paths = paths.get("lod_meta_paths", {})
    for lod_key in LOD_KEYS:
        log_line(f"[{index:03d}] PATH {lod_key}_meta: {norm_path(lod_meta_paths.get(lod_key))}")

    lod_mesh_paths = paths.get("lod_mesh_paths", {})
    for lod_key in LOD_KEYS:
        log_line(f"[{index:03d}] PATH {lod_key}_mesh: {norm_path(lod_mesh_paths.get(lod_key))}")

    for reason in reasons:
        log_line(f"[{index:03d}] WHY: {reason}")
    log_line(f"[{index:03d}] {'-' * 70}")


def finalize_item(index, asset_tag, publish_succeeded, reasons, paths, run_report):
    status = "PASS" if publish_succeeded else "FAIL"
    write_item_report(index, asset_tag, status, reasons, paths)
    run_report.append(
        {
            "index": index,
            "asset_tag": asset_tag,
            "status": status,
            "reasons": reasons,
            "paths": paths,
        }
    )
    log_step(index, "DONE", f"Completed {asset_tag} ({status})")


def flatten_item(item):
    return item[0] if isinstance(item, list) and item else item


def get_primary_meta_path(data_item, map_base_dir):
    meta_rel = None
    if isinstance(data_item, dict):
        meta_rel = data_item.get("hires") or data_item.get("source_mesh")
        if not meta_rel:
            for lod_key in LOD_KEYS:
                lod_rel = data_item.get(lod_key)
                if isinstance(lod_rel, str) and lod_rel.lower().endswith(".meta"):
                    meta_rel = lod_rel
                    break
    elif isinstance(data_item, str) and data_item.lower().endswith(".meta"):
        meta_rel = data_item

    meta_abs = resolve_path(meta_rel, map_base_dir)
    if not meta_abs or not os.path.isfile(meta_abs):
        return None
    return meta_abs


def extract_asset_variant_from_path(path_value, path_pattern):
    if not isinstance(path_value, str) or not path_value:
        return None, None

    if path_pattern and path_pattern.pattern:
        match = path_pattern.search(path_value)
        if match:
            return match.group("asset"), match.group("variant")

    models_match = re.search(r"/models/(?P<asset>[^/]+)/(?P<variant>[^/]+)/", path_value, re.IGNORECASE)
    if models_match:
        return models_match.group("asset"), models_match.group("variant")

    lod_match = re.search(r"/resolution-[^/]+/(?P<asset>[^/]+)/(?P<variant>[^/]+)/", path_value, re.IGNORECASE)
    if lod_match:
        return lod_match.group("asset"), lod_match.group("variant")

    return None, None


def extract_asset_variant_from_meta(meta_path, path_pattern):
    try:
        meta_data = load_json(meta_path)
    except Exception:
        return None, None, None

    for key in ["source_mesh", "output_mesh", "texture_path"]:
        candidate = meta_data.get(key)
        if not isinstance(candidate, str) or not candidate:
            continue
        candidate_norm = candidate.replace("\\", "/")
        asset, variant = extract_asset_variant_from_path(candidate_norm, path_pattern)
        if asset and variant:
            return asset, variant, candidate_norm

    return None, None, None


def find_fallback_source_path(data_item):
    if isinstance(data_item, dict):
        source_path = data_item.get("hires") or data_item.get("source_mesh")
        if source_path:
            return source_path
        for lod_key in LOD_KEYS:
            value = data_item.get(lod_key)
            if isinstance(value, str) and value:
                return value
        return None
    if isinstance(data_item, str) and data_item:
        return data_item
    return None


def resolve_asset_identity(data_item, asset_map_dir, path_pattern):
    base_meta_path = get_primary_meta_path(data_item, asset_map_dir)
    source_path = None
    asset = None
    variant = None

    if base_meta_path:
        asset, variant, source_path = extract_asset_variant_from_meta(base_meta_path, path_pattern)

    if not source_path:
        source_path = find_fallback_source_path(data_item)

    if not (asset and variant):
        fallback_asset, fallback_variant = extract_asset_variant_from_path(source_path, path_pattern)
        asset = asset or fallback_asset
        variant = variant or fallback_variant

    return {
        "asset": asset,
        "variant": variant,
        "source_path": source_path,
        "base_meta_path": base_meta_path,
    }


def get_texture_folder_from_base_meta(data_item, map_base_dir):
    base_meta_path = get_primary_meta_path(data_item, map_base_dir)
    if not base_meta_path:
        return None

    try:
        meta_data = load_json(base_meta_path)
    except Exception:
        return None

    texture_path = meta_data.get("texture_path")
    texture_folder = resolve_path(texture_path, os.path.dirname(base_meta_path))
    if not texture_folder:
        return None

    png_folder = os.path.join(texture_folder, "png")
    if os.path.isdir(png_folder):
        return png_folder
    if os.path.isdir(texture_folder):
        return texture_folder
    return None


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
            if file_type == "usd" and not cmds.pluginInfo("mayaUsdPlugin", query=True, loaded=True):
                cmds.loadPlugin("mayaUsdPlugin")

            result = import_utils.do_general_import(game, level, asset, variant, file_type, versioned_dir=None)
            if not result:
                raise RuntimeError(f"do_general_import returned {result!r}")
            return result, file_type
        except Exception as e:
            errors.append(f"{file_type}: {e}")

    raise RuntimeError("All import attempts failed -> " + " | ".join(errors))


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


def cleanup_transform_nodes(nodes):
    if not nodes:
        return
    cmds.select(nodes, replace=True)
    cmds.polySoftEdge(angle=30)
    cmds.delete(nodes, constructionHistory=True)
    cmds.makeIdentity(nodes, apply=True, t=1, r=1, s=1, n=0)
    cmds.select(clear=True)


def import_base_asset(index, game, level, asset, variant, import_type, item_reasons):
    lod_name_prefix = None
    base_attr_source = None
    base_delete_target = None
    asset_tag = f"{asset}/{variant}"

    log_step(index, "BASE", f"Importing {asset_tag} as {import_type}")
    try:
        import_result, used_import_type = run_general_import_with_guards(game, level, asset, variant, import_type)
        if used_import_type != import_type:
            log_step(index, "BASE", f"Fallback import type used: {used_import_type}")

        base_asset_nodes = import_result if isinstance(import_result, (list, tuple)) else [import_result]
        base_asset_nodes = [node for node in base_asset_nodes if isinstance(node, str) and cmds.objExists(node)]
        base_transforms = cmds.ls(base_asset_nodes, long=True, type="transform") or []
        if not base_transforms:
            log_warn(index, "BASE", "No base transforms found for cleanup")
            return lod_name_prefix, base_attr_source, base_delete_target

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

        cleanup_transform_nodes(base_transforms)
        for base_node in base_transforms:
            try:
                texture_utils.remove_shading_nodes_from_mesh(base_node)
            except Exception as e:
                log_warn(index, "BASE", f"Could not remove base shading nodes from {base_node}: {e}")
        log_step(index, "BASE", "Cleared base shading networks")
    except Exception as e:
        reason = f"Failed processing {asset_tag}: {e}"
        log_fail(index, "BASE", reason)
        item_reasons.append(reason)

    return lod_name_prefix, base_attr_source, base_delete_target


def load_lod_mesh_path(lod_meta_abs_path):
    lod_meta_data = load_json(lod_meta_abs_path)
    return lod_meta_data.get("output_mesh") or lod_meta_data.get("source_mesh")


def import_lod_nodes(index, data_item, asset_map_dir, asset, variant, tex_dir, base_attr_source, report_paths, item_reasons, lod_name_prefix):
    lod_nodes = []
    lod0_node = None

    if not isinstance(data_item, dict):
        return lod_nodes, lod0_node, lod_name_prefix

    for i, lod_key in enumerate(LOD_KEYS):
        lod_meta_rel_path = data_item.get(lod_key)
        if not lod_meta_rel_path:
            continue

        lod_meta_abs_path = resolve_path(lod_meta_rel_path, asset_map_dir)
        report_paths["lod_meta_paths"][lod_key] = lod_meta_abs_path
        if not lod_meta_abs_path or not os.path.exists(lod_meta_abs_path) or not lod_meta_abs_path.lower().endswith(".meta"):
            reason = f"{lod_key}: meta file missing/invalid: {norm_path(lod_meta_abs_path)}"
            log_warn(index, lod_key.upper(), f"Meta file missing/invalid: {norm_path(lod_meta_abs_path)}")
            item_reasons.append(reason)
            continue

        try:
            lod_mesh_path = load_lod_mesh_path(lod_meta_abs_path)
            report_paths["lod_mesh_paths"][lod_key] = norm_path(lod_mesh_path)
            if not lod_mesh_path or not os.path.exists(lod_mesh_path):
                reason = f"{lod_key}: mesh path missing/invalid: {norm_path(lod_mesh_path)}"
                log_warn(index, lod_key.upper(), f"Mesh path missing/invalid: {norm_path(lod_mesh_path)}")
                item_reasons.append(reason)
                continue

            log_step(index, lod_key.upper(), f"Importing mesh {norm_path(lod_mesh_path)}")
            imported_nodes = cmds.file(lod_mesh_path, i=True, type="OBJ", options="mo=1", pr=True, returnNewNodes=True)
            if not imported_nodes:
                reason = f"{lod_key}: no nodes returned from import"
                log_warn(index, lod_key.upper(), "No nodes returned from import")
                item_reasons.append(reason)
                continue

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

            cleanup_transform_nodes([lod_long_name])
        except Exception as e:
            reason = f"{lod_key}: failed reading meta {norm_path(lod_meta_abs_path)}: {e}"
            log_fail(index, lod_key.upper(), f"Failed reading meta {norm_path(lod_meta_abs_path)}: {e}")
            item_reasons.append(reason)

    return lod_nodes, lod0_node, lod_name_prefix


def publish_lods(index, game, level, asset_tag, lod_nodes, lod0_node, report_paths, item_reasons):
    if not (lod0_node and lod_nodes):
        reason = "Skipped publish: missing LOD0 or LOD selection"
        log_warn(index, "PUBLISH", reason)
        item_reasons.append(reason)
        return False

    try:
        lod0_publish_name = lod0_node.split("|")[-1]
        report_paths["publish_placement"] = lod0_publish_name
        cmds.select(lod_nodes, replace=True)
        publish_dir = pub_util.publish_asset(
            game,
            level,
            lod0_publish_name,
            data_types=["fbx", "usd"],
            collision_type="auto",
        )
        report_paths["publish_dir"] = norm_path(publish_dir)
        log_step(index, "PUBLISH", f"Publish directory: {norm_path(publish_dir)}")
        log_step(index, "PUBLISH", f"Published using placement mesh {lod0_publish_name}")
        return True
    except Exception as e:
        reason = f"publish failed for {asset_tag}: {e}"
        log_fail(index, "PUBLISH", f"Failed publishing {asset_tag}: {e}")
        item_reasons.append(reason)
        return False


def process_item(index, item, game, level, import_type, asset_map_dir, path_pattern):
    cmds.file(new=True, force=True)

    item_reasons = []
    report_paths = new_report_paths()
    publish_succeeded = False
    data = flatten_item(item)

    identity = resolve_asset_identity(data, asset_map_dir, path_pattern)
    asset = identity["asset"]
    variant = identity["variant"]
    source_path = identity["source_path"]
    base_meta_path = identity["base_meta_path"]
    report_paths["base_meta_path"] = norm_path(base_meta_path)
    report_paths["source_path"] = norm_path(source_path)

    if not isinstance(source_path, str) or not source_path:
        reason = f"Skipping item: could not extract source path from meta or item data. Item={data}"
        log_warn(index, "INPUT", reason)
        item_reasons.append(reason)
        return "UNKNOWN", publish_succeeded, item_reasons, report_paths

    if not (asset and variant):
        reason = f"Skipping item: could not extract asset/variant from meta file {norm_path(base_meta_path)}"
        log_warn(index, "INPUT", reason)
        item_reasons.append(reason)
        return "UNKNOWN", publish_succeeded, item_reasons, report_paths

    asset_tag = f"{asset}/{variant}"
    has_base_import = (isinstance(data, dict) and ("hires" in data or "source_mesh" in data)) or isinstance(data, str)
    tex_dir = get_texture_folder_from_base_meta(data, asset_map_dir)
    report_paths["tex_dir"] = norm_path(tex_dir)
    if tex_dir:
        log_step(index, "MAT", f"LOD material source: {norm_path(tex_dir)}")
    else:
        reason = "No texture folder found from base meta; LODs will keep default materials"
        log_warn(index, "MAT", reason)
        item_reasons.append(reason)

    lod_name_prefix = None
    base_attr_source = None
    base_delete_target = None
    if has_base_import:
        lod_name_prefix, base_attr_source, base_delete_target = import_base_asset(
            index, game, level, asset, variant, import_type, item_reasons
        )
    else:
        log_step(index, "BASE", f"No base import for {asset_tag}; proceeding with LODs only")

    lod_nodes, lod0_node, _ = import_lod_nodes(
        index,
        data,
        asset_map_dir,
        asset,
        variant,
        tex_dir,
        base_attr_source,
        report_paths,
        item_reasons,
        lod_name_prefix,
    )

    if base_delete_target and cmds.objExists(base_delete_target):
        deleted_nodes = delete_node_and_empty_parents(base_delete_target)
        if deleted_nodes:
            log_step(index, "BASE", f"Deleted base hierarchy: {', '.join(deleted_nodes)}")
        else:
            log_warn(index, "BASE", f"Base delete requested but nothing removed: {base_delete_target}")

    publish_succeeded = publish_lods(
        index, game, level, asset_tag, lod_nodes, lod0_node, report_paths, item_reasons
    )
    return asset_tag, publish_succeeded, item_reasons, report_paths


def write_run_summary(run_report):
    passed = [r for r in run_report if r["status"] == "PASS"]
    failed = [r for r in run_report if r["status"] == "FAIL"]
    log_line(f"[SUMMARY] Passed: {len(passed)} | Failed: {len(failed)} | Total: {len(run_report)}")
    for row in failed:
        log_line(f"[SUMMARY] FAIL [{row['index']:03d}] {row['asset_tag']}")
        for reason in row["reasons"]:
            log_line(f"[SUMMARY] WHY: {reason}")


def write_publish_report_json(run_report):
    publish_json = {
        "generated_at": datetime.datetime.now().isoformat(),
        "log_file": norm_path(os.path.abspath(LOG_FILE_PATH)),
        "items": [],
    }
    for row in run_report:
        publish_json["items"].append(
            {
                "index": row["index"],
                "asset": row["asset_tag"],
                "status": row["status"],
                "publish_dir": row["paths"].get("publish_dir"),
                "publish_placement": row["paths"].get("publish_placement"),
                "reasons": row["reasons"],
            }
        )

    with open(PUBLISH_REPORT_JSON_PATH, "w") as f:
        json.dump(publish_json, f, indent=2)
    log_line(f"[SUMMARY] Publish JSON: {norm_path(os.path.abspath(PUBLISH_REPORT_JSON_PATH))}")


def main():
    config = load_json(CONFIG_PATH)
    game = config.get("game")
    level = config.get("level")
    asset_map_path = config.get("asset_map_path")
    import_type = config.get("import_type", "usd")
    path_pattern = re.compile(config.get("path_pattern", ""), re.IGNORECASE)

    asset_map = load_json(asset_map_path)
    asset_map_dir = os.path.dirname(asset_map_path)
    items = asset_map if isinstance(asset_map, list) else list(asset_map.values())

    log_line(f"[INFO] Found {len(items)} items to process.")
    run_report = []
    for index, item in enumerate(items):
        asset_tag, publish_succeeded, item_reasons, report_paths = process_item(
            index, item, game, level, import_type, asset_map_dir, path_pattern
        )
        finalize_item(index, asset_tag, publish_succeeded, item_reasons, report_paths, run_report)

    write_run_summary(run_report)
    write_publish_report_json(run_report)


if __name__ == "__main__":
    init_log_file()
    main()

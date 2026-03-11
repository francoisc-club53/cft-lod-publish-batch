import os

import maya.cmds as cmds
from cft_sandbox import texture_utils


TEXTURE_ROOT = r"E:\work\c53\projects\bigtama_assets\trunk\models"


def get_selected_transform():
    selection = cmds.ls(selection=True, long=True) or []
    if not selection:
        raise RuntimeError("Nothing selected. Select a mesh or transform.")

    node = selection[0]
    node_type = cmds.nodeType(node)

    if node_type == "transform":
        return node

    if node_type == "mesh":
        parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
        if parents:
            return parents[0]

    if ".f[" in node or ".vtx[" in node or ".e[" in node:
        parents = cmds.ls(node, objectsOnly=True, long=True) or []
        if parents and cmds.nodeType(parents[0]) == "transform":
            return parents[0]

    raise RuntimeError(f"Selected node is not a mesh/transform: {node}")


def get_string_attr(transform, attr_name):
    plug = f"{transform}.{attr_name}"
    if not cmds.attributeQuery(attr_name, node=transform, exists=True):
        raise RuntimeError(f"Missing attribute '{attr_name}' on {transform}")
    value = cmds.getAttr(plug)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Attribute '{attr_name}' is empty on {transform}")
    return value.strip()


def main():
    transform = get_selected_transform()
    asset_type = get_string_attr(transform, "assetType")
    variant = get_string_attr(transform, "variant")

    texture_folder = os.path.join(TEXTURE_ROOT, asset_type, variant, "textures", "png")
    if not os.path.isdir(texture_folder):
        raise RuntimeError(f"Texture folder does not exist: {texture_folder}")

    cmds.select(transform, replace=True)
    print(f"[TEXTURE] Applying from: {texture_folder}")
    texture_utils.construct_shaders_from_folder(texture_folder, True)
    print(f"[TEXTURE] Done for {transform}")


if __name__ == "__main__":
    main()

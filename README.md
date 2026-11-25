# Smart Anchor UV v1.0.0

Community release candidate. Convert UV editing into box-mesh deformation using Begin and Finish.

## Usage
1. Select an editable mesh with UVs. This version supports disconnected closed boxes with six quad faces and eight vertices each.
2. In UV Editor or 3D View, press N and open Smart Anchor.
3. Click Begin. Edit UV points/edges using G, S, or R. Mesh positions remain unchanged until Finish.
4. Click Finish to apply corresponding mesh displacement, or Cancel to restore both mesh and UV positions captured at Begin.

## Limitations
- Experimental box-only topology; save your .blend before use.
- Compatible runtime and undo behavior should be tested in the exact Blender release used for submission.
- Changing topology during an active session is unsupported.

## Distribution
Source under GPL-3.0-or-later. Before publication, set `maintainer` in blender_manifest.toml to the real publishing person or group and rebuild the ZIP.

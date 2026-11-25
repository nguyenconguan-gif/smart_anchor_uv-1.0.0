# SPDX-License-Identifier: GPL-3.0-or-later
# Smart Anchor UV contributors

import bpy
import bmesh
from mathutils import Vector
from collections import defaultdict

_STATE = None
EPS_UV = 1.e-9


def components(bm):
    remaining = set(bm.faces)
    groups = []
    while remaining:
        face = remaining.pop()
        group = {face}
        queue = [face]
        while queue:
            f = queue.pop()
            for edge in f.edges:
                for adjacent in edge.link_faces:
                    if adjacent in remaining:
                        remaining.remove(adjacent)
                        group.add(adjacent)
                        queue.append(adjacent)
        groups.append(group)
    return groups


def is_box(group):
    verts = {v for f in group for v in f.verts}
    edges = {e for f in group for e in f.edges}
    return (len(group) == 6 and len(verts) == 8 and len(edges) == 12
            and all(len(f.verts) == 4 for f in group)
            and all(len(e.link_faces) == 2 for e in edges))


def uv_snapshot(bm, layer):
    return {(f.index, i): loop[layer].uv.copy()
            for f in bm.faces for i, loop in enumerate(f.loops)}


def face_basis(face, uv0, pos0):
    uvs = [uv0[(face.index, i)] for i in range(4)]
    xyz = [pos0[loop.vert.index] for loop in face.loops]
    uc = sum(uvs, Vector((0.0, 0.0))) / 4.0
    pc = sum(xyz, Vector((0.0, 0.0, 0.0))) / 4.0
    uu = uvv = vv = 0.0
    pu = Vector((0.0, 0.0, 0.0))
    pv = Vector((0.0, 0.0, 0.0))
    for uv, p in zip(uvs, xyz):
        du, dv = uv.x - uc.x, uv.y - uc.y
        dp = p - pc
        uu += du * du
        uvv += du * dv
        vv += dv * dv
        pu += du * dp
        pv += dv * dp
    det = uu * vv - uvv * uvv
    if det <= 1.e-12:
        return None
    tu = (vv * pu - uvv * pv) / det
    tv = (uu * pv - uvv * pu) / det
    if tu.length_squared <= 1.e-12 or tv.length_squared <= 1.e-12:
        return None
    return (tu, tv)


def face_center(face, pos):
    return sum((pos[v.index] for v in face.verts), Vector((0.0, 0.0, 0.0))) / 4.0


def candidate_caps(group, pos):
    faces = sorted(group, key=lambda f: f.index)
    pairs = []
    for i, a in enumerate(faces):
        for b in faces[i + 1:]:
            if not set(a.verts).intersection(b.verts):
                # Area proxy for potentially transformed box; original face area cached by Blender.
                pairs.append((0.5 * (a.calc_area() + b.calc_area()), a, b))
    return sorted(pairs, key=lambda item: (item[0], item[1].index))


def find_fixed_cap(group, changed_vertices, pos):
    pairs = candidate_caps(group, pos)
    if len(pairs) != 3:
        return None, 'Mesh is not a recognizable closed six-quad box'
    # Prefer the smallest cross-section for a clearly elongated beam.
    distinct_small_cap = pairs[0][0] < pairs[1][0] * 0.85
    if distinct_small_cap:
        options = pairs[:1]
    else:
        options = pairs
    eligible = []
    for _, a, b in options:
        av = {v.index for v in a.verts}
        bv = {v.index for v in b.verts}
        ca = len(changed_vertices & av)
        cb = len(changed_vertices & bv)
        if ca and not cb:
            eligible.append((ca, frozenset(bv)))
        if cb and not ca:
            eligible.append((cb, frozenset(av)))
    if not eligible:
        return None, 'Cannot determine a stationary opposite face: move UVs at only one end of the box'
    eligible.sort(key=lambda item: -item[0])
    best = [cap for hits, cap in eligible if hits == eligible[0][0]]
    if len(set(best)) > 1:
        return None, 'Opposite face is ambiguous: select more UV points at a single end'
    return set(best[0]), None


def mesh_snapshot(context):
    obj = context.edit_object
    if obj is None or obj.type != 'MESH' or obj.mode != 'EDIT':
        raise ValueError('Select one Mesh object in Edit Mode')
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.index_update()
    bm.verts.index_update()
    layer = bm.loops.layers.uv.active
    if layer is None:
        raise ValueError('The mesh has no active UV map')
    groups = [g for g in components(bm) if is_box(g)]
    if not groups:
        raise ValueError('No closed six-quad box Mesh Islands found')
    pos = {v.index: v.co.copy() for v in bm.verts}
    uv0 = uv_snapshot(bm, layer)
    boxes = []
    for group in groups:
        boxfaces = {f.index for f in group}
        boxes.append(dict(
            faces=boxfaces,
            vertices={v.index for f in group for v in f.verts},
            basis={f.index: face_basis(f, uv0, pos) for f in group},
        ))
    face_vertex_order = {f.index: tuple(v.index for v in f.verts) for f in bm.faces}
    return dict(obj=obj, mesh=obj.data, face_vertex_order=face_vertex_order,
                uv_layer_name=obj.data.uv_layers.active.name,
                vert_count=len(bm.verts), face_count=len(bm.faces), edge_count=len(bm.edges),
                uv0=uv0, pos0=pos, boxes=boxes)


def validate_state(context, state):
    obj = state['obj']
    if (obj is None or context.edit_object is not obj or obj.mode != 'EDIT'
            or obj.data is not state['mesh']):
        raise ValueError('Return to the original Mesh in Edit Mode to finish')
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.index_update()
    bm.verts.index_update()
    if (len(bm.faces) != state['face_count'] or len(bm.verts) != state['vert_count']
            or len(bm.edges) != state['edge_count']):
        raise ValueError('Mesh topology changed after Begin. Restore/Finish aborted')
    if any(tuple(v.index for v in f.verts) != state['face_vertex_order'].get(f.index)
           for f in bm.faces):
        raise ValueError('Mesh face/vertex order changed after Begin. Restore/Finish aborted')
    active_uv = obj.data.uv_layers.active
    if active_uv is None or active_uv.name != state['uv_layer_name']:
        raise ValueError('Active UV map changed since Begin')
    layer = bm.loops.layers.uv.active
    if layer is None:
        raise ValueError('No active UV map')
    uv1 = uv_snapshot(bm, layer)
    if uv1.keys() != state['uv0'].keys():
        raise ValueError('UV corner layout has changed since Begin')
    return bm, uv1


def calculate_positions(context, state, bm, uv1):
    originals = state['pos0']
    outputs = {}
    notices = []
    successes = 0
    for bi, info in enumerate(state['boxes'], 1):
        changed = []
        for fi in info['faces']:
            face = bm.faces[fi]
            basis = info['basis'][fi]
            for i, loop in enumerate(face.loops):
                key = (fi, i)
                duv = uv1[key] - state['uv0'][key]
                if duv.length > EPS_UV:
                    changed.append((loop.vert.index, duv, basis))
        if not changed:
            continue
        altered = {vid for vid, _, _ in changed}
        group = {bm.faces[fi] for fi in info['faces']}
        # First preserve the proven v0.8 end-cap rule when only one end moves.
        # For width edits, the selected UV vertices may span BOTH end caps
        # (e.g. a longitudinal edge or one entire long side of the box).
        # In that case, anchor a fully untouched opposite SIDE face instead.
        cap, _why = find_fixed_cap(group, altered, originals)
        if cap is None:
            untouched_faces = [f for f in group
                               if not any(v.index in altered for v in f.verts)]
            if not untouched_faces:
                notices.append('Box %d: no stationary opposite face; select one edge/side, not the whole box' % bi)
                continue
            # Multiple untouched faces are compatible: all their vertices
            # remain stationary; using their union does not split welds.
            cap = {v.index for face in untouched_faces for v in face.verts}
        # A vertex can be represented by multiple UV corners due to seams.
        # The altered corners must agree on their UV->3D target; never average inconsistent ones.
        movements = defaultdict(list)
        unresolvable = False
        for vid, duv, basis in changed:
            if basis is None:
                unresolvable = True
                break
            d3 = basis[0] * duv.x + basis[1] * duv.y
            movements[vid].append(d3)
        if unresolvable:
            notices.append('Box %d: degenerate UV face mapping' % bi)
            continue
        conflict = False
        updates = {}
        diameter = max((originals[a] - originals[b]).length
                       for a in info['vertices'] for b in info['vertices'])
        tolerance = max(1.e-5, diameter * 1.e-4)
        for vid, candidates in movements.items():
            if vid in cap:
                conflict = True
                break
            reference = candidates[0]
            if any((v - reference).length > tolerance for v in candidates[1:]):
                conflict = True
                break
            # Safety bound: delta per Finish <= 5x box diameter.
            if reference.length > max(diameter * 5.0, 1.e-5):
                conflict = True
                break
            updates[vid] = originals[vid] + reference
        if conflict:
            notices.append('Box %d: conflicting UV seams or unsafe displacement' % bi)
            continue
        # Reject obviously collapsed or inverted faces rather than creating
        # broken-looking geometry. All checks use the frozen Begin baseline.
        invalid_face = False
        for face in group:
            base = [originals[v.index] for v in face.verts]
            moved = [updates.get(v.index, originals[v.index]) for v in face.verts]
            # For quads check BOTH triangles, keeping Blender's loop order.
            for tri in ((0, 1, 2), (0, 2, 3)):
                i, j, k = tri
                n0 = (base[j] - base[i]).cross(base[k] - base[i])
                n1 = (moved[j] - moved[i]).cross(moved[k] - moved[i])
                if n0.length_squared <= 1.e-18:
                    continue
                if n1.length_squared < n0.length_squared * 1.e-6 or n0.dot(n1) <= 0.0:
                    invalid_face = True
                    break
            if invalid_face:
                break
        if invalid_face:
            notices.append('Box %d: UV edit would collapse/flip a mesh face' % bi)
            continue
        outputs.update(updates)
        successes += 1
    return outputs, successes, notices


class SATUV_OT_begin(bpy.types.Operator):
    bl_idname = 'satuv.begin_edit'
    bl_label = 'Begin UV Edit'
    bl_description = 'Capture UV and mesh baseline; UV edits do NOT move mesh until Finish'

    @classmethod
    def poll(cls, context):
        return (_STATE is None and context.edit_object is not None
                and context.edit_object.type == 'MESH')

    def execute(self, context):
        global _STATE
        try:
            _STATE = mesh_snapshot(context)
        except (ValueError, RuntimeError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, 'BEGIN: Edit UV freely with G/S/R; Mesh stays unchanged until Finish')
        return {'FINISHED'}


class SATUV_OT_finish(bpy.types.Operator):
    bl_idname = 'satuv.finish_edit'
    bl_label = 'Finish: Apply UV to Mesh'
    bl_description = 'Compare UV with Begin snapshot and apply one calculated mesh deformation'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _STATE is not None

    def execute(self, context):
        global _STATE
        state = _STATE
        if state is None:
            return {'CANCELLED'}
        try:
            bm, uv1 = validate_state(context, state)
            newpos, count, notices = calculate_positions(context, state, bm, uv1)
        except (ValueError, ReferenceError, RuntimeError) as exc:
            self.report({'ERROR'}, 'Finish cancelled: %s' % str(exc))
            return {'CANCELLED'}
        if not count:
            self.report({'WARNING'}, 'No box applied. ' + ('; '.join(notices[:2]) if notices else 'No UV changes detected'))
            return {'CANCELLED'}
        for vert_index, pos in newpos.items():
            bm.verts[vert_index].co = pos
        bmesh.update_edit_mesh(state['mesh'], loop_triangles=False, destructive=False)
        _STATE = None
        msg = 'Finished %d box(es). UV left untouched.' % count
        if notices:
            msg += ' Skipped %d ambiguous box(es).' % len(notices)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class SATUV_OT_cancel(bpy.types.Operator):
    bl_idname = 'satuv.cancel_edit'
    bl_label = 'Cancel: Restore UV + Mesh'
    bl_description = 'Restore ALL UV coordinates and Mesh vertex positions to the Begin snapshot'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _STATE is not None

    def execute(self, context):
        global _STATE
        state = _STATE
        if state is None:
            return {'CANCELLED'}
        try:
            # Validate everything BEFORE writing anything. This prevents a partial
            # restore if the object, UV layer, or mesh topology changed.
            bm, _ = validate_state(context, state)
            layer = bm.loops.layers.uv.active
            uv0 = state['uv0']
            pos0 = state['pos0']
            for f in bm.faces:
                for i, loop in enumerate(f.loops):
                    loop[layer].uv = uv0[(f.index, i)].copy()
            for vert in bm.verts:
                vert.co = pos0[vert.index].copy()
            bmesh.update_edit_mesh(state['mesh'], loop_triangles=False, destructive=False)
        except (ValueError, ReferenceError, RuntimeError, KeyError) as exc:
            self.report({'ERROR'}, 'Cancel could not safely restore: %s' % str(exc))
            # Preserve the Begin snapshot so the user can return to the proper
            # object/UV map and try Cancel again.
            return {'CANCELLED'}
        _STATE = None
        self.report({'INFO'}, 'Cancelled: all UV corners and Mesh vertices restored to Begin')
        return {'FINISHED'}


class SATUV_PT_uv(bpy.types.Panel):
    bl_label = 'Smart Anchor UV (Begin / Finish)'
    bl_idname = 'SATUV_PT_uv'
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category = 'Smart Anchor'

    def draw(self, context):
        draw_panel(self.layout)


class SATUV_PT_view(bpy.types.Panel):
    bl_label = 'Smart Anchor UV (Begin / Finish)'
    bl_idname = 'SATUV_PT_view'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Smart Anchor'

    def draw(self, context):
        draw_panel(self.layout)


def draw_panel(layout):
    col = layout.column(align=True)
    col.label(text='Two-Step UV to Mesh', icon='UV')
    col.label(text='Session: ACTIVE' if _STATE is not None else 'Session: OFF',
              icon='CHECKMARK' if _STATE is not None else 'PAUSE')
    col.operator('satuv.begin_edit', text='1. Begin', icon='PLAY')
    col.operator('satuv.finish_edit', text='2. Finish (Apply Mesh)', icon='CHECKMARK')
    col.operator('satuv.cancel_edit', text='Cancel (Restore UV + Mesh)', icon='LOOP_BACK')
    col.separator()
    col.label(text='Between Begin / Finish:')
    col.label(text='Use G / S / R on UV normally')
    col.label(text='Mesh stays fixed until Finish')
    col.label(text='Cancel restores UV AND Mesh')
    col.label(text='Only closed 6-quad boxes')
    col.label(text='Move UV end edge: length (V)')
    col.label(text='Move UV side edge: width (U)')
    col.label(text='Keep opposite UV side untouched')


CLASSES = (SATUV_OT_begin, SATUV_OT_finish, SATUV_OT_cancel, SATUV_PT_uv, SATUV_PT_view)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    global _STATE
    _STATE = None
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == '__main__':
    register()

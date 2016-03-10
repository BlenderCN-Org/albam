from base64 import b64decode
import ctypes
from io import BytesIO
import ntpath
import os
import tempfile

from albam.exceptions import BuildMeshError, ExportError
from albam.mtframework.mod import (
    VertexFormat, VertexFormat5, VertexFormat0,
    Mesh156,
    MaterialData,
    )
from albam.mtframework import Arc, Mod156, Tex112
from albam.mtframework.utils import (
    vertices_export_locations,
    )
from albam.utils import (
    pack_half_float,
    get_offset,
    triangles_list_to_triangles_strip,
    z_up_to_y_up,
    get_bounding_box_positions_from_blender_objects,
    get_bone_count_from_blender_objects,
    get_textures_from_blender_objects,
    get_materials_from_blender_objects,
    get_mesh_count_from_blender_objects,
    get_vertex_count_from_blender_objects,
    get_weights_per_vertex,
    get_uvs_per_vertex,
    )


def export_arc(blender_object):
    '''Exports an arc file containing mod and tex files, among others from a
    previously imported arc.'''
    mods = {}
    for child in blender_object.children:
        try:
            mod_dirpath = child.data.albam_mod156_dirpath
            # TODO: This could lead to errors if imported in Windows and exported in posix?
            mod_filepath = os.path.join(mod_dirpath, child.name)
        except AttributeError:
            raise ExportError("Object {0} did not come from the original arc")
        mod, textures = export_mod156(child)
        mods[mod_filepath] = (mod, textures)

    saved_arc = Arc(file_path=BytesIO(b64decode(blender_object.albam_arc)))
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_slash_ending = tmpdir + os.sep if not tmpdir.endswith(os.sep) else tmpdir
        saved_arc.unpack(tmpdir)
        mod_files = [os.path.join(root, f) for root, _, files in os.walk(tmpdir)
                     for f in files if f.endswith('.mod')]
        tex_files = {os.path.join(root, f) for root, _, files in os.walk(tmpdir)
                     for f in files if f.endswith('.tex')}
        new_tex_files = set()
        for modf in mod_files:
            rel_path = modf.split(tmpdir_slash_ending)[1]
            try:
                new_mod = mods[rel_path]
            except KeyError:
                raise ExportError("Can't export to arc, a mod file is missing: {}".format(rel_path))

            with open(modf, 'wb') as w:
                w.write(new_mod[0])
            mod_textures = new_mod[1]
            for texture in mod_textures:
                tex = Tex112.from_dds(file_path=texture.image.filepath)
                tex_name = os.path.basename(texture.image.filepath)
                tex_filepath = os.path.join(os.path.dirname(modf), tex_name.replace('.dds', '.tex'))
                new_tex_files.add(tex_filepath)
                with open(tex_filepath, 'wb') as w:
                    w.write(tex)
        # probably other files can reference textures besides mod, this is in case
        # textures applied have other names.
        # TODO: delete only textures referenced from saved_mods at import time
        unused_tex_files = tex_files - new_tex_files
        for utex in unused_tex_files:
            os.unlink(utex)
        new_arc = Arc.from_dir(tmpdir)
    return new_arc


def export_mod156(blender_object):
    '''The blender_object provided should have meshes as children'''

    objects = [child for child in blender_object.children] + [blender_object]
    try:
        mod_dirpath = blender_object.data.albam_mod156_dirpath
        saved_mod = Mod156(file_path=BytesIO(b64decode(blender_object.data.albam_mod156)))
    except AttributeError:
        raise ExportError("Can't export model to Mod156, the model to be exported "
                          "didn't come from a game that uses Mod156 (e.g. Resident Evil 5)")
    bounding_box = get_bounding_box_positions_from_blender_objects(objects)

    # TODO: this is also called in _export_textures...
    materials = get_materials_from_blender_objects(objects)
    textures = get_textures_from_blender_objects(objects)

    textures_array, materials_array = _export_textures_and_materials(objects, mod_dirpath, saved_mod)
    meshes_array, vertex_buffer, index_buffer = _export_meshes(objects, materials, bounding_box, saved_mod)

    mod = Mod156(id_magic=b'MOD',
                 version=156,
                 version_rev=1,
                 bone_count=get_bone_count_from_blender_objects(objects),
                 mesh_count=get_mesh_count_from_blender_objects(objects),
                 material_count=len(materials),
                 vertex_count=get_vertex_count_from_blender_objects(objects),
                 face_count=(ctypes.sizeof(index_buffer) // 2) + 1,
                 edge_count=0,  # TODO: add edge_count
                 vertex_buffer_size=ctypes.sizeof(vertex_buffer),
                 vertex_buffer_2_size=0,
                 texture_count=len(textures_array),
                 group_count=saved_mod.group_count,
                 group_data_array=saved_mod.group_data_array,
                 bone_palette_count=saved_mod.bone_palette_count,
                 sphere_x=saved_mod.sphere_x,
                 sphere_y=saved_mod.sphere_y,
                 sphere_z=saved_mod.sphere_z,
                 sphere_w=saved_mod.sphere_w,
                 box_min_x=saved_mod.box_min_x,
                 box_min_y=saved_mod.box_min_y,
                 box_min_z=saved_mod.box_min_z,
                 box_min_w=saved_mod.box_min_w,
                 box_max_x=saved_mod.box_max_x,
                 box_max_y=saved_mod.box_max_y,
                 box_max_z=saved_mod.box_max_z,
                 box_max_w=saved_mod.box_max_w,
                 unk_01=saved_mod.unk_01,
                 unk_02=saved_mod.unk_02,
                 unk_03=saved_mod.unk_03,
                 unk_04=saved_mod.unk_04,
                 unk_05=saved_mod.unk_05,
                 unk_06=saved_mod.unk_06,
                 unk_07=saved_mod.unk_07,
                 unk_08=saved_mod.unk_08,
                 unk_09=saved_mod.unk_09,
                 unk_10=saved_mod.unk_10,
                 unk_11=saved_mod.unk_11,
                 bones_array=saved_mod.bones_array,
                 bones_unk_matrix_array=saved_mod.bones_unk_matrix_array,
                 bones_world_transform_matrix_array=saved_mod.bones_world_transform_matrix_array,
                 unk_13=saved_mod.unk_13,
                 bone_palette_array=saved_mod.bone_palette_array,
                 textures_array=textures_array,
                 materials_data_array=materials_array,
                 meshes_array=meshes_array,
                 meshes_array_2=saved_mod.meshes_array_2,
                 vertex_buffer=vertex_buffer,
                 index_buffer=index_buffer
                 )

    mod.bones_array_offset = get_offset(mod, 'bones_array')
    mod.group_offset = get_offset(mod, 'group_data_array')
    mod.textures_array_offset = get_offset(mod, 'textures_array')
    mod.meshes_array_offset = get_offset(mod, 'meshes_array')
    mod.vertex_buffer_offset = get_offset(mod, 'vertex_buffer')
    mod.vertex_buffer_2_offset = get_offset(mod, 'vertex_buffer_2')
    mod.index_buffer_offset = get_offset(mod, 'index_buffer')
    return mod, textures


def _export_vertices(blender_mesh_object, bounding_box, bone_palette):
    blender_mesh = blender_mesh_object.data
    vertex_count = len(blender_mesh.vertices)
    weights_per_vertex = get_weights_per_vertex(blender_mesh_object)
    # TODO: check the number of uv layers
    uvs_per_vertex = get_uvs_per_vertex(blender_mesh_object.data, blender_mesh_object.data.uv_layers[0])
    if weights_per_vertex:
        max_bones_per_vertex = max({len(data) for data in weights_per_vertex.values()})
        if max_bones_per_vertex <= 4:
            weights_array_size = 4
            VF = VertexFormat
        elif max_bones_per_vertex <= 8:
            weights_array_size = 8
            VF = VertexFormat5
        else:
            raise RuntimeError("The mesh '{}' contains some vertex that are weighted by "
                               "more than 8 bones, which is not supported. Fix it and try again"
                               .format(blender_mesh.name))

    else:
        max_bones_per_vertex = 0
        weights_array_size = 0
        VF = VertexFormat0

    for vertex_index, (uv_x, uv_y) in uvs_per_vertex.items():
        # flipping for dds textures
        uv_y *= -1
        uv_x = pack_half_float(uv_x)
        uv_y = pack_half_float(uv_y)
        uvs_per_vertex[vertex_index] = (uv_x, uv_y)

    vertices_array = (VF * vertex_count)()
    if uvs_per_vertex and len(uvs_per_vertex) != vertex_count:
        raise BuildMeshError('There are some vertices with no uvs in mesh in {}'.format(blender_mesh.name))

    box_width = abs(bounding_box.min_x * 100) + abs(bounding_box.max_x * 100)
    box_height = abs(bounding_box.min_y * 100) + abs(bounding_box.max_y * 100)
    box_length = abs(bounding_box.min_z * 100) + abs(bounding_box.max_z * 100)

    for vertex_index, vertex in enumerate(blender_mesh.vertices):
        vertex_format = vertices_array[vertex_index]
        if max_bones_per_vertex:
            weights_data = weights_per_vertex[vertex_index]   # list of (str(bone_index), value)
        else:
            weights_data = []
        # FIXME: Assumming vertex groups were named after the bone index,
        # should get the bone and get the index
        bone_indices = [bone_palette.index(int(vg_name)) for vg_name, _ in weights_data]
        weight_values = [round(weight_value * 255) for _, weight_value in weights_data]

        xyz = (vertex.co[0] * 100, vertex.co[1] * 100, vertex.co[2] * 100)
        xyz = z_up_to_y_up(xyz)
        if VF != VertexFormat0:
            xyz = vertices_export_locations(xyz, box_width, box_length, box_height)
        vertex_format.position_x = xyz[0]
        vertex_format.position_y = xyz[1]
        vertex_format.position_z = xyz[2]
        vertex_format.position_w = 32767
        vertex_format.bone_indices = (ctypes.c_ubyte * weights_array_size)(*bone_indices)
        vertex_format.weight_values = (ctypes.c_ubyte * weights_array_size)(*weight_values)
        vertex_format.normal_x = round(vertex.normal[0] * 127)
        vertex_format.normal_y = round(vertex.normal[2] * 127) * -1
        vertex_format.normal_z = round(vertex.normal[1] * 127)
        vertex_format.normal_w = -1
        if VF == VertexFormat:
            vertex_format.tangent_x = -1
            vertex_format.tangent_y = -1
            vertex_format.tangent_z = -1
            vertex_format.tangent_w = -1
        vertex_format.uv_x = uvs_per_vertex[vertex_index][0] if uvs_per_vertex else 0
        vertex_format.uv_y = uvs_per_vertex[vertex_index][1] if uvs_per_vertex else 0
        if VF == VertexFormat:
            vertex_format.uv2_x = 0
            vertex_format.uv2_y = 0

    return VF, vertices_array


def _export_meshes(blender_objects, materials, bounding_box, saved_mod):
    """
    No weird optimization or sharing of offsets in the vertex buffer.
    All the same offsets, different positions like pl0200.mod from
    uPl01ShebaCos1.arc
    No time to investigate why and how those are decided. I suspect it might have to
    do with location of the meshes
    """
    blender_meshes_objects = [ob for ob in blender_objects if ob.type == 'MESH']
    meshes_156 = (Mesh156 * len(blender_meshes_objects))()
    vertex_buffer = bytearray()
    index_buffer = bytearray()

    vertex_position = 0
    face_position = 0
    for i, blender_mesh_object in enumerate(blender_meshes_objects):
        # XXX: if a model with more meshes than the original is exported... boom
        try:
            saved_mesh = saved_mod.meshes_array[i]
        except IndexError:
            raise ExportError('Exporting models with more meshes (parts) than the original not supported yet')
        blender_mesh = blender_mesh_object.data

        bpi = saved_mesh.bone_palette_index
        vertex_format, vertices_array = _export_vertices(blender_mesh_object,
                                                         bounding_box,
                                                         saved_mod.bone_palette_array[bpi].values[:])
        vertex_buffer.extend(vertices_array)
        # TODO: is all this format conversion necessary?
        triangle_strips_python = triangles_list_to_triangles_strip(blender_mesh)
        # mod156 use global indices for verts, in case one only mesh is needed, probably
        triangle_strips_python = [e + vertex_position for e in triangle_strips_python]
        triangle_strips_ctypes = (ctypes.c_ushort * len(triangle_strips_python))(*triangle_strips_python)
        index_buffer.extend(triangle_strips_ctypes)

        if vertex_format == VertexFormat0:
            vf = 0
        elif vertex_format == VertexFormat:
            vf = 1
        else:
            vf = 5

        vertex_count = len(blender_mesh.vertices)
        index_count = len(triangle_strips_python)

        m156 = meshes_156[i]
        m156.type = saved_mesh.type  # Needs to be investigated
        try:
            m156.material_index = materials.index(blender_mesh.materials[0])
        except IndexError:
            raise ExportError('Mesh {} has no materials'.format(blender_mesh.name))
        m156.unk_01 = 1  # all game models seem to have the value 1
        m156.level_of_detail = saved_mesh.level_of_detail  # TODO
        m156.unk_02 = 0  # most player models seem to have this value, needs research
        m156.vertex_format = vf
        m156.vertex_stride = 32
        m156.unk_03 = 0  # Most meshes use this value
        m156.unk_04 = 0
        m156.unk_05 = 110
        m156.vertex_count = vertex_count
        m156.vertex_index_end = vertex_position + vertex_count - 1
        m156.vertex_index_start_1 = vertex_position
        m156.vertex_offset = 0
        m156.unk_06 = 0  # Most models have this value
        m156.face_position = face_position
        m156.face_count = index_count
        m156.face_offset = 0
        m156.unk_07 = saved_mesh.unk_07
        m156.unk_08 = saved_mesh.unk_08
        m156.vertex_index_start_2 = vertex_position
        m156.unk_09 = saved_mesh.unk_09
        m156.bone_palette_index = saved_mesh.bone_palette_index  # XXX: improve, not guaranteed!
        m156.unk_10 = saved_mesh.unk_10
        m156.unk_11 = saved_mesh.unk_11
        m156.unk_12 = saved_mesh.unk_12
        m156.unk_13 = saved_mesh.unk_13

        vertex_position += vertex_count
        face_position += index_count
    vertex_buffer = (ctypes.c_ubyte * len(vertex_buffer)).from_buffer(vertex_buffer)
    index_buffer = (ctypes.c_ushort * (len(index_buffer) // 2)).from_buffer(index_buffer)

    return meshes_156, vertex_buffer, index_buffer


def _export_textures_and_materials(blender_objects, base_path=None, saved_mod=None):
    textures = get_textures_from_blender_objects(blender_objects)
    materials = get_materials_from_blender_objects(blender_objects)
    textures_array = ((ctypes.c_char * 64) * len(textures))()
    materials_data_array = (MaterialData * len(materials))()

    for i, texture in enumerate(textures):
        file_path = os.path.basename(texture.image.filepath)
        if len(file_path) > 64:
            # TODO: what if relative path are used?
            raise ExportError('File path to texture {} is longer than 64 characters'
                              .format(file_path))
        try:
            if base_path:
                file_path = os.path.join(base_path, file_path)
            file_path, _ = os.path.splitext(file_path)
            parts = file_path.split(os.path.sep)
            file_path = ntpath.join(*parts)
            file_path = file_path.encode('ascii')
            # TODO: there must be a better way instead of splitting bytes
            # TODO: when exporting to Arc, the mod should go in a defined folder
            textures_array[i] = (ctypes.c_char * 64)(*file_path)
        except UnicodeEncodeError:
            raise ExportError('Texture path {} is not in ascii'.format(file_path))

    for i, mat in enumerate(materials):
        material_data = MaterialData()
        try:
            # XXX: Should use data from actual blender material
            saved_mat = saved_mod.materials_data_array[i]
        except IndexError:
            raise ExportError('Exporting models with more materials than the original not supported yet')
        material_data.unk_01 = saved_mat.unk_01
        material_data.unk_02 = saved_mat.unk_02
        material_data.unk_03 = saved_mat.unk_03
        material_data.unk_04 = saved_mat.unk_04
        material_data.unk_05 = saved_mat.unk_05
        material_data.unk_06 = saved_mat.unk_06
        material_data.unk_07 = saved_mat.unk_07
        for texture_slot in mat.texture_slots:
            if not texture_slot:
                continue
            texture = texture_slot.texture
            # texture_indices expects index-1 based
            texture_index = textures.index(texture) + 1
            if texture_slot.use_map_normal and texture_slot.mapping != 'CUBE':
                material_data.texture_indices[1] = texture_index
            elif texture_slot.use_map_specular:
                material_data.texture_indices[2] = texture_index
            elif texture_slot.mapping == 'CUBE':
                material_data.texture_indices[7] = texture_index
            else:
                if not material_data.texture_indices[0]:
                    material_data.texture_indices[0] = texture_index
                else:
                    material_data.texture_indices[6] = texture_index
        materials_data_array[i] = material_data

    return textures_array, materials_data_array
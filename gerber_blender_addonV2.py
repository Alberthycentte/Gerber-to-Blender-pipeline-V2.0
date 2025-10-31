bl_info = {
    "name": "Gerber PCB Importer",
    "author": "PCB Tools",
    "version": (1, 0, 0),
    "blender": (3, 0, 0),
    "location": "File > Import > Gerber PCB",
    "description": "Import Gerber files as 3D PCB models with layer visualization",
    "category": "Import-Export",
}

import bpy
import bmesh
import os
import re
from mathutils import Vector
from bpy.props import (
    StringProperty,
    BoolProperty,
    FloatProperty,
    EnumProperty,
    CollectionProperty,
)
from bpy_extras.io_utils import ImportHelper

# Gerber parser utilities
class GerberParser:
    def __init__(self):
        self.apertures = {}
        self.current_aperture = None
        self.current_pos = [0.0, 0.0]
        self.paths = []
        self.flashes = []
        self.regions = []
        self.current_region = None
        self.unit_scale = 1.0  # mm
        self.format_spec = (2, 4)  # Default format
        
    def parse_aperture_definition(self, line):
        """Parse aperture definition like %ADD10C,0.254*%"""
        match = re.match(r'%ADD(\d+)([CRO])(.*?)\*%', line)
        if match:
            code = int(match.group(1))
            shape = match.group(2)
            params = match.group(3).split(',') if match.group(3) else []
            
            aperture = {'shape': shape, 'params': [float(p) for p in params if p]}
            self.apertures[code] = aperture
            
    def parse_coordinate(self, coord_str, axis):
        """Parse Gerber coordinate string"""
        if not coord_str:
            return self.current_pos[0 if axis == 'X' else 1]
        
        integer_digits, decimal_digits = self.format_spec
        total_digits = integer_digits + decimal_digits
        
        # Pad with leading zeros if needed
        coord_str = coord_str.zfill(total_digits)
        
        # Split into integer and decimal parts
        int_part = coord_str[:-decimal_digits] if decimal_digits > 0 else coord_str
        dec_part = coord_str[-decimal_digits:] if decimal_digits > 0 else '0'
        
        value = float(f"{int_part}.{dec_part}")
        return value * self.unit_scale
    
    def parse_file(self, filepath):
        """Parse a Gerber file"""
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        # Parse format specification
        format_match = re.search(r'%FSLAX(\d)(\d)Y(\d)(\d)\*%', content)
        if format_match:
            self.format_spec = (int(format_match.group(1)), int(format_match.group(2)))
        
        # Parse units
        if '%MOMM*%' in content:
            self.unit_scale = 1.0
        elif '%MOIN*%' in content:
            self.unit_scale = 25.4
        
        lines = content.split('\n')
        in_region = False
        
        for line in lines:
            line = line.strip()
            
            # Aperture definitions
            if line.startswith('%ADD'):
                self.parse_aperture_definition(line)
            
            # Aperture selection
            elif line.startswith('D') and line[1:].rstrip('*').isdigit():
                code = int(line[1:].rstrip('*'))
                if code >= 10:  # Aperture codes start at 10
                    self.current_aperture = code
            
            # Region mode
            elif line.startswith('G36'):
                in_region = True
                self.current_region = []
            elif line.startswith('G37'):
                if self.current_region:
                    self.regions.append(self.current_region)
                    self.current_region = None
                in_region = False
            
            # Operations (D01=draw, D02=move, D03=flash)
            elif 'D01' in line or 'D02' in line or 'D03' in line:
                x_match = re.search(r'X([+-]?\d+)', line)
                y_match = re.search(r'Y([+-]?\d+)', line)
                
                x = self.parse_coordinate(x_match.group(1) if x_match else None, 'X')
                y = self.parse_coordinate(y_match.group(1) if y_match else None, 'Y')
                
                if 'D01' in line:  # Draw
                    if in_region and self.current_region is not None:
                        self.current_region.append([x, y])
                    else:
                        self.paths.append({
                            'start': list(self.current_pos),
                            'end': [x, y],
                            'aperture': self.current_aperture
                        })
                elif 'D03' in line:  # Flash
                    self.flashes.append({
                        'pos': [x, y],
                        'aperture': self.current_aperture
                    })
                
                self.current_pos = [x, y]
        
        return {
            'paths': self.paths,
            'flashes': self.flashes,
            'regions': self.regions,
            'apertures': self.apertures
        }

class DrillParser:
    def __init__(self):
        self.tools = {}
        self.holes = []
        
    def parse_file(self, filepath):
        """Parse Excellon drill file"""
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        
        current_tool = None
        unit_scale = 25.4  # Default to inches
        
        for line in lines:
            line = line.strip()
            
            # Unit specification
            if line == 'METRIC':
                unit_scale = 1.0
            elif line == 'INCH':
                unit_scale = 25.4
            
            # Tool definition
            if line.startswith('T') and 'C' in line:
                match = re.match(r'T(\d+)C([\d.]+)', line)
                if match:
                    tool_num = int(match.group(1))
                    diameter = float(match.group(2)) * unit_scale
                    self.tools[tool_num] = diameter
            
            # Tool selection
            elif line.startswith('T') and line[1:].isdigit():
                current_tool = int(line[1:])
            
            # Hole coordinates
            elif line.startswith('X') and 'Y' in line:
                x_match = re.search(r'X([+-]?[\d.]+)', line)
                y_match = re.search(r'Y([+-]?[\d.]+)', line)
                if x_match and y_match and current_tool:
                    x = float(x_match.group(1)) * unit_scale
                    y = float(y_match.group(1)) * unit_scale
                    diameter = self.tools.get(current_tool, 0.8)
                    self.holes.append({'pos': [x, y], 'diameter': diameter})
        
        return self.holes

# Blender mesh creation
def create_pcb_layer(name, data, color, thickness, z_offset):
    """Create a mesh for a PCB layer"""
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    
    bm = bmesh.new()
    
    # Create paths
    for path in data.get('paths', []):
        aperture = data['apertures'].get(path['aperture'])
        if aperture:
            width = aperture['params'][0] if aperture['params'] else 0.254
            create_trace(bm, path['start'], path['end'], width, z_offset, thickness)
    
    # Create flashes (pads)
    for flash in data.get('flashes', []):
        aperture = data['apertures'].get(flash['aperture'])
        if aperture:
            if aperture['shape'] == 'C':  # Circle
                diameter = aperture['params'][0] if aperture['params'] else 0.254
                create_circular_pad(bm, flash['pos'], diameter, z_offset, thickness)
            elif aperture['shape'] == 'R':  # Rectangle
                width = aperture['params'][0] if len(aperture['params']) > 0 else 0.254
                height = aperture['params'][1] if len(aperture['params']) > 1 else width
                create_rectangular_pad(bm, flash['pos'], width, height, z_offset, thickness)
    
    # Create regions (filled areas)
    for region in data.get('regions', []):
        if len(region) > 2:
            create_region(bm, region, z_offset, thickness)
    
    bm.to_mesh(mesh)
    bm.free()
    
    # Add material
    mat = bpy.data.materials.new(name=f"{name}_mat")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    if bsdf:
        bsdf.inputs['Base Color'].default_value = color
        bsdf.inputs['Metallic'].default_value = 1.0 if 'copper' in name.lower() else 0.0
        bsdf.inputs['Roughness'].default_value = 0.2 if 'copper' in name.lower() else 0.5
    
    obj.data.materials.append(mat)
    return obj

def create_trace(bm, start, end, width, z, thickness):
    """Create a rectangular trace between two points"""
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = (dx**2 + dy**2)**0.5
    
    if length < 0.001:
        return
    
    # Perpendicular direction
    px = -dy / length * width / 2
    py = dx / length * width / 2
    
    # Create 8 vertices (4 top, 4 bottom)
    verts = [
        bm.verts.new((start[0] + px, start[1] + py, z)),
        bm.verts.new((start[0] - px, start[1] - py, z)),
        bm.verts.new((end[0] - px, end[1] - py, z)),
        bm.verts.new((end[0] + px, end[1] + py, z)),
        bm.verts.new((start[0] + px, start[1] + py, z + thickness)),
        bm.verts.new((start[0] - px, start[1] - py, z + thickness)),
        bm.verts.new((end[0] - px, end[1] - py, z + thickness)),
        bm.verts.new((end[0] + px, end[1] + py, z + thickness)),
    ]
    
    # Create faces
    bm.faces.new([verts[0], verts[1], verts[2], verts[3]])  # Bottom
    bm.faces.new([verts[4], verts[7], verts[6], verts[5]])  # Top
    bm.faces.new([verts[0], verts[4], verts[5], verts[1]])  # Side
    bm.faces.new([verts[1], verts[5], verts[6], verts[2]])  # Side
    bm.faces.new([verts[2], verts[6], verts[7], verts[3]])  # Side
    bm.faces.new([verts[3], verts[7], verts[4], verts[0]])  # Side

def create_circular_pad(bm, pos, diameter, z, thickness, segments=16):
    """Create a circular pad"""
    radius = diameter / 2
    import math
    
    bottom_verts = []
    top_verts = []
    
    for i in range(segments):
        angle = (i / segments) * 2 * math.pi
        x = pos[0] + radius * math.cos(angle)
        y = pos[1] + radius * math.sin(angle)
        bottom_verts.append(bm.verts.new((x, y, z)))
        top_verts.append(bm.verts.new((x, y, z + thickness)))
    
    # Create faces
    bm.faces.new(bottom_verts)
    bm.faces.new(list(reversed(top_verts)))
    
    for i in range(segments):
        next_i = (i + 1) % segments
        bm.faces.new([bottom_verts[i], bottom_verts[next_i], 
                     top_verts[next_i], top_verts[i]])

def create_rectangular_pad(bm, pos, width, height, z, thickness):
    """Create a rectangular pad"""
    hw = width / 2
    hh = height / 2
    
    verts = [
        bm.verts.new((pos[0] - hw, pos[1] - hh, z)),
        bm.verts.new((pos[0] + hw, pos[1] - hh, z)),
        bm.verts.new((pos[0] + hw, pos[1] + hh, z)),
        bm.verts.new((pos[0] - hw, pos[1] + hh, z)),
        bm.verts.new((pos[0] - hw, pos[1] - hh, z + thickness)),
        bm.verts.new((pos[0] + hw, pos[1] - hh, z + thickness)),
        bm.verts.new((pos[0] + hw, pos[1] + hh, z + thickness)),
        bm.verts.new((pos[0] - hw, pos[1] + hh, z + thickness)),
    ]
    
    bm.faces.new([verts[0], verts[1], verts[2], verts[3]])
    bm.faces.new([verts[4], verts[7], verts[6], verts[5]])
    bm.faces.new([verts[0], verts[4], verts[5], verts[1]])
    bm.faces.new([verts[1], verts[5], verts[6], verts[2]])
    bm.faces.new([verts[2], verts[6], verts[7], verts[3]])
    bm.faces.new([verts[3], verts[7], verts[4], verts[0]])

def create_region(bm, points, z, thickness):
    """Create a filled region (polygon)"""
    if len(points) < 3:
        return
    
    bottom_verts = [bm.verts.new((p[0], p[1], z)) for p in points]
    top_verts = [bm.verts.new((p[0], p[1], z + thickness)) for p in points]
    
    bm.faces.new(bottom_verts)
    bm.faces.new(list(reversed(top_verts)))
    
    for i in range(len(points)):
        next_i = (i + 1) % len(points)
        bm.faces.new([bottom_verts[i], bottom_verts[next_i], 
                     top_verts[next_i], top_verts[i]])

def create_drill_holes(holes, board_thickness, z_offset):
    """Create drill holes"""
    if not holes:
        return None
    
    mesh = bpy.data.meshes.new("Drill_Holes")
    obj = bpy.data.objects.new("Drill_Holes", mesh)
    bpy.context.collection.objects.link(obj)
    
    bm = bmesh.new()
    
    import math
    for hole in holes:
        radius = hole['diameter'] / 2
        segments = 16
        
        bottom_verts = []
        top_verts = []
        
        for i in range(segments):
            angle = (i / segments) * 2 * math.pi
            x = hole['pos'][0] + radius * math.cos(angle)
            y = hole['pos'][1] + radius * math.sin(angle)
            bottom_verts.append(bm.verts.new((x, y, z_offset)))
            top_verts.append(bm.verts.new((x, y, z_offset + board_thickness)))
        
        bm.faces.new(bottom_verts)
        bm.faces.new(list(reversed(top_verts)))
        
        for i in range(segments):
            next_i = (i + 1) % segments
            bm.faces.new([bottom_verts[i], bottom_verts[next_i], 
                         top_verts[next_i], top_verts[i]])
    
    bm.to_mesh(mesh)
    bm.free()
    
    # Material
    mat = bpy.data.materials.new(name="Drill_mat")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    if bsdf:
        bsdf.inputs['Base Color'].default_value = (0.05, 0.05, 0.05, 1.0)
        bsdf.inputs['Metallic'].default_value = 0.8
    
    obj.data.materials.append(mat)
    return obj

# Operator
class ImportGerber(bpy.types.Operator, ImportHelper):
    bl_idname = "import_pcb.gerber"
    bl_label = "Import Gerber PCB"
    bl_options = {'REGISTER', 'UNDO'}
    
    filename_ext = ".gbr"
    filter_glob: StringProperty(default="*.gbr;*.gtl;*.gbl;*.gts;*.gbs;*.gto;*.gbo;*.drl", options={'HIDDEN'})
    directory: StringProperty(subtype='DIR_PATH')
    files: CollectionProperty(type=bpy.types.OperatorFileListElement)
    
    copper_thickness: FloatProperty(name="Copper Thickness", default=0.035, min=0.001, max=1.0, unit='LENGTH')
    board_thickness: FloatProperty(name="Board Thickness", default=1.6, min=0.1, max=10.0, unit='LENGTH')
    soldermask_thickness: FloatProperty(name="Soldermask Thickness", default=0.025, min=0.001, max=0.5, unit='LENGTH')
    silkscreen_thickness: FloatProperty(name="Silkscreen Thickness", default=0.020, min=0.001, max=0.5, unit='LENGTH')
    
    import_top_copper: BoolProperty(name="Import Top Copper", default=True)
    import_bottom_copper: BoolProperty(name="Import Bottom Copper", default=True)
    import_top_soldermask: BoolProperty(name="Import Top Soldermask", default=True)
    import_bottom_soldermask: BoolProperty(name="Import Bottom Soldermask", default=True)
    import_top_silkscreen: BoolProperty(name="Import Top Silkscreen", default=True)
    import_bottom_silkscreen: BoolProperty(name="Import Bottom Silkscreen", default=True)
    import_drills: BoolProperty(name="Import Drill Holes", default=True)
    
    def draw(self, context):
        layout = self.layout
        
        box = layout.box()
        box.label(text="Layer Thickness:")
        box.prop(self, "board_thickness")
        box.prop(self, "copper_thickness")
        box.prop(self, "soldermask_thickness")
        box.prop(self, "silkscreen_thickness")
        
        box = layout.box()
        box.label(text="Layers to Import:")
        box.prop(self, "import_top_copper")
        box.prop(self, "import_bottom_copper")
        box.prop(self, "import_top_soldermask")
        box.prop(self, "import_bottom_soldermask")
        box.prop(self, "import_top_silkscreen")
        box.prop(self, "import_bottom_silkscreen")
        box.prop(self, "import_drills")
    
    def execute(self, context):
        if not self.files:
            self.report({'ERROR'}, "No files selected")
            return {'CANCELLED'}
        
        # Layer mapping (common file extensions)
        layer_map = {
            'gtl': ('top_copper', (0.8, 0.5, 0.2, 1.0)),
            'gbl': ('bottom_copper', (0.8, 0.5, 0.2, 1.0)),
            'gts': ('top_soldermask', (0.0, 0.3, 0.0, 0.8)),
            'gbs': ('bottom_soldermask', (0.0, 0.3, 0.0, 0.8)),
            'gto': ('top_silkscreen', (1.0, 1.0, 1.0, 1.0)),
            'gbo': ('bottom_silkscreen', (1.0, 1.0, 1.0, 1.0)),
        }
        
        layers = {}
        drill_file = None
        
        # Parse all files
        for file_elem in self.files:
            filepath = os.path.join(self.directory, file_elem.name)
            ext = file_elem.name.split('.')[-1].lower()
            
            if ext == 'drl' or 'drill' in file_elem.name.lower():
                drill_file = filepath
            elif ext in layer_map:
                layer_name, color = layer_map[ext]
                parser = GerberParser()
                layers[layer_name] = {'data': parser.parse_file(filepath), 'color': color}
        
        if not layers and not drill_file:
            self.report({'ERROR'}, "No valid Gerber or drill files found")
            return {'CANCELLED'}
        
        # Create collection for PCB
        pcb_collection = bpy.data.collections.new("PCB_Import")
        bpy.context.scene.collection.children.link(pcb_collection)
        
        # Layer Z positions
        z_positions = {
            'bottom_copper': 0.0,
            'bottom_soldermask': self.copper_thickness,
            'top_copper': self.board_thickness,
            'top_soldermask': self.board_thickness + self.copper_thickness,
            'bottom_silkscreen': self.copper_thickness + self.soldermask_thickness,
            'top_silkscreen': self.board_thickness + self.copper_thickness + self.soldermask_thickness,
        }
        
        layer_thickness = {
            'bottom_copper': self.copper_thickness,
            'top_copper': self.copper_thickness,
            'bottom_soldermask': self.soldermask_thickness,
            'top_soldermask': self.soldermask_thickness,
            'bottom_silkscreen': self.silkscreen_thickness,
            'top_silkscreen': self.silkscreen_thickness,
        }
        
        # Create layers
        import_flags = {
            'top_copper': self.import_top_copper,
            'bottom_copper': self.import_bottom_copper,
            'top_soldermask': self.import_top_soldermask,
            'bottom_soldermask': self.import_bottom_soldermask,
            'top_silkscreen': self.import_top_silkscreen,
            'bottom_silkscreen': self.import_bottom_silkscreen,
        }
        
        for layer_name, layer_info in layers.items():
            if import_flags.get(layer_name, False):
                obj = create_pcb_layer(
                    layer_name.replace('_', ' ').title(),
                    layer_info['data'],
                    layer_info['color'],
                    layer_thickness[layer_name],
                    z_positions[layer_name]
                )
                pcb_collection.objects.link(obj)
                bpy.context.collection.objects.unlink(obj)
        
        # Create drill holes
        if drill_file and self.import_drills:
            drill_parser = DrillParser()
            holes = drill_parser.parse_file(drill_file)
            if holes:
                obj = create_drill_holes(holes, self.board_thickness + 2 * self.copper_thickness, 0.0)
                if obj:
                    pcb_collection.objects.link(obj)
                    bpy.context.collection.objects.unlink(obj)
        
        # Create substrate (FR4 board)
        bpy.ops.mesh.primitive_cube_add()
        substrate = bpy.context.active_object
        substrate.name = "PCB_Substrate"
        
        # Calculate board bounds from all layers
        min_x, max_x = float('inf'), float('-inf')
        min_y, max_y = float('inf'), float('-inf')
        
        for layer_info in layers.values():
            for path in layer_info['data'].get('paths', []):
                for point in [path['start'], path['end']]:
                    min_x = min(min_x, point[0])
                    max_x = max(max_x, point[0])
                    min_y = min(min_y, point[1])
                    max_y = max(max_y, point[1])
        
        if min_x != float('inf'):
            center_x = (min_x + max_x) / 2
            center_y = (min_y + max_y) / 2
            width = max_x - min_x + 5  # Add margin
            height = max_y - min_y + 5
            
            substrate.location = (center_x, center_y, self.board_thickness / 2)
            substrate.scale = (width / 2, height / 2, self.board_thickness / 2)
        
        # FR4 material
        mat = bpy.data.materials.new(name="FR4")
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get('Principled BSDF')
        if bsdf:
            bsdf.inputs['Base Color'].default_value = (0.2, 0.25, 0.15, 1.0)
            bsdf.inputs['Roughness'].default_value = 0.4
        
        substrate.data.materials.append(mat)
        pcb_collection.objects.link(substrate)
        bpy.context.collection.objects.unlink(substrate)
        
        self.report({'INFO'}, f"Imported {len(layers)} layers successfully")
        return {'FINISHED'}

def menu_func_import(self, context):
    self.layout.operator(ImportGerber.bl_idname, text="Gerber PCB (.gbr)")

def register():
    bpy.utils.register_class(ImportGerber)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)

def unregister():
    bpy.utils.unregister_class(ImportGerber)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)

if __name__ == "__main__":
    register()
import pygame
import numpy as np
from PIL import Image
import sys
import tkinter as tk
from tkinter import filedialog
import math
import os
import struct
import json
from OpenGL.GL import *
from OpenGL.GLU import *

pygame.init()

# ---------------- CONFIG ----------------
pygame.key.set_repeat(0)  # disable OS key repeat
brush_cache = {}

pygame.display.set_allow_screensaver(False)

WINDOW_W, WINDOW_H = 1600, 900
FONT = pygame.font.SysFont("consolas", 24)
SMALL_FONT = pygame.font.SysFont("consolas", 18)
TITLE_FONT = pygame.font.SysFont("consolas", 64)

UI_PANEL_W = 260
UI_PADDING = 16

WHITE = (240, 240, 240)
GRAY = (40, 40, 40)
DARK = (13, 14, 14)
BLUE = (17, 55, 104)

#base_terrain = 0
rgb = False

CANVAS_MARGIN = 40

zoom = 1.0
#MIN_ZOOM = 0.2
MAX_ZOOM = 18.0

last_zoom = None
cached_scaled_surface = None

view_x = 0.0
view_y = 0.0

panning = False
pan_start_mouse = (0, 0)
pan_start_view = (0.0, 0.0)

CANVAS_PADDING = 20

brush_radius = 20
brush_strength = 0.5  # 0..1

noise_scale = 0.0
noise_octaves = 4
noise_persistence = 0.5
noise_seed = 56

max_height = 255
min_height = 0

brush_power = 3.0

brush_spacing = 0.45  # fraction of radius

last_paint_pos = None
last_stamp_pos = None

painting = False

erosion = False

procedural_noise = False

beach_offset = 0

canvas_x = 0
canvas_y = 0
canvas_w = 0
canvas_h = 0

active_input = None
input_buffers = {
    "radius": "",
    "strength": "",
    "power": "",
    "max_h": "",
    "min_h": "",
}

noise_settings = {
    "enabled": True,

    # placement
    "primary_spacing": 32,      # distance between main features
    "secondary_spacing": 12,    # smaller details

    # radii
    "primary_radius": 18,
    "secondary_radius": 8,

    # strength
    "primary_strength": 0.6,
    "secondary_strength": 0.25,

    # randomness
    "jitter": 0.35,             # position randomness (0–1)
    "strength_jitter": 0.3,     # per-point strength variance

    # layering
    "octaves": 2,               # how many passes
    "falloff": 0.6,             # strength multiplier per octave

    # direction
    "direction": 1              # 1 = add height, -1 = erode
}

point_features = {
    "primary": {
        "spacing": 112,
        "radius": 120,
        "strength": 0.6,
        "power": 3.0,
        "density": 0.85,
        "jitter": 0.4,
        "count": 1
    },

    "secondary": {
        "spacing": 74,
        "radius": 82,
        "strength": 0.25,
        "power": 2.2,
        "density": 7.0,
        "jitter": 0.6,
        "count": 2
    },

    "tertiary": {
        "spacing": 18,
        "radius": 6,
        "strength": 0.3,
        "power": 3.0,
        "density": 0.25,
        "jitter": 0.7,
        "count": 1
    }
}
seed = 67
# ---------------- STATES ----------------
STATE_MENU = "menu"
STATE_SIZE = "size"
STATE_EDITOR = "editor"

right_panel = False

state = STATE_MENU

MODE_HEIGHT = "height"
MODE_OVERHANG = "overhang"
MODE_BLOCK = "block"

paint_mode = MODE_HEIGHT

raise_mode = False

NOISE_STAMP = "stamp"
NOISE_VALUE = "value"
NOISE_POINTS = "points"

noise_mode = NOISE_POINTS

layer_mode = "primary"

terrain_dirty = True
cached_surface = None
noise_queue = []

CHUNK_SIZE = 256

chunk_surfaces = {}   # (cx, cy) -> pygame.Surface
chunk_dirty = set()   # chunks needing rebuild

# ---------------- WINDOW ----------------

red = True

# ---------------- TERRAIN DATA ----------------
terrain = None
terrain_w = 0
terrain_h = 0

lod = np.empty(20, dtype = int)
# ---------------- UI HELPERS ----------------

class IsometricPreview:
    def __init__(self):
        self.active = False
        self.yaw = 45
        self.pitch = 35.264
        self.display_list = None
        self.ortho_scale = 1.0
        self.pan_x = 0
        self.pan_y = 0
        self.rmb_down = False
        self.cx = 0
        self.cy = 0
        self.chunk_size = 512

    def toggle(self):
        global screen
        self.active = not self.active
        if self.active:
            pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
            pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
            screen = pygame.display.set_mode((WINDOW_W, WINDOW_H), pygame.OPENGL | pygame.DOUBLEBUF | pygame.RESIZABLE)
            pygame.display.set_caption("3D Preview - RMB: Rotate, Wheel: Zoom, WASD: Pan, ESC: Exit")
            
            self.init_opengl()
            
            # Center camera
            self.pan_x = terrain_w / 2
            self.pan_y = terrain_h / 2
            # Determine initial chunk from editor view
            try:
                # Calculate center of view in terrain coordinates
                center_x = view_x + (canvas_w / 2) / zoom
                center_y = view_y + (canvas_h / 2) / zoom
                self.cx = int(center_x // self.chunk_size)
                self.cy = int(center_y // self.chunk_size)
            except:
                self.cx = 0
                self.cy = 0
            
            # Fit terrain
            self.ortho_scale = max(terrain_w / WINDOW_W, terrain_h / WINDOW_H) * 1.5
            # Clamp to valid chunks
            max_cx = max(0, (terrain_w - 1) // self.chunk_size)
            max_cy = max(0, (terrain_h - 1) // self.chunk_size)
            self.cx = max(0, min(max_cx, self.cx))
            self.cy = max(0, min(max_cy, self.cy))
            
            # Set zoom to see roughly 3 chunks width
            self.ortho_scale = (self.chunk_size * 3.0) / WINDOW_W
            
            self.update_pan_from_chunk()
            
            self.build_terrain_mesh()
            
            pygame.mouse.set_visible(True)
        else:
            screen = pygame.display.set_mode((WINDOW_W, WINDOW_H), pygame.RESIZABLE)
            pygame.display.set_caption("Godot Terrain Map Editor")
            try:
                pygame.display.set_icon(pygame.image.load("terrain_tools.ico"))
            except Exception:
                pass
            
            layout_ui()

    def update_pan_from_chunk(self):
        self.pan_x = self.cx * self.chunk_size + self.chunk_size / 2
        self.pan_y = self.cy * self.chunk_size + self.chunk_size / 2
        pygame.display.set_caption(f"3D Preview - Chunk: {self.cx},{self.cy} - Arrows: Navigate Chunks, RMB: Rotate, Wheel: Zoom, WASD: Pan, ESC: Exit")

    def init_opengl(self):
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_LIGHTING)
        glEnable(GL_LIGHT0)
        glEnable(GL_COLOR_MATERIAL)
        glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)
        
        glLightfv(GL_LIGHT0, GL_POSITION,  (1.0, 1.0, 1.0, 0.0))
        glLightfv(GL_LIGHT0, GL_DIFFUSE, (1.0, 1.0, 0.95, 1.0))
        glLightfv(GL_LIGHT0, GL_AMBIENT, (0.3, 0.3, 0.35, 1.0))
        
        glClearColor(0.05, 0.07, 0.1, 1.0)
        self.update_viewport()

    def update_viewport(self):
        glViewport(0, 0, WINDOW_W, WINDOW_H)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        
        if WINDOW_H == 0: return
        
        w = self.ortho_scale * WINDOW_W / 2
        h = self.ortho_scale * WINDOW_H / 2
        
        glOrtho(-w, w, -h, h, -5000, 5000)
        glMatrixMode(GL_MODELVIEW)

    def build_terrain_mesh(self):
        if self.display_list is not None:
            glDeleteLists(self.display_list, 1)
        
        self.display_list = glGenLists(1)
        glNewList(self.display_list, GL_COMPILE)
        
        h, w = terrain.shape[:2]
        
        # Prepare data
        heights = terrain[:, :, 3].astype(np.float32)
        
        # Calculate normals
        gy, gx = np.gradient(heights * 0.4)
        norm = np.sqrt(gx**2 + gy**2 + 1.0)
        nx = -gx / norm
        ny = 1.0 / norm
        nz = -gy / norm
        
        # Render ONLY the current chunk
        x0 = self.cx * self.chunk_size
        y0 = self.cy * self.chunk_size
        x1 = min(x0 + self.chunk_size, w)
        y1 = min(y0 + self.chunk_size, h)
        
        step = 1
        stop_y = y1 if y1 < h else h - 1
        stop_x = x1 if x1 < w else w - 1
        
        for y in range(y0, stop_y, step):
            glBegin(GL_TRIANGLE_STRIP)
            for x in range(x0, stop_x, step):
                h1 = heights[y, x]
                h2 = heights[y + step, x]
                
                glColor3f(*self.get_color(h1))
                glNormal3f(nx[y, x], ny[y, x], nz[y, x])
                glVertex3f(x, h1 * 0.4, y)
                
                glColor3f(*self.get_color(h2))
                glNormal3f(nx[y + step, x], ny[y + step, x], nz[y + step, x])
                glVertex3f(x, h2 * 0.4, y + step)
            glEnd()
        glEndList()

    def get_color(self, h):
        if h < 40: return (0.1, 0.3, 0.8)
        if h < 50: return (0.8, 0.75, 0.55)
        if h < 140: return (0.2, 0.55, 0.2)
        if h < 200: return (0.45, 0.45, 0.48)
        return (0.95, 0.95, 1.0)

    def render(self, target_screen, terrain_data):
        self.process_input()
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glLoadIdentity()
        
        glRotatef(self.pitch, 1, 0, 0)
        glRotatef(self.yaw, 0, 1, 0)
        glTranslatef(-self.pan_x, 0, -self.pan_y)
        
        if self.display_list:
            glCallList(self.display_list)

    def process_input(self):
        keys = pygame.key.get_pressed()
        speed = 15.0 * self.ortho_scale
        
        rad_yaw = math.radians(self.yaw)
        c = math.cos(rad_yaw)
        s = math.sin(rad_yaw)
        
        dx = 0
        dz = 0
        
        if keys[pygame.K_w]: dz -= speed
        if keys[pygame.K_s]: dz += speed
        if keys[pygame.K_a]: dx -= speed
        if keys[pygame.K_d]: dx += speed
        
        self.pan_x += dx * c - dz * s
        self.pan_y += dx * s + dz * c

    def handle_input(self, event):
        if not self.active: return False
        
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.toggle()
                return True
            
            # Chunk navigation
            moved = False
            if event.key == pygame.K_LEFT:
                self.cx -= 1
                moved = True
            elif event.key == pygame.K_RIGHT:
                self.cx += 1
                moved = True
            elif event.key == pygame.K_UP:
                self.cy -= 1
                moved = True
            elif event.key == pygame.K_DOWN:
                self.cy += 1
                moved = True
            
            if moved:
                max_cx = max(0, (terrain_w - 1) // self.chunk_size)
                max_cy = max(0, (terrain_h - 1) // self.chunk_size)
                self.cx = max(0, min(max_cx, self.cx))
                self.cy = max(0, min(max_cy, self.cy))
                self.update_pan_from_chunk()
                self.build_terrain_mesh()
                return True
        
        if event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 3: # RMB
                self.rmb_down = True
                pygame.event.set_grab(True)
                pygame.mouse.set_visible(False)
            elif event.button == 4: # Wheel Up
                self.ortho_scale = max(0.05, self.ortho_scale * 0.9)
                self.update_viewport()
            elif event.button == 5: # Wheel Down
                self.ortho_scale = min(20.0, self.ortho_scale * 1.1)
                self.update_viewport()
        
        if event.type == pygame.MOUSEBUTTONUP:
            if event.button == 3:
                self.rmb_down = False
                pygame.event.set_grab(False)
                pygame.mouse.set_visible(True)
        
        if event.type == pygame.MOUSEMOTION and self.rmb_down:
            self.yaw += event.rel[0] * 0.5
            self.pitch += event.rel[1] * 0.5
            self.pitch = max(0, min(90, self.pitch))
            
        if event.type == pygame.VIDEORESIZE:
            self.update_viewport()
            
        return False

iso_preview = IsometricPreview()

def draw_button(rect, text):
    pygame.draw.rect(screen, BLUE, rect)
    label = FONT.render(text, True, WHITE)
    screen.blit(label, label.get_rect(center=rect.center))

def draw_mode_button(rect, text, active):
    color = (17, 55, 104) if active else (60, 60, 60)
    pygame.draw.rect(screen, color, rect)
    pygame.draw.rect(screen, (120,120,120), rect, 2)
    txt = SMALL_FONT.render(text, True, WHITE)
    screen.blit(txt, txt.get_rect(center=rect.center))

def draw_toggle_button(rect, text, active):
    color = (17, 55, 104) if active else (60, 60, 60)
    pygame.draw.rect(screen, color, rect)
    pygame.draw.rect(screen, (120,120,120), rect, 2)
    txt = SMALL_FONT.render(text, True, WHITE)
    screen.blit(txt, txt.get_rect(center=rect.center))

def button_clicked(rect, mouse_pos, mouse_down):
    return rect.collidepoint(mouse_pos) and mouse_down

def draw_text_input(rect, text, active):
    color = BLUE if active else GRAY
    pygame.draw.rect(screen, color, rect, 2)
    label = FONT.render(text, True, WHITE)
    screen.blit(label, (rect.x + 5, rect.y + 5))

def open_file_dialog():
    root = tk.Tk()
    root.withdraw()
    file_path = filedialog.askopenfilename(
        title="Open Terrain Image",
        filetypes=[("PNG Images", "*.png")]
    )
    root.destroy()
    return file_path

def save_file_dialog():
    root = tk.Tk()
    root.withdraw()
    path = filedialog.asksaveasfilename(
        title="Save Terrain",
        defaultextension="", # No default extension, as we're saving a folder
        filetypes=[("", "")]
    )
    root.destroy()
    return path

def open_project_dialog():
    root = tk.Tk()
    root.withdraw()
    folder_path = filedialog.askdirectory(
        title="Open Terrain Project Folder"
    )
    root.destroy()
    return folder_path

def average_data(data, lod):
    pass

def rearrange_data(terrain, lod):
    pass

def load_terrain_from_project(project_path):
    global terrain_w, terrain_h
    map_path = os.path.join(project_path, "map")
    if not os.path.isdir(map_path):
        print(f"Error: 'map' subfolder not found in {project_path}")
        return None

    # Load width.json to interpret linear indices
    width_json_path = os.path.join(map_path, "width.json")
    if not os.path.exists(width_json_path):
        print(f"Error: 'width.json' not found in {map_path}")
        return None
    
    with open(width_json_path, 'r') as f:
        width_data = json.load(f)
        num_chunks_x = width_data.get("width", 0)

    chunk_files = [f for f in os.listdir(map_path) if f.endswith(".hmap")]
    if not chunk_files:
        print(f"Error: No .hmap chunk files found in {map_path}")
        return None

    max_cx = -1
    max_cy = -1
    chunks = {}
    CHUNK_EXTRACT_SIZE = 64

    for filename in chunk_files:
        try:
            index_str = os.path.splitext(filename)[0]
            if not index_str.isdigit(): continue
            
            idx_0 = int(index_str) - 1
            cx = idx_0 % num_chunks_x
            cy = idx_0 // num_chunks_x
            
            max_cx = max(max_cx, cx)
            max_cy = max(max_cy, cy)
            
            chunk_path = os.path.join(map_path, filename)
            with open(chunk_path, 'rb') as f:
                chunk_h = CHUNK_EXTRACT_SIZE
                chunk_w = CHUNK_EXTRACT_SIZE
                
                raw_data = f.read(chunk_w * chunk_h * 2)
                chunk_data = np.frombuffer(raw_data, dtype=np.uint16).reshape((chunk_h, chunk_w))
                chunks[(cx, cy)] = chunk_data
        except (ValueError, IndexError, struct.error) as e:
            print(f"Warning: Could not read or parse chunk file '{filename}': {e}")
            continue
    
    if max_cx == -1 or max_cy == -1:
        print("Error: No valid map chunk files found.")
        return None

    terrain_w = (max_cx + 1) * CHUNK_EXTRACT_SIZE
    terrain_h = (max_cy + 1) * CHUNK_EXTRACT_SIZE

    new_terrain = np.zeros((terrain_h, terrain_w, 4), dtype=np.uint16)

    for (cx, cy), chunk_data in chunks.items():
        x0 = cx * CHUNK_EXTRACT_SIZE
        y0 = cy * CHUNK_EXTRACT_SIZE
        h, w = chunk_data.shape
        new_terrain[y0:y0+h, x0:x0+w, 3] = chunk_data

    # Standard dirty-marking logic
    mark_all_dirty()
    
    print(f"Loaded project from {project_path}")
    return new_terrain

def create_new_terrain(w, h):
    data = np.zeros((h, w, 4), dtype=np.uint16)
    data[:, :, 3] = base_height
    chunk_surfaces.clear()

    chunk_dirty.clear()

    for cy in range((terrain_h + CHUNK_SIZE - 1) // CHUNK_SIZE):
        for cx in range((terrain_w + CHUNK_SIZE - 1) // CHUNK_SIZE):
            chunk_dirty.add((cx, cy))

    return data

def load_terrain(path):
    global terrain_w, terrain_h
    img = Image.open(path).convert("RGBA")
    arr = np.array(img).astype(np.uint16)
    
    # Update global dimensions based on the new image
    terrain_h, terrain_w = arr.shape[:2]
    
    # Reset and mark all chunks as dirty so they rebuild with the new data
    chunk_surfaces.clear()
    chunk_dirty.clear()
    for cy in range((terrain_h + CHUNK_SIZE - 1) // CHUNK_SIZE):
        for cx in range((terrain_w + CHUNK_SIZE - 1) // CHUNK_SIZE):
            chunk_dirty.add((cx, cy))
            
    return arr

# Shader for generating importance map
IMP_VS = """#version 120
void main() {
    gl_Position = gl_Vertex;
}
"""

IMP_FS = """#version 120
uniform sampler2D terrainTex;
uniform vec2 chunkOffset;
uniform vec2 texSize;

void main() {
    vec2 localPos = gl_FragCoord.xy - 0.5;
    
    // Mask boundary vertices of the chunk (0 and 63)
    if (localPos.x < 1.0 || localPos.x > 62.0 || localPos.y < 1.0 || localPos.y > 62.0) {
        gl_FragColor = vec4(0.0);
        return;
    }
    
    vec2 onePixel = 1.0 / texSize;
    vec2 uv = (chunkOffset + localPos + 0.5) / texSize;
    
    // Sample neighbors
    float hc = texture2D(terrainTex, uv).a * 65535.0;
    float hl = texture2D(terrainTex, uv + vec2(-onePixel.x, 0.0)).a * 65535.0;
    float hr = texture2D(terrainTex, uv + vec2( +onePixel.x, 0.0)).a * 65535.0;
    float hd = texture2D(terrainTex, uv + vec2(0.0, -onePixel.y)).a * 65535.0;
    float hu = texture2D(terrainTex, uv + vec2(0.0,  +onePixel.y)).a * 65535.0;
    
    // Calculate internal angles
    // X-axis angle (Center to Left, Center to Right)
    vec3 v_cl = normalize(vec3(-1.0, hl - hc, 0.0));
    vec3 v_cr = normalize(vec3( 1.0, hr - hc, 0.0));
    float angleX = degrees(acos(clamp(dot(v_cl, v_cr), -1.0, 1.0)));
    
    // Y-axis angle
    vec3 v_cd = normalize(vec3(0.0, hd - hc, -1.0));
    vec3 v_cu = normalize(vec3(0.0, hu - hc,  1.0));
    float angleY = degrees(acos(clamp(dot(v_cd, v_cu), -1.0, 1.0)));
    
    // Map angles to importance values
    float valX = 0.0;
    if (angleX > 177.0) valX = 1.0;
    else if (angleX > 170.0) valX = 2.0;
    else if (angleX > 150.0) valX = 3.0;
    else if (angleX <= 150.0) valX = 4.0;

    float valY = 0.0;
    if (angleY > 177.0) valY = 1.0;
    else if (angleY > 170.0) valY = 2.0;
    else if (angleY > 150.0) valY = 3.0;
    else if (angleY <= 150.0) valY = 4.0;
    
    // Combine both axes and scale for 8-bit integer output
    float finalVal = max(valX, valY);
    gl_FragColor = vec4(finalVal / 255.0, 0.0, 0.0, 1.0);
}
"""

def save_terrain(base_path, terrain_full_res):
    """
    Exports terrain data into a folder structure with different LOD levels and individual chunks.
    Two subfolders are created: 'map' and 'importance'.
    64x64 chunks are saved as separate .hmap files.
    The 'importance' map is generated on the GPU based on vertex angles.
    """
    global screen
    CHUNK_EXTRACT_SIZE = 64

    # Extract directory and desired folder name from the base_path
    # The save_file_dialog returns a path with a suggested filename, e.g., "my_terrain.hmap"
    # We want to create a folder named "my_terrain"
    export_dir = os.path.dirname(base_path)
    folder_name = os.path.splitext(os.path.basename(base_path))[0]
    full_export_path = os.path.join(export_dir, folder_name)

    # Create the base export directory
    if not os.path.exists(full_export_path):
        os.makedirs(full_export_path)

    # Create subfolders
    map_folder = os.path.join(full_export_path, "map")
    importance_folder = os.path.join(full_export_path, "importance")
    
    if not os.path.exists(map_folder):
        os.makedirs(map_folder)
    if not os.path.exists(importance_folder):
        os.makedirs(importance_folder)

    current_h, current_w = terrain_full_res.shape[:2]
    
    # --- GPU INITIALIZATION ---
    # Switch to OpenGL context for processing
    try:
        pygame.display.set_mode((WINDOW_W, WINDOW_H), pygame.OPENGL | pygame.DOUBLEBUF)
        glViewport(0, 0, 64, 64) # Set viewport to chunk size

        # Compile Shader
        vs = glCreateShader(GL_VERTEX_SHADER)
        glShaderSource(vs, IMP_VS)
        glCompileShader(vs)
        
        fs = glCreateShader(GL_FRAGMENT_SHADER)
        glShaderSource(fs, IMP_FS)
        glCompileShader(fs)
        
        program = glCreateProgram()
        glAttachShader(program, vs)
        glAttachShader(program, fs)
        glLinkProgram(program)
        glUseProgram(program)
        
        glDeleteShader(vs)
        glDeleteShader(fs)

        # Create and Upload Terrain Texture
        tex_id = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, tex_id)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        
        # Upload full terrain (RGBA)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA16, current_w, current_h, 0, GL_RGBA, GL_UNSIGNED_SHORT, terrain_full_res.tobytes())

        # Setup FBO
        fbo = glGenFramebuffers(1)
        glBindFramebuffer(GL_FRAMEBUFFER, fbo)
        
        res_tex = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, res_tex)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_R8, CHUNK_EXTRACT_SIZE, CHUNK_EXTRACT_SIZE, 0, GL_RED, GL_UNSIGNED_BYTE, None)
        
        # Re-bind terrain texture to Unit 0 so the shader can sample it
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, tex_id)

        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, res_tex, 0)
        
        # Uniforms
        loc_terrainTex = glGetUniformLocation(program, "terrainTex")
        loc_texSize = glGetUniformLocation(program, "texSize")
        loc_offset = glGetUniformLocation(program, "chunkOffset")
        glUniform1i(loc_terrainTex, 0)
        glUniform2f(loc_texSize, current_w, current_h)

    # Determine the number of chunks 
        num_chunks_x = math.ceil(current_w / CHUNK_EXTRACT_SIZE)
        num_chunks_y = math.ceil(current_h / CHUNK_EXTRACT_SIZE)
        total_chunks = num_chunks_x * num_chunks_y

        # Export width configuration
        with open(os.path.join(map_folder, "width.json"), "w") as f:
            json.dump({
                "width": num_chunks_x,
                "total_chunks": total_chunks
            }, f)

        for cy in range(num_chunks_y):
            for cx in range(num_chunks_x):
                chunk_index = cy * num_chunks_x + cx + 1
                chunk_filename = f"{chunk_index}.hmap"

                # 1. Save Map Chunk (CPU)
                chunk_alpha_data_1d = get_chunk_alpha_data(terrain_full_res, cx, cy)
                with open(os.path.join(map_folder, chunk_filename), 'wb') as f:
                    f.write(chunk_alpha_data_1d.tobytes())

                # 2. Generate Importance Chunk (GPU)
                glUniform2f(loc_offset, cx * CHUNK_EXTRACT_SIZE, cy * CHUNK_EXTRACT_SIZE)
                glBegin(GL_QUADS)
                glVertex2f(-1, -1); glVertex2f( 1, -1); glVertex2f( 1,  1); glVertex2f(-1,  1)
                glEnd()
                
                gpu_data = glReadPixels(0, 0, CHUNK_EXTRACT_SIZE, CHUNK_EXTRACT_SIZE, GL_RED, GL_UNSIGNED_BYTE)
                with open(os.path.join(importance_folder, chunk_filename), 'wb') as f:
                    f.write(gpu_data)

        # Cleanup GL
        glDeleteTextures([tex_id, res_tex])
        glDeleteFramebuffers(1, [fbo])
        glDeleteProgram(program)
        
    finally:
        # Restore Software Mode
        screen = pygame.display.set_mode((WINDOW_W, WINDOW_H), pygame.RESIZABLE)

    print(f"Exported 'map' and 'importance' with {num_chunks_x * num_chunks_y} chunks to {full_export_path}")

def get_chunk_alpha_data(terrain_data, cx, cy):
    CHUNK_EXTRACT_SIZE = 64

    current_h, current_w = terrain_data.shape[:2]


    x0 = cx * CHUNK_EXTRACT_SIZE
    y0 = cy * CHUNK_EXTRACT_SIZE
    
    x1 = min(x0 + CHUNK_EXTRACT_SIZE, current_w)
    y1 = min(y0 + CHUNK_EXTRACT_SIZE, current_h)
    
    if terrain_data.ndim == 3 and terrain_data.shape[2] == 4:
        partial_data = terrain_data[y0:y1, x0:x1, 3]
    elif terrain_data.ndim == 2: 
        partial_data = terrain_data[y0:y1, x0:x1]
    else:
        raise ValueError("Unsupported terrain_data format in get_chunk_alpha_data: expected 2D or 4-channel 3D array.")
    
    actual_h, actual_w = partial_data.shape
    if actual_h < CHUNK_EXTRACT_SIZE or actual_w < CHUNK_EXTRACT_SIZE:
        padded_chunk = np.zeros((CHUNK_EXTRACT_SIZE, CHUNK_EXTRACT_SIZE), dtype=np.uint16)
        padded_chunk[0:actual_h, 0:actual_w] = partial_data
        return padded_chunk.ravel()

    return partial_data.ravel()

def mark_all_dirty():
    for cy in range(terrain_h // CHUNK_SIZE):
        for cx in range(terrain_w // CHUNK_SIZE):
            chunk_dirty.add((cx, cy))

def draw_value_box(rect, value_text):
    pygame.draw.rect(screen, (50,50,50), rect)
    pygame.draw.rect(screen, (120,120,120), rect, 2)
    txt = SMALL_FONT.render(value_text, True, WHITE)
    screen.blit(txt, (rect.x + 8, rect.y + 6))

def draw_chunks():
    canvas_rect = pygame.Rect(canvas_x, canvas_y, canvas_w, canvas_h)
    screen.set_clip(canvas_rect)

    vx0, vy0 = view_x, view_y
    vx1, vy1 = view_x + (canvas_w / zoom), view_y + (canvas_h / zoom)

    cx0 = max(0, int(vx0 // CHUNK_SIZE))
    cy0 = max(0, int(vy0 // CHUNK_SIZE))
    cx1 = min((terrain_w - 1) // CHUNK_SIZE, int(vx1 // CHUNK_SIZE))
    cy1 = min((terrain_h - 1) // CHUNK_SIZE, int(vy1 // CHUNK_SIZE))

    for cy in range(cy0, cy1 + 1):
        for cx in range(cx0, cx1 + 1):
            surf = chunk_surfaces.get((cx, cy))
            if not surf:
                continue

            wx, wy = cx * CHUNK_SIZE, cy * CHUNK_SIZE

            # ✅ REAL chunk size (fixes stretching)
            chunk_w = surf.get_width()
            chunk_h = surf.get_height()
            wnx = wx + chunk_w
            wny = wy + chunk_h

            # Floating screen boundaries
            sx  = canvas_x + (wx  - view_x) * zoom
            sy  = canvas_y + (wy  - view_y) * zoom
            snx = canvas_x + (wnx - view_x) * zoom
            sny = canvas_y + (wny - view_y) * zoom

            # Snap edges
            isx, isy   = int(sx), int(sy)
            isnx, isny = int(snx), int(sny)

            sw, sh = isnx - isx, isny - isy

            if sw > 0 and sh > 0:
                scaled = pygame.transform.scale(surf, (sw, sh))
                screen.blit(scaled, (isx, isy))

    screen.set_clip(None)


def clamp_view():
    global view_x, view_y

    max_x = max(0, terrain_w - canvas_w / zoom)
    max_y = max(0, terrain_h - canvas_h / zoom)

    view_x = max(0, min(view_x, max_x))
    view_y = max(0, min(view_y, max_y))

def terrain_to_surface(terrain):
    # Invert height for display:
    # 0 (highest) → white
    # 65535 (lowest) → black
    alpha = 65535 - terrain[:, :, 3].astype(np.uint16)
    # 0 (lowest) → black, 65535 (highest) → white
    # Map 16-bit (0-65535) to 8-bit (0-255) for visual display
    alpha = (terrain[:, :, 3] >> 8).astype(np.uint8)

    surface = pygame.Surface((alpha.shape[1], alpha.shape[0]))
    rgb = np.dstack((alpha, alpha, alpha))
    rgb = np.transpose(rgb, (1, 0, 2))

    pygame.surfarray.blit_array(surface, rgb)
    return surface

def rebuild_chunk(cx, cy):
    x0, y0 = cx * CHUNK_SIZE, cy * CHUNK_SIZE
    x1, y1 = min(x0 + CHUNK_SIZE, terrain_w), min(y0 + CHUNK_SIZE, terrain_h)
    
    data = terrain[y0:y1, x0:x1]
    surf = pygame.Surface((x1 - x0, y1 - y0)).convert()
    pixels = pygame.surfarray.pixels3d(surf)

    # Note: 255 - X handles the "darkness is lightness" inversion you requested earlier
    # Ensure this logic matches your desired visual style
    # Use signed 32-bit math and clipping to prevent wrapping when height > 255
    # This makes anything >= 255 render as black (0)
    
    if paint_mode == MODE_OVERHANG:
        # Render R and G channels
        # If 'red' is true, we focus on R, otherwise G. 
        # Here we show both so you can see the "overhang map"
        pixels[:, :, 0] = (255 - data[:, :, 1]).T # Red channel
        pixels[:, :, 1] = (255 - data[:, :, 0]).T # Green channel
        # Use clip to ensure values > 255 don't wrap to white
        pixels[:, :, 0] = np.clip(255 - data[:, :, 1].astype(np.int32), 0, 255).T # Red channel
        pixels[:, :, 1] = np.clip(255 - data[:, :, 0].astype(np.int32), 0, 255).T # Green channel
        pixels[:, :, 2] = 255                     # Keep B high for white background
    elif paint_mode == MODE_BLOCK:
        # Fix: Ensure the B channel (index 2) is mapped to the RGB surface
        # We render it as a Cyan/Blue tint to distinguish it from height
        b_chan = (255 - data[:, :, 2]).T
        b_chan = np.clip(255 - data[:, :, 2].astype(np.int32), 0, 255).T
        pixels[:, :, 0] = 255 # White background
        pixels[:, :, 1] = b_chan
        pixels[:, :, 2] = b_chan
    else:
        # Default Height (Alpha/Channel 3)
        h_chan = (255 - data[:, :, 3]).T
        # Logic: 255 (White) at height 0, 0 (Black) at height 255+.
        h_chan = np.clip(255 - data[:, :, 3].astype(np.int32), 0, 255).T
        pixels[:, :, 0] = h_chan
        pixels[:, :, 1] = h_chan
        pixels[:, :, 2] = h_chan
        
    del pixels
    return surf

def random_points(w, h, spacing, density=1.0):
    area = w * h
    approx_count = int(area / (spacing * spacing) * density)

    xs = np.random.randint(0, w, approx_count)
    ys = np.random.randint(0, h, approx_count)

    return zip(xs, ys)

def generate_base_terrain(spacing, strength_scale=1.0):
    global brush_strength

    h, w = terrain.shape[:2]
    if procedural_noise:
        points = seeded_poisson_points(w, h, spacing, seed, 1, k=30)
    else:
        points = poisson_points(w, h, spacing)

    old_strength = brush_strength
    brush_strength *= strength_scale

    for x, y in points:
        direction = -1 if raise_mode else 1
        apply_height_brush(x, y, direction, procedural = True)

    brush_strength = old_strength
    terrain_dirty = True


def generate_detail_terrain(spacing, strength_scale=0.3):
    global brush_strength

    h, w = terrain.shape[:2]
    if procedural_noise:
        points = seeded_poisson_points(w, h, spacing, seed, 2, k=30)
    else:
        points = poisson_points(w, h, spacing)

    old_strength = brush_strength
    brush_strength *= strength_scale

    for x, y in points:
        direction = -1 if raise_mode else 1
        apply_height_brush(x, y, direction, procedural = True)

    brush_strength = old_strength

    terrain_dirty = True

def set_pix_value(x, y, a=None, r=None, g=None, b=None):
    if r is not None:
        terrain[y, x, 0] = r
    if g is not None:
        terrain[y, x, 1] = g
    if b is not None:
        terrain[y, x, 2] = b
    if a is not None:
        terrain[y, x, 3] = a

def generate_value_noise(w, h, scale, seed=56):
    rng = np.random.default_rng(seed)
    noise = rng.random((h // scale + 2, w // scale + 2))

    result = np.zeros((h, w), dtype=np.float32)

    for y in range(h):
        for x in range(w):
            gx = x / scale
            gy = y / scale

            x0 = int(gx)
            y0 = int(gy)

            tx = gx - x0
            ty = gy - y0

            v00 = noise[y0, x0]
            v10 = noise[y0, x0 + 1]
            v01 = noise[y0 + 1, x0]
            v11 = noise[y0 + 1, x0 + 1]

            a = v00 * (1 - tx) + v10 * tx
            b = v01 * (1 - tx) + v11 * tx

            result[y, x] = a * (1 - ty) + b * ty

    return result

def radialize_value_noise(noise, radius_frac=0.5, power=2.5):
    
    h, w = noise.shape
    result = np.zeros_like(noise)

    cell = int(min(w, h) * radius_frac)
    cell = max(8, cell)

    for y in range(0, h, cell):
        for x in range(0, w, cell):
            y0 = max(0, y - cell)
            y1 = min(h, y + cell)
            x0 = max(0, x - cell)
            x1 = min(w, x + cell)

            cy = (y0 + y1) // 2
            cx = (x0 + x1) // 2

            ys, xs = np.ogrid[y0:y1, x0:x1]
            dist = np.sqrt((xs - cx)**2 + (ys - cy)**2)
            r = np.max(dist)

            falloff = np.clip(1.0 - dist / r, 0.0, 1.0) ** power
            result[y0:y1, x0:x1] += noise[y0:y1, x0:x1] * falloff

    return result / result.max()

def stamp_noise_point(x, y, radius, strength, direction):
    global brush_radius, brush_strength

    old_radius = brush_radius
    old_strength = brush_strength

    brush_radius = int(radius)
    brush_strength = float(strength)

    apply_height_brush(x, y, direction, procedural=True)

    brush_radius = old_radius
    brush_strength = old_strength

def generate_noise_layer(spacing, radius, strength, direction):
    h, w = terrain.shape[:2]

    strength_jitter = noise_settings["strength_jitter"]

    points = poisson_points(w, h, spacing)

    for px, py in points:
        s = strength * np.random.uniform(
            1.0 - strength_jitter,
            1.0 + strength_jitter
        )

        stamp_noise_point(px, py, radius, s, direction)

def poisson_points(w, h, radius, k=30):
    w = w - beach_offset*2
    h = h - beach_offset*2
    cell = radius / math.sqrt(2)
    grid_w = int(w / cell) + 1
    grid_h = int(h / cell) + 1
    grid = [[None] * grid_h for _ in range(grid_w)]

    def grid_coords(p):
        return int(p[0] / cell), int(p[1] / cell)

    def fits(p):
        gx, gy = grid_coords(p)
        for i in range(max(0, gx-2), min(grid_w, gx+3)):
            for j in range(max(0, gy-2), min(grid_h, gy+3)):
                q = grid[i][j]
                if q and (p[0]-q[0])**2 + (p[1]-q[1])**2 < radius**2:
                    return False
        return True

    points = []
    active = []

    p0 = (np.random.uniform(0, w), np.random.uniform(0, h))
    points.append(p0)
    active.append(p0)
    gx, gy = grid_coords(p0)
    grid[gx][gy] = p0

    while active:
        idx = np.random.randint(len(active))
        p = active[idx]
        found = False

        for _ in range(k):
            ang = np.random.uniform(0, 2*np.pi)
            mag = np.random.uniform(radius, 2*radius)
            npnt = (p[0] + math.cos(ang)*mag,
                    p[1] + math.sin(ang)*mag)

            if 0 <= npnt[0] < w and 0 <= npnt[1] < h and fits(npnt):
                points.append(npnt)
                active.append(npnt)
                gx, gy = grid_coords(npnt)
                grid[gx][gy] = npnt
                found = True
                break

        if not found:
            active.pop(idx)

    return [(int(x), int(y)) for x, y in points]

def seeded_poisson_points(w, h, radius, seed, point_set, k=30):
    w = w - beach_offset*2
    h = h - beach_offset*2

    combined_seed = hash((seed, point_set)) % (2**32)
    rng = np.random.default_rng(combined_seed)

    cell_size = radius / math.sqrt(2)
    
    # To make points independent of w and h, we calculate the max possible grid
    # or handle the grid dynamically. Here, we use the requested w/h.
    grid_w = int(math.ceil(w / cell_size))
    grid_h = int(math.ceil(h / cell_size))
    
    # grid[x][y] stores the point located in that cell
    grid = {} 

    def get_grid_coords(p):
        return int(p[0] / cell_size), int(p[1] / cell_size)

    def is_valid(p):
        if not (0 <= p[0] < w and 0 <= p[1] < h):
            return False
        
        gx, gy = get_grid_coords(p)
        # Check neighboring cells (5x5 area around the point)
        for i in range(gx - 2, gx + 3):
            for j in range(gy - 2, gy + 3):
                q = grid.get((i, j))
                if q:
                    dist_sq = (p[0] - q[0])**2 + (p[1] - q[1])**2
                    if dist_sq < radius**2:
                        return False
        return True

    points = []
    active_list = []

    # Initial point: seeded by the specific set/seed combo
    # We use a fixed starting strategy (e.g., center or random based on rng)
    p0 = (rng.uniform(0, w), rng.uniform(0, h))
    
    points.append(p0)
    active_list.append(p0)
    grid[get_grid_coords(p0)] = p0

    while active_list:
        # Pick a random active point
        idx = rng.integers(len(active_list))
        p = active_list[idx]
        found_new_point = False

        for _ in range(k):
            # Generate a point in the annular region [radius, 2*radius]
            angle = rng.uniform(0, 2 * math.pi)
            distance = rng.uniform(radius, 2 * radius)
            new_p = (
                p[0] + math.cos(angle) * distance,
                p[1] + math.sin(angle) * distance
            )

            if is_valid(new_p):
                points.append(new_p)
                active_list.append(new_p)
                grid[get_grid_coords(new_p)] = new_p
                found_new_point = True
                break

        if not found_new_point:
            active_list.pop(idx)

    return [(int(x), int(y)) for x, y in points]

def apply_point_feature(feature, direction, layer_int):
    h, w = terrain.shape[:2]

    spacing  = feature["spacing"]
    radius   = feature["radius"]
    strength = feature["strength"]
    power    = feature["power"]
    density  = feature["density"]
    jitter   = feature["jitter"]
    count    = feature["count"]

    global brush_radius, brush_strength, brush_power

    old_radius   = brush_radius
    old_strength = brush_strength
    old_power    = brush_power

    brush_radius   = radius
    brush_strength = strength
    brush_power    = power

    # ✅ ONE deterministic RNG per layer
    rng = np.random.default_rng(seed + layer_int * 7919)

    if procedural_noise:
        points = seeded_poisson_points(w, h, spacing, seed, layer_int, k=30)
    else:
        points = poisson_points(w, h, spacing)

    for px, py in points:
        for _ in range(count):
            # ✅ jitter must stay within Poisson domain
            jx = int(rng.uniform(-spacing * jitter, spacing * jitter))
            jy = int(rng.uniform(-spacing * jitter, spacing * jitter))

            x = np.clip(px + jx, 0, w - 1) + beach_offset
            y = np.clip(py + jy, 0, h - 1) + beach_offset

            apply_height_brush(x, y, direction, procedural=False)

    brush_radius   = old_radius
    brush_strength = old_strength
    brush_power    = old_power

def generate_custom_noise():
    global noise_queue
    terrain[:, :, 3] = base_height
    noise_queue = []

    direction = -1 if raise_mode else 1
    h, w = terrain.shape[:2]

    apply_point_feature(point_features["primary"], 1, 1)
    apply_point_feature(point_features["secondary"], 1, 2)
    if erosion:
        apply_point_feature(point_features["tertiary"], -1, 3)

def generate_terrain():
    global terrain

    h, w = terrain.shape[:2]

    if noise_mode == NOISE_STAMP:
        terrain[:, :, 3] = base_height

        generate_base_terrain(brush_radius * 4, 1.0)
        generate_base_terrain(brush_radius * 2, 0.6)
        generate_detail_terrain(brush_radius, 0.25)

    elif noise_mode == NOISE_VALUE:
        scale = max(4, int(min(w, h) / 16))

        noise = generate_value_noise(w, h, scale, noise_seed)
        noise = (noise - noise.min()) / (noise.max() - noise.min())

        # 🔥 RADIAL SHAPING (this is the magic)
        noise = radialize_value_noise(noise, radius_frac=0.35, power=2.8)

        heights = max_height - noise * (max_height - min_height)
        terrain[:, :, 3] = heights.astype(np.uint8)


    elif noise_mode == NOISE_POINTS:
        terrain[:, :, 3] = base_height

        noise_settings["direction"] = -1 if raise_mode else 1
        generate_custom_noise()

    terrain_dirty = True



def apply_height_brush(tx, ty, paint_direction, procedural=False):
    global terrain

    tx = int(tx)
    ty = int(ty)

    r = int(brush_radius)
    h, w = terrain.shape[:2]

    x0 = max(0, tx - r)
    x1 = min(w, tx + r + 1)
    y0 = max(0, ty - r)
    y1 = min(h, ty + r + 1)

    if x0 >= x1 or y0 >= y1:
        return

    # Determine which channel we are actually editing
    # 0: Red, 1: Green, 2: Blue, 3: Alpha (Height)
    if paint_mode == MODE_OVERHANG:
        target_channel = 1 if red else 0
    elif paint_mode == MODE_BLOCK:
        target_channel = 2
    else:
        target_channel = 3

    key = (r, brush_power)
    if key not in brush_cache:
        ys2, xs2 = np.ogrid[-r:r+1, -r:r+1]   
        dist2 = np.sqrt(xs2*xs2 + ys2*ys2)
        t2 = np.clip(dist2 / r, 0.0, 1.0)
        # Store mask and falloff
        brush_cache[key] = (t2 <= 1.0, (1.0 - t2) ** brush_power)

    mask_full, falloff_full = brush_cache[key]

    # Slice the cached brush to fit the current bounds
    # (Handling cases where the brush is partially off-screen)
    slice_y = slice(y0 - ty + r, y1 - ty + r)
    slice_x = slice(x0 - tx + r, x1 - tx + r)
    mask = mask_full[slice_y, slice_x]
    falloff = falloff_full[slice_y, slice_x]

    # Extract ONLY the channel we are currently painting
    working_channel = terrain[y0:y1, x0:x1, target_channel].astype(np.float32)

    # Pixel delta
    delta = brush_strength * 255.0 * paint_direction

    if not procedural:
        # MANUAL PAINTING → additive based on brush falloff
        working_channel[mask] += delta * falloff[mask]
    else:
        # PROCEDURAL → growth-limited
        proposed = working_channel + delta * falloff
        if paint_direction > 0:
            working_channel[mask] = np.maximum(working_channel[mask], proposed[mask])
        else:
            working_channel[mask] = np.minimum(working_channel[mask], proposed[mask])

    # Finalize values: Clip and cast back to uint16
    np.clip(working_channel, 0, 65535, out=working_channel)
    terrain[y0:y1, x0:x1, target_channel] = working_channel.astype(np.uint16)

    # Mark global terrain as dirty for saving/processing
    global terrain_dirty
    terrain_dirty = True
    
    # Mark specific chunks for visual refresh
    cx0 = x0 // CHUNK_SIZE
    cx1 = (x1 - 1) // CHUNK_SIZE
    cy0 = y0 // CHUNK_SIZE
    cy1 = (y1 - 1) // CHUNK_SIZE

    for cy in range(cy0, cy1 + 1):
        for cx in range(cx0, cx1 + 1):
            chunk_dirty.add((cx, cy))

def compute_min_zoom():
   fit_zoom = min(
       (WINDOW_W - 2 * CANVAS_PADDING) / terrain_w,
       (WINDOW_H - 2 * CANVAS_PADDING) / terrain_h
   )
   return min(fit_zoom, 1.0)

def screen_to_terrain(mx, my):
    tx = view_x + (mx - canvas_x) / zoom
    ty = view_y + (my - canvas_y) / zoom

    if 0 <= tx < terrain_w and 0 <= ty < terrain_h:
        return int(tx), int(ty)
    return None

def draw_arrow_button(rect, text):
    pygame.draw.rect(screen, (60,60,60), rect)
    pygame.draw.rect(screen, (120,120,120), rect, 2)
    label = SMALL_FONT.render(text, True, WHITE)
    screen.blit(label, label.get_rect(center=rect.center))

def draw_input_box(rect, value, key):
    pygame.draw.rect(screen, (60, 60, 60), rect)
    pygame.draw.rect(screen, (160, 160, 160), rect, 1)

    if active_input == key:
        text = input_buffers[key]
    else:
        text = str(value)

    txt = SMALL_FONT.render(text, True, WHITE)
    screen.blit(txt, (rect.x + 5, rect.y + 4))

class Slider:
    def __init__(self, x, y, w, min_val, max_val, value, step=0.0):
        self.rect = pygame.Rect(x, y, w, 18)
        self.min = min_val
        self.max = max_val
        self.value = value
        self.step = step
        self.dragging = False

def draw_slider(slider):
    # track
    pygame.draw.rect(screen, (60, 60, 60), slider.rect)
    pygame.draw.rect(screen, (120, 120, 120), slider.rect, 1)

    # knob position
    t = (slider.value - slider.min) / (slider.max - slider.min)
    knob_x = slider.rect.x + int(t * slider.rect.w)

    knob_rect = pygame.Rect(knob_x - 5, slider.rect.y - 4, 10, slider.rect.h + 8)
    pygame.draw.rect(screen, (200, 200, 200), knob_rect)

def handle_slider_event(slider, event):
    if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
        if slider.rect.collidepoint(event.pos):
            slider.dragging = True

    elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
        slider.dragging = False

    elif event.type == pygame.MOUSEMOTION and slider.dragging:
        mx = event.pos[0]
        t = (mx - slider.rect.x) / slider.rect.w
        t = max(0.0, min(1.0, t))

        val = slider.min + t * (slider.max - slider.min)

        if slider.step > 0:
            val = round(val / slider.step) * slider.step

        slider.value = val

class NumberInput:
    def __init__(self, x, y, w, value):
        self.rect = pygame.Rect(x, y, w, 28)
        self.value = value
        self.buffer = ""
        self.active = False

def draw_number_input(inp):
    color = (120,120,200) if inp.active else (120,120,120)
    pygame.draw.rect(screen, (50,50,50), inp.rect)
    pygame.draw.rect(screen, color, inp.rect, 2)

    text = inp.buffer if inp.active else str(inp.value)
    label = SMALL_FONT.render(text, True, WHITE)
    screen.blit(label, (inp.rect.x + 6, inp.rect.y + 6))

def handle_number_input_event(inp, event, min_val=None, max_val=None):
    if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
        inp.active = inp.rect.collidepoint(event.pos)
        if inp.active:
            inp.buffer = ""

    if not inp.active:
        return

    if event.type == pygame.KEYDOWN:
        if event.key == pygame.K_RETURN:
            if inp.buffer != "":
                try:
                    v = float(inp.buffer)
                    if min_val is not None:
                        v = max(min_val, v)
                    if max_val is not None:
                        v = min(max_val, v)
                    inp.value = v
                except:
                    pass
            inp.buffer = ""
            inp.active = False

        elif event.key == pygame.K_BACKSPACE:
            inp.buffer = inp.buffer[:-1]

        else:
            if event.unicode.isdigit() or event.unicode == ".":
                inp.buffer += event.unicode

def sync_slider_input(slider, inp):
    if not inp.active:
        inp.value = slider.value
    else:
        try:
            slider.value = float(inp.buffer)
        except:
            pass


# ---------------- UI ELEMENTS ----------------
input_active = None
input_w_text = ""
input_h_text = ""
input_base_text = "0"
input_offset_text = "0"

def layout_ui():
    global btn_create, btn_open_project, btn_open_png, input_w, input_h, btn_confirm, input_base, input_offset
    global mode_label_pos, btn_height, btn_overhang, btn_block, btn_save, btn_open
    global brush_label_y, radius_rect, strength_rect, radius_minus, radius_plus
    global strength_minus, strength_plus, power_rect, power_minus, power_plus
    global max_height_rect, min_height_rect, btn_noise
    global btn_noise_stamp, btn_noise_value, btn_noise_points
    global btn_primary, btn_secondary, btn_tertiary
    global primary_radius_slider, primary_radius_input, primary_spacing_slider, primary_spacing_input
    global primary_strength_slider, primary_strength_input, primary_power_slider, primary_power_input
    global primary_density_slider, primary_density_input, primary_jitter_slider, primary_jitter_input
    global primary_count_slider, primary_count_input
    global secondary_radius_slider, secondary_radius_input, secondary_spacing_slider, secondary_spacing_input
    global secondary_strength_slider, secondary_strength_input, secondary_power_slider, secondary_power_input
    global secondary_density_slider, secondary_density_input, secondary_jitter_slider, secondary_jitter_input
    global secondary_count_slider, secondary_count_input
    global tertiary_radius_slider, tertiary_radius_input, tertiary_spacing_slider, tertiary_spacing_input
    global tertiary_strength_slider, tertiary_strength_input, tertiary_power_slider, tertiary_power_input
    global tertiary_density_slider, tertiary_density_input, tertiary_jitter_slider, tertiary_jitter_input
    global tertiary_count_slider, tertiary_count_input
    global button_erosion, button_procedural, seed_rect, btn_iso_preview, btn_red

    cx = WINDOW_W // 2
    btn_create = pygame.Rect(cx - 125, 220, 250, 50)
    btn_open_project = pygame.Rect(cx - 150, 300, 300, 50)
    btn_open_png = pygame.Rect(cx - 150, 370, 300, 50)

    input_w = pygame.Rect(cx - 90, 240, 180, 40)
    input_h = pygame.Rect(cx - 90, 300, 180, 40)
    input_base = pygame.Rect(cx - 90, 360, 180, 40)
    input_offset = pygame.Rect(cx - 90, 420, 180, 40)
    btn_confirm = pygame.Rect(cx - 90, 480, 180, 45)

    mode_label_pos = (UI_PADDING, 40)

    btn_height = pygame.Rect(20, 70, 220, 40)
    btn_overhang = pygame.Rect(20, 120, 220, 40)
    btn_block = pygame.Rect(20, 170, 220, 40)

    btn_save = pygame.Rect(20, WINDOW_H - 60, 220, 40)

    brush_label_y = 230

    radius_rect = pygame.Rect(60, brush_label_y + 20, 140, 32)
    strength_rect = pygame.Rect(60, brush_label_y + 80, 140, 32)

    radius_minus = pygame.Rect(20, brush_label_y + 20, 32, 32)
    radius_plus  = pygame.Rect(208, brush_label_y + 20, 32, 32)

    strength_minus = pygame.Rect(20, brush_label_y + 80, 32, 32)
    strength_plus  = pygame.Rect(208, brush_label_y + 80, 32, 32)

    power_rect = pygame.Rect(60, brush_label_y + 140, 140, 32)
    power_minus = pygame.Rect(20, brush_label_y + 140, 32, 32)
    power_plus  = pygame.Rect(208, brush_label_y + 140, 32, 32)

    max_height_rect =  pygame.Rect(20, brush_label_y + 220, 100, 32)
    min_height_rect =  pygame.Rect(20, brush_label_y + 280, 100, 32)

    btn_noise = pygame.Rect(20, WINDOW_H - 120, 220, 40)

    btn_noise_stamp  = pygame.Rect(20, WINDOW_H - 260, 220, 36)
    btn_noise_value  = pygame.Rect(20, WINDOW_H - 220, 220, 36)
    btn_noise_points = pygame.Rect(20, WINDOW_H - 180, 220, 36)

    rx = WINDOW_W - 270
    sx = WINDOW_W - 200

    btn_primary = pygame.Rect(rx, 70, 220, 40)
    btn_secondary = pygame.Rect(rx, 120, 220, 40)
    btn_tertiary = pygame.Rect(rx, 170, 220, 40)

    # Sliders
    # Primary
    primary_radius_slider = Slider(sx, 260, 180, 1, 300, point_features["primary"]["radius"], step=1)
    primary_radius_input  = NumberInput(rx, 255, 60, point_features["primary"]["radius"])
    primary_spacing_slider = Slider(sx, 320, 180, 1, 300, point_features["primary"]["spacing"], step=1)
    primary_spacing_input  = NumberInput(rx, 315, 60, point_features["primary"]["spacing"])
    primary_strength_slider = Slider(sx, 380, 180, 0.1, 1, point_features["primary"]["strength"], step=0.05)
    primary_strength_input  = NumberInput(rx, 375, 60, point_features["primary"]["strength"])
    primary_power_slider = Slider(sx, 440, 180, 0.1, 20, point_features["primary"]["power"], step=0.1)
    primary_power_input  = NumberInput(rx, 435, 60, point_features["primary"]["power"])
    primary_density_slider = Slider(sx, 500, 180, 0.05, 10, point_features["primary"]["density"], step=0.05)
    primary_density_input  = NumberInput(rx, 495, 60, point_features["primary"]["density"])
    primary_jitter_slider = Slider(sx, 560, 180, 0.1, 100, point_features["primary"]["jitter"], step=0.05)
    primary_jitter_input  = NumberInput(rx, 555, 60, point_features["primary"]["jitter"])
    primary_count_slider = Slider(sx, 620, 180, 1, 100, point_features["primary"]["count"], step=1)
    primary_count_input  = NumberInput(rx, 615, 60, point_features["primary"]["count"])

    # Secondary
    secondary_radius_slider = Slider(sx, 260, 180, 1, 300, point_features["secondary"]["radius"], step=1)
    secondary_radius_input  = NumberInput(rx, 255, 60, point_features["secondary"]["radius"])
    secondary_spacing_slider = Slider(sx, 320, 180, 1, 300, point_features["secondary"]["spacing"], step=1)
    secondary_spacing_input  = NumberInput(rx, 315, 60, point_features["secondary"]["spacing"])
    secondary_strength_slider = Slider(sx, 380, 180, 0.1, 1, point_features["secondary"]["strength"], step=0.05)
    secondary_strength_input  = NumberInput(rx, 375, 60, point_features["secondary"]["strength"])
    secondary_power_slider = Slider(sx, 440, 180, 0.1, 20, point_features["secondary"]["power"], step=0.1)
    secondary_power_input  = NumberInput(rx, 435, 60, point_features["secondary"]["power"])
    secondary_density_slider = Slider(sx, 500, 180, 0.05, 10, point_features["secondary"]["density"], step=0.05)
    secondary_density_input  = NumberInput(rx, 495, 60, point_features["secondary"]["density"])
    secondary_jitter_slider = Slider(sx, 560, 180, 0.1, 100, point_features["secondary"]["jitter"], step=0.05)
    secondary_jitter_input  = NumberInput(rx, 555, 60, point_features["secondary"]["jitter"])
    secondary_count_slider = Slider(sx, 620, 180, 1, 100, point_features["secondary"]["count"], step=1)
    secondary_count_input  = NumberInput(rx, 615, 60, point_features["secondary"]["count"])

    # Tertiary
    tertiary_radius_slider = Slider(sx, 260, 180, 1, 300, point_features["tertiary"]["radius"], step=1)
    tertiary_radius_input  = NumberInput(rx, 255, 60, point_features["tertiary"]["radius"])
    tertiary_spacing_slider = Slider(sx, 320, 180, 1, 300, point_features["tertiary"]["spacing"], step=1)
    tertiary_spacing_input  = NumberInput(rx, 315, 60, point_features["tertiary"]["spacing"])
    tertiary_strength_slider = Slider(sx, 380, 180, 0.1, 1, point_features["tertiary"]["strength"], step=0.05)
    tertiary_strength_input  = NumberInput(rx, 375, 60, point_features["tertiary"]["strength"])
    tertiary_power_slider = Slider(sx, 440, 180, 0.1, 20, point_features["tertiary"]["power"], step=0.1)
    tertiary_power_input  = NumberInput(rx, 435, 60, point_features["tertiary"]["power"])
    tertiary_density_slider = Slider(sx, 500, 180, 0.05, 10, point_features["tertiary"]["density"], step=0.05)
    tertiary_density_input  = NumberInput(rx, 495, 60, point_features["tertiary"]["density"])
    tertiary_jitter_slider = Slider(sx, 560, 180, 0.1, 100, point_features["tertiary"]["jitter"], step=0.05)
    tertiary_jitter_input  = NumberInput(rx, 555, 60, point_features["tertiary"]["jitter"])
    tertiary_count_slider = Slider(sx, 620, 180, 1, 100, point_features["tertiary"]["count"], step=1)
    tertiary_count_input  = NumberInput(rx, 615, 60, point_features["tertiary"]["count"])

    button_erosion = pygame.Rect(rx, WINDOW_H - 100, 220, 36)
    button_procedural = pygame.Rect(rx, WINDOW_H - 60, 220, 36)
    seed_rect =  pygame.Rect(rx, WINDOW_H - 200, 140, 32)

    btn_iso_preview = pygame.Rect(20, brush_label_y + 320, 220, 40)
    btn_red = pygame.Rect(rx, 70, 220, 40)

layout_ui()

# ---------------- MAIN LOOP ----------------
screen = pygame.display.set_mode((WINDOW_W, WINDOW_H), pygame.RESIZABLE)
pygame.display.set_caption("Vertex HGT Editor")
try:
    pygame.display.set_icon(pygame.image.load("terrain_tools.ico"))
except Exception:
    pass

clock = pygame.time.Clock()
mark_all_dirty()
running = True
while running:
    mouse_pos = pygame.mouse.get_pos()
    mouse_down = False

    if state == STATE_EDITOR and terrain is not None:
        rpw = UI_PANEL_W if right_panel else 0
        avail_w = WINDOW_W - UI_PANEL_W - rpw
        
        # Calculate size based on zoom
        canvas_w = avail_w - 2 * CANVAS_PADDING
        canvas_h = WINDOW_H - 2 * CANVAS_PADDING

        
        # Calculate offsets to center the view
        canvas_x = UI_PANEL_W + (avail_w - canvas_w) // 2
        canvas_y = (WINDOW_H - canvas_h) // 2

        visible_w = terrain_w * zoom
        visible_h = terrain_h * zoom

        render_off_x = max(0, (canvas_w - visible_w) // 2)
        render_off_y = max(0, (canvas_h - visible_h) // 2)

    
    if state == STATE_EDITOR and panning:
        mx, my = pygame.mouse.get_pos()
        dx = mx - pan_start_mouse[0]
        dy = my - pan_start_mouse[1]        
        # Convert screen movement to terrain movement
        view_x = pan_start_view[0] - dx / zoom
        view_y = pan_start_view[1] - dy / zoom
        clamp_view()


    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        
        if event.type == pygame.VIDEORESIZE:
            WINDOW_W, WINDOW_H = event.w, event.h
            if iso_preview.active:
                screen = pygame.display.set_mode((WINDOW_W, WINDOW_H), pygame.OPENGL | pygame.DOUBLEBUF | pygame.RESIZABLE)
                iso_preview.update_viewport()
            else:
                screen = pygame.display.set_mode((WINDOW_W, WINDOW_H), pygame.RESIZABLE)
                layout_ui()

        if event.type == pygame.KEYDOWN and event.key == pygame.K_F11:
            pygame.display.toggle_fullscreen()

        if event.type == pygame.MOUSEWHEEL and state == STATE_EDITOR:
            old_zoom = zoom

            zoom *= 1.1 ** (event.y)
            zoom = max(compute_min_zoom(), min(MAX_ZOOM, zoom))
            
            mx, my = pygame.mouse.get_pos()

            cx = mx - canvas_x
            cy = my - canvas_y

            if cx < 0 or cy < 0 or cx > canvas_w or cy > canvas_h:
                continue
            
            

            tx = view_x + cx / old_zoom
            ty = view_y + cy / old_zoom

            view_x = tx - cx / zoom
            view_y = ty - cy / zoom
            clamp_view()

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mouse_down = True

        if event.type == pygame.KEYDOWN and state == STATE_SIZE:
            if input_active == "w":
                if event.key == pygame.K_BACKSPACE:
                    input_w_text = input_w_text[:-1]
                elif event.unicode.isdigit():
                    input_w_text += event.unicode
            elif input_active == "h":
                if event.key == pygame.K_BACKSPACE:
                    input_h_text = input_h_text[:-1]
                elif event.unicode.isdigit():
                    input_h_text += event.unicode
            elif input_active == "b":
                if event.key == pygame.K_BACKSPACE:
                    input_base_text = input_base_text[:-1]
                elif event.unicode.isdigit():
                    input_base_text += event.unicode

            elif input_active == "o":
                if event.key == pygame.K_BACKSPACE:
                    input_offset_text = input_offset_text[:-1]
                elif event.unicode.isdigit():
                    input_offset_text += event.unicode

        if event.type == pygame.MOUSEBUTTONDOWN and state == STATE_EDITOR:
            if event.button == 3:  # Right mouse button
                panning = True
                pan_start_mouse = pygame.mouse.get_pos()
                pan_start_view = (view_x, view_y)

        elif event.type == pygame.MOUSEBUTTONUP and state == STATE_EDITOR:
            if event.button == 3:
                panning = False

        if event.type == pygame.MOUSEMOTION and state == STATE_EDITOR and painting:
            mx, my = event.pos
            pos = screen_to_terrain(mx, my)

            if pos:
                direction = -1 if raise_mode else 1

                if last_stamp_pos is None:
                    apply_height_brush(pos[0], pos[1], direction, procedural=False)
                    last_stamp_pos = pos
                else:
                    x0, y0 = last_stamp_pos
                    x1, y1 = pos

                    dx = x1 - x0
                    dy = y1 - y0
                    dist = math.hypot(dx, dy)

                    spacing = brush_radius * max(0.5, brush_spacing)

                    if dist >= spacing:
                        steps = int(dist // spacing)
                        for i in range(1, steps + 1):
                            t = i / steps
                            xi = int(x0 + dx * t)
                            yi = int(y0 + dy * t)
                            apply_height_brush(xi, yi, direction, procedural=False)

                        last_stamp_pos = pos


        
        if event.type == pygame.KEYDOWN and event.key == pygame.K_LSHIFT:
            raise_mode = not raise_mode

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and state == STATE_EDITOR:
            painting = True
            last_paint_pos = None
            last_stamp_pos = None

        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1 and state == STATE_EDITOR:
            painting = False
            last_paint_pos = None
            last_stamp_pos = None


        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if btn_height.collidepoint(mouse_pos):
                paint_mode = MODE_HEIGHT
                mark_all_dirty()
            elif btn_overhang.collidepoint(mouse_pos):
                paint_mode = MODE_OVERHANG
                mark_all_dirty()
            elif btn_block.collidepoint(mouse_pos):
                paint_mode = MODE_BLOCK
                mark_all_dirty()

            if btn_noise_stamp.collidepoint(mouse_pos):
                noise_mode = NOISE_STAMP
            elif btn_noise_value.collidepoint(mouse_pos):
                noise_mode = NOISE_VALUE
            elif btn_noise_points.collidepoint(mouse_pos):
                noise_mode = NOISE_POINTS
                #right_panel = True

            if btn_primary.collidepoint(mouse_pos):
                layer_mode = "primary"
            elif btn_secondary.collidepoint(mouse_pos):
                layer_mode = "secondary"
            elif btn_tertiary.collidepoint(mouse_pos):
                layer_mode = "tertiary"

            if button_erosion.collidepoint(mouse_pos):
                erosion = False if erosion else True

            if button_procedural.collidepoint(mouse_pos):
                procedural_noise = False if procedural_noise else True

            if right_panel and paint_mode == MODE_OVERHANG:
                if btn_red.collidepoint(mouse_pos):
                    red = False if red else True

        if event.type == pygame.KEYDOWN and state == STATE_EDITOR:
            if event.key == pygame.K_LEFT:
                brush_radius = max(1, brush_radius - 1)
                brush_radius = min(brush_radius, min(terrain_w, terrain_h) // 2)
            elif event.key == pygame.K_RIGHT:
                brush_radius += 1
                brush_radius = min(brush_radius, min(terrain_w, terrain_h) // 2)
            elif event.key == pygame.K_DOWN:
                brush_strength = max(0.001, brush_strength - 0.02)
            elif event.key == pygame.K_UP:
                brush_strength += 0.02
            

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if radius_minus.collidepoint(mouse_pos):
                brush_radius = max(1, brush_radius - 1)
                brush_radius = min(brush_radius, min(terrain_w, terrain_h) // 2)
            elif radius_plus.collidepoint(mouse_pos):
                brush_radius += 1
                brush_radius = min(brush_radius, min(terrain_w, terrain_h) // 2)

            elif strength_minus.collidepoint(mouse_pos):
                brush_strength = max(0.001, brush_strength - 0.02)
            elif strength_plus.collidepoint(mouse_pos):
                brush_strength += 0.02

            elif power_minus.collidepoint(mouse_pos):
                brush_power = max(0.1, brush_power - 0.1)
            elif power_plus.collidepoint(mouse_pos):
                brush_power += 0.1
            
            elif btn_save.collidepoint(mouse_pos) and state == STATE_EDITOR:
                path = save_file_dialog()
                if path:
                    save_terrain(path, terrain)

            elif btn_noise.collidepoint(mouse_pos):
                generate_terrain()
                #right_panel = False
            
            elif btn_iso_preview.collidepoint(mouse_pos):
                iso_preview.toggle()

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and state == STATE_EDITOR:
            active_input = None  # reset first

            if radius_rect.collidepoint(event.pos):
                active_input = "radius"
                input_buffers["radius"] = ""

            elif strength_rect.collidepoint(event.pos):
                active_input = "strength"
                input_buffers["strength"] = ""

            elif power_rect.collidepoint(event.pos):
                active_input = "power"
                input_buffers["power"] = ""
            
            elif max_height_rect.collidepoint(event.pos):
                active_input = "max"
                input_buffers["max"] = ""

            elif min_height_rect.collidepoint(event.pos):
                active_input = "min"
                input_buffers["min"] = ""
            
            elif seed_rect.collidepoint(event.pos):
                active_input = "seed"
                input_buffers["seed"] = ""

            painting = True
            last_stamp_pos = None

            pos = screen_to_terrain(*event.pos)
            if pos:
                direction = -1 if raise_mode else 1
                apply_height_brush(pos[0], pos[1], direction, procedural=False)


        if event.type == pygame.KEYDOWN and active_input in input_buffers:
            if event.key == pygame.K_RETURN:
                text = input_buffers[active_input]

                if text != "":
                    value = float(text)

                    if active_input == "radius":
                        brush_radius = int(max(1, min(500, value)))
                        brush_radius = min(brush_radius, min(terrain_w, terrain_h) // 2)

                    elif active_input == "strength":
                        brush_strength = max(0.001, min(1.0, value))

                    elif active_input == "power":
                        brush_power = max(0.1, min(10.0, value))

                    elif active_input == "max":
                        max_height = int(max(0.1, min(65535.0, value)))

                    elif active_input == "min":
                        min_height = int(max(0.1, min(65535.0, value)))

                    elif active_input == "seed":
                        seed = int(max(0.1, min(100000000000, value)))

                input_buffers[active_input] = ""
                active_input = None

            elif event.key == pygame.K_BACKSPACE:
                input_buffers[active_input] = input_buffers[active_input][:-1]

            else:
                if event.unicode.isdigit() or event.unicode == ".":
                    input_buffers[active_input] += event.unicode
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_s and pygame.key.get_mods() & pygame.KMOD_CTRL:
                path = save_file_dialog()
                if path:
                    save_terrain(path, terrain)

        if iso_preview.active:
            if iso_preview.handle_input(event):
                # If handle_input returned True, it means ESC was pressed
                continue
        
        if right_panel and noise_mode == NOISE_POINTS:
            if layer_mode == "primary":
                handle_slider_event(primary_radius_slider, event)
                handle_number_input_event(primary_radius_input, event, 1, 300)

                handle_slider_event(primary_spacing_slider, event)
                handle_number_input_event(primary_spacing_input, event, 1, 300)

                handle_slider_event(primary_strength_slider, event)
                handle_number_input_event(primary_strength_input, event, 0.1, 1)

                handle_slider_event(primary_power_slider, event)
                handle_number_input_event(primary_power_input, event, 0.1, 20)

                handle_slider_event(primary_density_slider, event)
                handle_number_input_event(primary_density_input, event, 0.05, 10)

                handle_slider_event(primary_jitter_slider, event)
                handle_number_input_event(primary_jitter_input, event, 0.1, 100)

                handle_slider_event(primary_count_slider, event)
                handle_number_input_event(primary_jitter_input, event, 1, 100)

            elif layer_mode == "secondary":
                handle_slider_event(secondary_radius_slider, event)
                handle_number_input_event(secondary_radius_input, event, 1, 300)

                handle_slider_event(secondary_spacing_slider, event)
                handle_number_input_event(secondary_spacing_input, event, 1, 300)

                handle_slider_event(secondary_strength_slider, event)
                handle_number_input_event(secondary_strength_input, event, 0.1, 1)

                handle_slider_event(secondary_power_slider, event)
                handle_number_input_event(secondary_power_input, event, 0.1, 20)

                handle_slider_event(secondary_density_slider, event)
                handle_number_input_event(secondary_density_input, event, 0.05, 10)

                handle_slider_event(secondary_jitter_slider, event)
                handle_number_input_event(secondary_jitter_input, event, 0.1, 100)

                handle_slider_event(secondary_count_slider, event)
                handle_number_input_event(secondary_jitter_input, event, 1, 100)

            elif layer_mode == "tertiary":
                handle_slider_event(tertiary_radius_slider, event)
                handle_number_input_event(tertiary_radius_input, event, 1, 300)

                handle_slider_event(tertiary_spacing_slider, event)
                handle_number_input_event(tertiary_spacing_input, event, 1, 300)

                handle_slider_event(tertiary_strength_slider, event)
                handle_number_input_event(tertiary_strength_input, event, 0.1, 1)

                handle_slider_event(tertiary_power_slider, event)
                handle_number_input_event(tertiary_power_input, event, 0.1, 20)

                handle_slider_event(tertiary_density_slider, event)
                handle_number_input_event(tertiary_density_input, event, 0.05, 10)

                handle_slider_event(tertiary_jitter_slider, event)
                handle_number_input_event(tertiary_jitter_input, event, 0.1, 100)

                handle_slider_event(tertiary_count_slider, event)
                handle_number_input_event(tertiary_jitter_input, event, 1, 100)
        
#inpu goes above this----------------------------------------------------------


        

    screen.fill(DARK)

    if iso_preview.active:
        iso_preview.render(screen, terrain)
    else: 
        if state == STATE_MENU:
            title = TITLE_FONT.render("Vertex-HGT", True, WHITE)
            screen.blit(title, title.get_rect(center=(WINDOW_W // 2, 140)))

            draw_button(btn_create, "Create New")
            draw_button(btn_open_project, "Open Project Folder")
            draw_button(btn_open_png, "Open PNG Image")

            if button_clicked(btn_create, mouse_pos, mouse_down):
                state = STATE_SIZE

            if button_clicked(btn_open_project, mouse_pos, mouse_down):
                path = open_project_dialog()
                if path:
                    try:
                        terrain = load_terrain_from_project(path)
                        if terrain is not None:
                            terrain_h, terrain_w = terrain.shape[:2]
                            height_f = terrain[:,:,3].astype(np.float32)/255.0
                            zoom = compute_min_zoom()
                            view_x=0
                            view_y=0
                            clamp_view()
                            state = STATE_EDITOR
                    except Exception as e:
                        print("Failed to load project:", e)

            if button_clicked(btn_open_png, mouse_pos, mouse_down):
                path = open_file_dialog()
                if path:
                    try:
                        terrain = load_terrain(path)
                        terrain_h, terrain_w = terrain.shape[:2]
                        height_f = terrain[:,:,3].astype(np.float32)/255.0
                        zoom = compute_min_zoom()
                        view_x=0
                        view_y=0
                        clamp_view()
                        state = STATE_EDITOR
                    except Exception as e:
                        print("Failed to load image:", e)

        # ---------------- SIZE DIALOG ----------------
        elif state == STATE_SIZE:
            label = FONT.render("Enter Terrain Size (pixels)", True, WHITE)
            screen.blit(label, label.get_rect(center=(WINDOW_W // 2, 180)))

            draw_text_input(input_w, input_w_text, input_active == "w")
            draw_text_input(input_h, input_h_text, input_active == "h")
            draw_text_input(input_base, input_base_text, input_active == "b")
            draw_text_input(input_offset, input_offset_text, input_active == "o")

            w_lbl = SMALL_FONT.render("Width", True, WHITE)
            h_lbl = SMALL_FONT.render("Height", True, WHITE)
            b_lbl = SMALL_FONT.render("Color", True, WHITE)
            o_lbl = SMALL_FONT.render("Offset", True, WHITE)

            screen.blit(w_lbl, (input_w.x, input_w.y - 22))
            screen.blit(h_lbl, (input_h.x, input_h.y - 22))
            screen.blit(b_lbl, (input_base.x, input_base.y - 22))
            screen.blit(o_lbl, (input_offset.x, input_offset.y - 22))

            draw_button(btn_confirm, "Create")

            if button_clicked(input_w, mouse_pos, mouse_down):
                input_active = "w"
            elif button_clicked(input_h, mouse_pos, mouse_down):
                input_active = "h"
            elif button_clicked(input_base, mouse_pos, mouse_down):
                input_active = "b"
            elif button_clicked(input_offset, mouse_pos, mouse_down):
                input_active = "o"

            if button_clicked(btn_confirm, mouse_pos, mouse_down):
                if input_w_text and input_h_text:
                    terrain_w = int(input_w_text)
                    terrain_h = int(input_h_text)
                    base_height = int(input_base_text)
                    base_height = max(0, min(65535, base_height))
                    beach_offset = int(input_offset_text)
                    beach_offset = max(0, min(min(terrain_w, terrain_h)/2, beach_offset))
                    terrain = create_new_terrain(terrain_w, terrain_h)
                    #terrain = np.full((terrain_h,terrain_w,4),255, dtype=np.uint8)
                    height_f = np.full((terrain_h, terrain_w),0.7, dtype=np.float32)
                    state = STATE_EDITOR
                    print(f"Created terrain {terrain_w}x{terrain_h}")

        # ---------------- EDITOR PLACEHOLDER ----------------
        elif state == STATE_EDITOR:

            # --- TERRAIN SURFACE UPDATE ---
            MAX_CHUNKS_PER_FRAME = 2

            for _ in range(MAX_CHUNKS_PER_FRAME):
                if not chunk_dirty:
                    break
                cx, cy = chunk_dirty.pop()
                chunk_surfaces[(cx, cy)] = rebuild_chunk(cx, cy)

            NOISE_PER_FRAME = 20

            if not painting:
                for _ in range(min(NOISE_PER_FRAME, len(noise_queue))):
                    feature, x, y, direction = noise_queue.pop(0)
                    apply_height_brush(x, y, direction, procedural=True)


            # UI panel background
            pygame.draw.rect(
                screen,
                (30, 30, 30),
                pygame.Rect(0, 0, UI_PANEL_W, WINDOW_H)
            )

            if right_panel:
                pygame.draw.rect(
                screen,
                (30, 30, 30),
                pygame.Rect(WINDOW_H - UI_PANEL_W, 0, UI_PANEL_W, WINDOW_H)
                )
            # Label
            label = SMALL_FONT.render("Choose painting mode:", True, WHITE)
            screen.blit(label, mode_label_pos)

            if active_input != "radius":
                input_buffers["radius"] = str(brush_radius)

            if active_input != "strength":
                input_buffers["strength"] = f"{brush_strength:.3f}"

            label = SMALL_FONT.render("Noise generation mode:", True, WHITE)
            screen.blit(label, (20, WINDOW_H - 280))

            draw_mode_button(btn_noise_stamp,  "Stamp-based", noise_mode == NOISE_STAMP)
            draw_mode_button(btn_noise_value,  "Value noise", noise_mode == NOISE_VALUE)
            draw_mode_button(btn_noise_points, "Point-based", noise_mode == NOISE_POINTS)


            draw_mode_button(btn_height, "Height", paint_mode == MODE_HEIGHT)
            draw_mode_button(btn_overhang, "Overhangs", paint_mode == MODE_OVERHANG)
            draw_mode_button(btn_block, "Block / Material", paint_mode == MODE_BLOCK)

            draw_arrow_button(radius_minus, "-")
            draw_arrow_button(radius_plus, "+")

            draw_arrow_button(strength_minus, "-")
            draw_arrow_button(strength_plus, "+")

            draw_arrow_button(power_minus, "-")
            draw_arrow_button(power_plus, "+")

            draw_button(btn_save, "Save Terrain")

            draw_button(btn_noise, "Noise Terrain")

            draw_button(btn_iso_preview, "Launch 3D Preview")

            if noise_mode == NOISE_POINTS:
                right_panel = True
            else:
                right_panel = False

            if right_panel and paint_mode == MODE_HEIGHT:
                draw_mode_button(btn_primary, "Primary", layer_mode == "primary")
                draw_mode_button(btn_secondary, "Secondary", layer_mode == "secondary")
                draw_mode_button(btn_tertiary, "Tertiary", layer_mode == "tertiary")

                if layer_mode == "primary":
                    sync_slider_input(primary_radius_slider, primary_radius_input)
                    point_features["primary"]["radius"] = int(primary_radius_input.value)
                    draw_slider(primary_radius_slider)
                    draw_number_input(primary_radius_input)

                    sync_slider_input(primary_spacing_slider, primary_spacing_input)
                    point_features["primary"]["spacing"] = int(primary_spacing_input.value)
                    draw_slider(primary_spacing_slider)
                    draw_number_input(primary_spacing_input)

                    sync_slider_input(primary_strength_slider, primary_strength_input)
                    point_features["primary"]["strength"] = (primary_strength_input.value)
                    draw_slider(primary_strength_slider)
                    draw_number_input(primary_strength_input)

                    sync_slider_input(primary_power_slider, primary_power_input)
                    point_features["primary"]["power"] = (primary_power_input.value)
                    draw_slider(primary_power_slider)
                    draw_number_input(primary_power_input)

                    sync_slider_input(primary_density_slider, primary_density_input)
                    point_features["primary"]["density"] = (primary_density_input.value)
                    draw_slider(primary_density_slider)
                    draw_number_input(primary_density_input)

                    sync_slider_input(primary_jitter_slider, primary_jitter_input)
                    point_features["primary"]["jitter"] = (primary_jitter_input.value)
                    draw_slider(primary_jitter_slider)
                    draw_number_input(primary_jitter_input)

                    sync_slider_input(primary_count_slider, primary_count_input)
                    point_features["primary"]["count"] = int(primary_count_input.value)
                    draw_slider(primary_count_slider)
                    draw_number_input(primary_count_input)

                elif layer_mode == "secondary":
                    sync_slider_input(secondary_radius_slider, secondary_radius_input)
                    point_features["secondary"]["radius"] = int(secondary_radius_input.value)
                    draw_slider(secondary_radius_slider)
                    draw_number_input(secondary_radius_input)

                    sync_slider_input(secondary_spacing_slider, secondary_spacing_input)
                    point_features["secondary"]["spacing"] = int(secondary_spacing_input.value)
                    draw_slider(secondary_spacing_slider)
                    draw_number_input(secondary_spacing_input)

                    sync_slider_input(secondary_strength_slider, secondary_strength_input)
                    point_features["secondary"]["strength"] = (secondary_strength_input.value)
                    draw_slider(secondary_strength_slider)
                    draw_number_input(secondary_strength_input)

                    sync_slider_input(secondary_power_slider, secondary_power_input)
                    point_features["secondary"]["power"] = (secondary_power_input.value)
                    draw_slider(secondary_power_slider)
                    draw_number_input(secondary_power_input)

                    sync_slider_input(secondary_density_slider, secondary_density_input)
                    point_features["secondary"]["density"] = (secondary_density_input.value)
                    draw_slider(secondary_density_slider)
                    draw_number_input(secondary_density_input)

                    sync_slider_input(secondary_jitter_slider, secondary_jitter_input)
                    point_features["secondary"]["jitter"] = (secondary_jitter_input.value)
                    draw_slider(secondary_jitter_slider)
                    draw_number_input(secondary_jitter_input)

                    sync_slider_input(secondary_count_slider, secondary_count_input)
                    point_features["secondary"]["count"] = int(secondary_count_input.value)
                    draw_slider(secondary_count_slider)
                    draw_number_input(secondary_count_input)

                elif layer_mode == "tertiary":
                    sync_slider_input(tertiary_radius_slider, tertiary_radius_input)
                    point_features["tertiary"]["radius"] = int(tertiary_radius_input.value)
                    draw_slider(tertiary_radius_slider)
                    draw_number_input(tertiary_radius_input)

                    sync_slider_input(tertiary_spacing_slider, tertiary_spacing_input)
                    point_features["tertiary"]["spacing"] = int(tertiary_spacing_input.value)
                    draw_slider(tertiary_spacing_slider)
                    draw_number_input(tertiary_spacing_input)

                    sync_slider_input(tertiary_strength_slider, tertiary_strength_input)
                    point_features["tertiary"]["strength"] = (tertiary_strength_input.value)
                    draw_slider(tertiary_strength_slider)
                    draw_number_input(tertiary_strength_input)

                    sync_slider_input(tertiary_power_slider, tertiary_power_input)
                    point_features["tertiary"]["power"] = (tertiary_power_input.value)
                    draw_slider(tertiary_power_slider)
                    draw_number_input(tertiary_power_input)

                    sync_slider_input(tertiary_density_slider, tertiary_density_input)
                    point_features["tertiary"]["density"] = (tertiary_density_input.value)
                    draw_slider(tertiary_density_slider)
                    draw_number_input(tertiary_density_input)

                    sync_slider_input(tertiary_jitter_slider, tertiary_jitter_input)
                    point_features["tertiary"]["jitter"] = (tertiary_jitter_input.value)
                    draw_slider(tertiary_jitter_slider)
                    draw_number_input(tertiary_jitter_input)

                    sync_slider_input(tertiary_count_slider, tertiary_count_input)
                    point_features["tertiary"]["count"] = int(tertiary_count_input.value)
                    draw_slider(tertiary_count_slider)
                    draw_number_input(tertiary_count_input)        
                screen.blit(
                    SMALL_FONT.render("Radius:", True, WHITE),
                    (WINDOW_W - 270, 235)
                )

                screen.blit(
                    SMALL_FONT.render("Spacing:", True, WHITE),
                    (WINDOW_W - 270, 295)
                )

                screen.blit(
                    SMALL_FONT.render("Strength:", True, WHITE),
                    (WINDOW_W - 270, 355)
                )

                screen.blit(
                    SMALL_FONT.render("Power:", True, WHITE),
                    (WINDOW_W - 270, 415)
                )

                screen.blit(
                    SMALL_FONT.render("Density:", True, WHITE),
                    (WINDOW_W - 270, 475)
                )

                screen.blit(
                    SMALL_FONT.render("Jitter:", True, WHITE),
                    (WINDOW_W - 270, 535)
                )

                screen.blit(
                    SMALL_FONT.render("Count:", True, WHITE),
                    (WINDOW_W - 270, 595)
                )
                # Brush labels
                screen.blit(
                    SMALL_FONT.render("Brush radius:", True, WHITE),
                    (20, brush_label_y)
                )

                if layer_mode == "tertiary":
                    draw_toggle_button(button_erosion, "Erosion", erosion)

                draw_toggle_button(button_procedural, "Procedural", procedural_noise)

                if procedural_noise:
                    draw_value_box(seed_rect, input_buffers["seed"] if active_input == "seed" else str(seed))

            elif right_panel and paint_mode == MODE_OVERHANG:
                draw_toggle_button(btn_red, "Blue" if red else "Magenta", red)


            screen.blit(
                SMALL_FONT.render("Brush strength:", True, WHITE),
                (20, brush_label_y + 60)
            )

            mode_text = "Lower" if raise_mode else "Raise"
            screen.blit(
                SMALL_FONT.render(f"Brush Mode: {mode_text}", True, WHITE),
                (20, brush_label_y + 180)
            )

            screen.blit(
                SMALL_FONT.render("Brush falloff power:", True, WHITE),
                (20, brush_label_y + 120)
            )

            screen.blit(
                SMALL_FONT.render("Maximum terrain height:", True, WHITE),
                (20, brush_label_y + 200)
            )

            screen.blit(
                SMALL_FONT.render("Minimum terrain height", True, WHITE),
                (20, brush_label_y + 260)
            )

            
            

            draw_value_box(
                radius_rect,
                input_buffers["radius"] if active_input == "radius" else str(brush_radius)
            )

            draw_value_box(
                strength_rect,
                input_buffers["strength"] if active_input == "strength" else f"{brush_strength:.3f}"
            )

            draw_value_box(
                power_rect,
                input_buffers["power"] if active_input == "power" else f"{brush_power:.2f}"
            )

            draw_value_box(
                max_height_rect,
                input_buffers["max"] if active_input == "max" else str(max_height)
            )

            draw_value_box(
                min_height_rect,
                input_buffers["min"] if active_input == "min" else str(min_height)
            )

            MIN_ZOOM = compute_min_zoom()

            # Scale entire terrain by zoom
            scaled_w = int(terrain_w * zoom)
            scaled_h = int(terrain_h * zoom)

            # Clamp camera so it doesn't go out of bounds
            view_x = max(0, min(view_x, terrain_w - canvas_w / zoom))
            view_y = max(0, min(view_y, terrain_h - canvas_h / zoom))

            # ---- CANVAS LAYOUT ----
            right_panel_w = UI_PANEL_W if right_panel else 0

            usable_w = WINDOW_W - UI_PANEL_W - right_panel_w - 2 * CANVAS_PADDING
            usable_h = WINDOW_H - 2 * CANVAS_PADDING

            canvas_x = UI_PANEL_W + CANVAS_PADDING
            canvas_y = CANVAS_PADDING

            canvas_w = min(int(terrain_w * zoom), usable_w)
            canvas_h = min(int(terrain_h * zoom), usable_h)

            src_rect = pygame.Rect(
                int(view_x * zoom),
                int(view_y * zoom),
                canvas_w,
                canvas_h
            )

            # Draw frame
            pygame.draw.rect(
                screen,
                GRAY,
                pygame.Rect(canvas_x-2, canvas_y-2, min(scaled_w, WINDOW_W), min(scaled_h,WINDOW_H)),2
            )
            canvas_rect = pygame.Rect(canvas_x, canvas_y, canvas_w, canvas_h)
            screen.set_clip(canvas_rect)

            # Draw terrain
            draw_chunks()
            # Debug label
            label = SMALL_FONT.render(
                f"Terrain {terrain_w}x{terrain_h}  Zoom {zoom:.2f}",
                True,
                WHITE
            )
            screen.blit(label, (10, 10))

            mx, my = pygame.mouse.get_pos()
            pos = screen_to_terrain(mx, my)

            if pos:
                cx, cy = pos

                sx = int(canvas_x + (cx - view_x) * zoom)
                sy = int(canvas_y + (cy - view_y) * zoom)

                pygame.draw.circle(
                    screen,
                    (200, 200, 200),
                    (sx, sy),
                    max(1, int((brush_radius - 0.5 * brush_radius) * zoom)),
                    1
                )

    pygame.display.flip()
    clock.tick(60)

pygame.quit()
sys.exit()

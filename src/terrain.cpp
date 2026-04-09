#include <winerror.h>
#ifdef _WIN32
#include <windows.h>
#else
#include <sys/mman.h>
#include <fcntl.h>
#include <unistd.h>
#endif

#include "terrain.hpp"
#include <godot_cpp/variant/utility_functions.hpp>
#include <godot_cpp/classes/rendering_device.hpp>
#include <godot_cpp/classes/random_number_generator.hpp>
#include <godot_cpp/classes/resource.hpp>
#include <godot_cpp/core/class_db.hpp>

#include <godot_cpp/classes/object.hpp>
#include <godot_cpp/core/print_string.hpp>
#include <godot_cpp/classes/node3d.hpp>



#include <iostream>
#include <algorithm>
#include <fstream>
#include <string>
#include <dxgi1_6.h>
#include <dstorage.h>
#include <vector>

#include <godot_cpp/classes/rendering_server.hpp>
#include <godot_cpp/classes/rendering_device.hpp>
#include <wrl/client.h> // For Microsoft::WRL::ComPtr
#include <d3d12.h>
using Microsoft::WRL::ComPtr;
using namespace godot;

Terrain::Terrain() { }
Terrain::~Terrain() { }

void Terrain::set_render_dis(int p_render_dis) {
    if (render_dis != p_render_dis) {
        render_dis = p_render_dis;
        update_render_templates();
    }
}

// ─────────────────────────────────────────────────────────
// DIRECTSTORAGE INIT — persists factory and queue as members
// ─────────────────────────────────────────────────────────

bool Terrain::init_directstorage() {
    HRESULT hr = DStorageGetFactory(IID_PPV_ARGS(&ds_factory));
    if (FAILED(hr)) {
        print_error("[Vertex-HGT] DStorageGetFactory failed.");
        return false;
    }

    ds_factory->SetStagingBufferSize(256 * 1024 * 1024);

    DSTORAGE_QUEUE_DESC q{};
    q.Capacity   = DSTORAGE_MAX_QUEUE_CAPACITY;
    q.Priority   = DSTORAGE_PRIORITY_HIGH;
    q.SourceType = DSTORAGE_REQUEST_SOURCE_FILE;
    q.Device     = d3d12_device; // set this before calling if you have the device

    hr = ds_factory->CreateQueue(&q, IID_PPV_ARGS(&ds_queue));
    if (FAILED(hr)) {
        print_error("[Vertex-HGT] DirectStorage queue creation failed.");
        return false;
    }

    return true;
}

// ─────────────────────────────────────────────────────────
// GPU BUFFER CREATION
// Fixed size allocated once — never reallocated
// ─────────────────────────────────────────────────────────

bool Terrain::create_gpu_buffer() {
    if (!d3d12_device) {
        print_error("[Vertex-HGT] No D3D12 device available for buffer creation.");
        return false;
    }

    constexpr UINT32 HMAP_BYTES       = 4096 * sizeof(uint16_t);
    constexpr UINT32 VIMP_BYTES       = 4096 * sizeof(uint8_t);
    constexpr UINT32 CHUNK_GPU_STRIDE = HMAP_BYTES + VIMP_BYTES;

    UINT64 buffer_size = (UINT64)total_slots * CHUNK_GPU_STRIDE;

    D3D12_HEAP_PROPERTIES heap{};
    heap.Type = D3D12_HEAP_TYPE_DEFAULT;

    D3D12_RESOURCE_DESC desc{};
    desc.Dimension          = D3D12_RESOURCE_DIMENSION_BUFFER;
    desc.Width              = buffer_size;
    desc.Height             = 1;
    desc.DepthOrArraySize   = 1;
    desc.MipLevels          = 1;
    desc.SampleDesc.Count   = 1;
    desc.Layout             = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
    desc.Flags              = D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS;

    HRESULT hr = d3d12_device->CreateCommittedResource(
        &heap,
        D3D12_HEAP_FLAG_NONE,
        &desc,
        D3D12_RESOURCE_STATE_COMMON,
        nullptr,
        IID_PPV_ARGS(&gpu_buffer)
    );

    if (FAILED(hr)) {
        print_error("[Vertex-HGT] GPU buffer creation failed.");
        return false;
    }

    print_line("[Vertex-HGT] GPU buffer allocated: " +
        String::num_uint64(buffer_size) + " bytes.");
    return true;
}

// ─────────────────────────────────────────────────────────
// STREAM NEW CHUNKS
// Takes a directional template (list of offsets for incoming chunks)
// Finds free slots, assigns chunks, fires DirectStorage
// ─────────────────────────────────────────────────────────

void Terrain::stream_new_chunks(const vector<int64_t>& incoming_offsets) {
    if (incoming_offsets.empty()) return;

    constexpr UINT32 HMAP_BYTES       = 4096 * sizeof(uint16_t);
    constexpr UINT32 VIMP_BYTES       = 4096 * sizeof(uint8_t);
    constexpr UINT32 CHUNK_GPU_STRIDE = HMAP_BYTES + VIMP_BYTES;

    // Build list of chunk IDs currently needed in the full template
    int64_t r2 = (int64_t)render_dis * render_dis;
    vector<int> needed_ids;
    for (int z = -render_dis; z <= render_dis; z++) {
        for (int x = -render_dis; x <= render_dis; x++) {
            if ((int64_t)x*x + (int64_t)z*z > r2) continue;
            needed_ids.push_back(encode_chunk_id(chunk_x + x, chunk_z + z));
        }
    }

    // Find slots that are no longer needed — these are free for incoming chunks
    vector<int> free_slots;
    for (int slot = 0; slot < (int)slot_chunk_map.size(); slot++) {
        int id = slot_chunk_map[slot];
        if (id == -1) {
            free_slots.push_back(slot); // empty slot
            continue;
        }
        bool still_needed = false;
        for (int nid : needed_ids) {
            if (nid == id) { still_needed = true; break; }
        }
        if (!still_needed) free_slots.push_back(slot);
    }

    // Match incoming offsets to free slots and enqueue DirectStorage requests
    int stream_count = std::min((int)incoming_offsets.size(),
                                 (int)free_slots.size());

    if (!ds_ready || !ds_queue || !gpu_buffer) {
        // Tier 2/3/4: no DirectStorage, skip for now
        // (Vulkan fallback goes here in future tiers)
        print_line("[Vertex-HGT] Skipping stream — DirectStorage not ready.");
        return;
    }

    for (int i = 0; i < stream_count; i++) {
        int64_t offset   = incoming_offsets[i];
        int     slot     = free_slots[i];

        // Decode offset back to absolute chunk x,z
        int rel_x = (int)(offset % terrain_width);
        int rel_z = (int)(offset / terrain_width);
        int abs_x = chunk_x + rel_x;
        int abs_z = chunk_z + rel_z;
        int chunk_id = encode_chunk_id(abs_x, abs_z);

        // Update slot map
        slot_chunk_map[slot] = chunk_id;

        // Build file paths
        String base     = absolute_path;
        String hmap_str = base + String::num_int64(chunk_id) + ".hmap";
        String vimp_str = base + String::num_int64(chunk_id) + ".vimp";

        std::wstring hmap_w(hmap_str.ptr(),
                            hmap_str.ptr() + hmap_str.length());
        std::wstring vimp_w(vimp_str.ptr(),
                            vimp_str.ptr() + vimp_str.length());

        IDStorageFile* hmap_file = nullptr;
        IDStorageFile* vimp_file = nullptr;

        if (FAILED(ds_factory->OpenFile(hmap_w.c_str(),
                IID_PPV_ARGS(&hmap_file)))) {
            print_error("[Vertex-HGT] Cannot open: " + hmap_str);
            continue;
        }
        if (FAILED(ds_factory->OpenFile(vimp_w.c_str(),
                IID_PPV_ARGS(&vimp_file)))) {
            print_error("[Vertex-HGT] Cannot open: " + vimp_str);
            hmap_file->Release();
            continue;
        }

        UINT64 slot_base = (UINT64)slot * CHUNK_GPU_STRIDE;

        DSTORAGE_REQUEST hmap_req{};
        hmap_req.Options.SourceType          = DSTORAGE_REQUEST_SOURCE_FILE;
        hmap_req.Options.DestinationType     = DSTORAGE_REQUEST_DESTINATION_BUFFER;
        hmap_req.Options.CompressionFormat   = DSTORAGE_COMPRESSION_FORMAT_NONE;
        hmap_req.Source.File.Source          = hmap_file;
        hmap_req.Source.File.Offset          = 0;
        hmap_req.Source.File.Size            = HMAP_BYTES;
        hmap_req.Destination.Buffer.Resource = gpu_buffer;
        hmap_req.Destination.Buffer.Offset   = slot_base;
        hmap_req.Destination.Buffer.Size     = HMAP_BYTES;
        hmap_req.UncompressedSize            = HMAP_BYTES;
        ds_queue->EnqueueRequest(&hmap_req);

        DSTORAGE_REQUEST vimp_req{};
        vimp_req.Options.SourceType          = DSTORAGE_REQUEST_SOURCE_FILE;
        vimp_req.Options.DestinationType     = DSTORAGE_REQUEST_DESTINATION_BUFFER;
        vimp_req.Options.CompressionFormat   = DSTORAGE_COMPRESSION_FORMAT_NONE;
        vimp_req.Source.File.Source          = vimp_file;
        vimp_req.Source.File.Offset          = 0;
        vimp_req.Source.File.Size            = VIMP_BYTES;
        vimp_req.Destination.Buffer.Resource = gpu_buffer;
        vimp_req.Destination.Buffer.Offset   = slot_base + HMAP_BYTES;
        vimp_req.Destination.Buffer.Size     = VIMP_BYTES;
        vimp_req.UncompressedSize            = VIMP_BYTES;
        ds_queue->EnqueueRequest(&vimp_req);

        hmap_file->Release();
        vimp_file->Release();
    }

    // Submit entire batch + signal fence
    ds_queue->EnqueueSignal(ds_fence, ++ds_fence_value);
    ds_queue->Submit();

    print_line("[Vertex-HGT] Streamed " + String::num_int64(stream_count) +
        " chunks. Fence value: " + String::num_uint64(ds_fence_value));
}

// ─────────────────────────────────────────────────────────
// HELPERS
// ─────────────────────────────────────────────────────────

int Terrain::encode_chunk_id(int abs_x, int abs_z) const {
    return (abs_x + 100000) * 1000000 + (abs_z + 100000);
}

Vector2i Terrain::decode_chunk_id(int chunk_id) const {
    int abs_z = (chunk_id % 1000000) - 100000;
    int abs_x = (chunk_id / 1000000) - 100000;
    return Vector2i(abs_x, abs_z);
}

int Terrain::get_lod_ring(int dx, int dz) const {
    float dist = Math::sqrt((float)(dx*dx + dz*dz));
    int ring = (int)(dist / (float)render_dis * 4.0f);
    return CLAMP(ring, 0, 3);
}

bool Terrain::initialize_directstorage(ID3D12Device* d3d12_device) {
    HRESULT hr = DStorageGetFactory(IID_PPV_ARGS(&ds_factory));
    if (FAILED(hr)) {
        UtilityFunctions::printerr("[Vertex-HGT] DStorageGetFactory failed. "
                                   "Is dstorage.dll present?");
        return false;
    }

    // Staging buffer size — 256MB is recommended for terrain streaming
    ds_factory->SetStagingBufferSize(256 * 1024 * 1024);

    DSTORAGE_QUEUE_DESC queue_desc{};
    queue_desc.Capacity   = DSTORAGE_MAX_QUEUE_CAPACITY;
    queue_desc.Priority   = DSTORAGE_PRIORITY_HIGH;
    queue_desc.SourceType = DSTORAGE_REQUEST_SOURCE_FILE;
    queue_desc.Device     = d3d12_device; // GPU destination requires device

    hr = ds_factory->CreateQueue(&queue_desc, IID_PPV_ARGS(&ds_queue));
    if (FAILED(hr)) {
        UtilityFunctions::printerr("[Vertex-HGT] Failed to create DirectStorage queue.");
        return false;
    }

    ds_initialized = true;
    UtilityFunctions::print("[Vertex-HGT] DirectStorage initialized.");
    return true;
}

void Terrain::update_render_templates() {
    full_circle_template.clear();
    north_template.clear();
    east_template.clear();
    ne_template.clear();
    nw_template.clear();
    south_template.clear();
    west_template.clear();
    sw_template.clear();
    se_template.clear();

    // Ensure we have a valid width to calculate 1D offsets
    if (terrain_width <= 0) return;

    int64_t r2 = (int64_t)render_dis * render_dis;
    int64_t W = (int64_t)terrain_width;

    auto is_inside = [r2](int x, int z) {
        return ((int64_t)x * x + (int64_t)z * z) <= r2;
    };

    // Temporary storage to allow sorting the full circle by distance
    struct OffsetPair { int x, z; int64_t dist_sq; };
	vector<OffsetPair> circle_pairs;

    // Iterate through the bounding box of the circle
    for (int z = -render_dis; z <= render_dis; z++) {
        for (int x = -render_dis; x <= render_dis; x++) {
            if (!is_inside(x, z)) continue;

            int64_t offset = (int64_t)z * W + x;

            // 1. Collect for Full Circle
            circle_pairs.push_back({x, z, (int64_t)x * x + (int64_t)z * z});

            // 2. Directional Templates (Leading Edges) using 1D offsets
            // North (dZ = -1): New if outside when shifted back to old center (0, 1)
            if (!is_inside(x, z + 1)) {
                north_template.push_back(offset);
            }
            // East (dX = 1): New if outside when shifted back to old center (-1, 0)
            if (!is_inside(x - 1, z)) {
                east_template.push_back(offset);
            }
            // North-East (dX = 1, dZ = -1): Old center was at (-1, 1)
            if (!is_inside(x - 1, z + 1)) {
                ne_template.push_back(offset);
            }
            // North-West (dX = -1, dZ = -1): Old center was at (1, 1)
            if (!is_inside(x + 1, z + 1)) {
                nw_template.push_back(offset);
            }

            if (!is_inside(x, z - 1)) {
                south_template.push_back(offset);
            }
            // West (dX = -1): New if outside when shifted back to old center (+1, 0)
            if (!is_inside(x + 1, z)) {
                west_template.push_back(offset);
            }
            // South-West (dX = -1, dZ = +1): Old center was at (1, -1)
            if (!is_inside(x + 1, z - 1)) {
                sw_template.push_back(offset);
            }
            // South-East (dX = +1, dZ = +1): Old center was at (-1, -1)
            if (!is_inside(x - 1, z - 1)) {
                se_template.push_back(offset);
            }
        }
    }

    // Sort full circle from top to down (row-major order) then convert to 1D offsets
    std::sort(circle_pairs.begin(), circle_pairs.end(), [](const OffsetPair& a, const OffsetPair& b) {
        if (a.z != b.z) return a.z < b.z;
        return a.x < b.x;
    });
    for (const auto& p : circle_pairs) {
        full_circle_template.push_back((int64_t)p.z * W + p.x);
    }

    print_line("Terrain templates updated. Full circle size: " + String::num_uint64(full_circle_template.size()));
}

bool Terrain::check_direct_storage_support() const  {
    IDStorageFactory* factory = nullptr;

    // Attempt to create the DirectStorage factory
    HRESULT hr = DStorageGetFactory(IID_PPV_ARGS(&factory));
    if (FAILED(hr)) {
        return false; // DirectStorage runtime isn't even installed on the OS
    }

    // Create a temporary queue to check its properties
    DSTORAGE_QUEUE_DESC queueDesc = {};
    queueDesc.Capacity = DSTORAGE_MIN_QUEUE_CAPACITY;
    queueDesc.Priority = DSTORAGE_PRIORITY_NORMAL;
    queueDesc.SourceType = DSTORAGE_REQUEST_SOURCE_FILE;

    IDStorageQueue* queue = nullptr;
    if (SUCCEEDED(factory->CreateQueue(&queueDesc, IID_PPV_ARGS(&queue)))) {
        // Query if GPU decompression path is supported
        DSTORAGE_COMPRESSION_SUPPORT support = {};
        // Query the API to see if it routes through the optimized driver path
        queue->QueryInterface(__uuidof(IDStorageQueue2), (void**)&queue);

        // This checks if the GPU and drivers are ready for DirectStorage!
        queue->Release();
        factory->Release();
        return true;
    }

    factory->Release();
    return false;
}

int Terrain::check_dual_gpu_setup() const {
    IDXGIFactory6* factory = nullptr;
    // Create the DXGI Factory to scan hardware
    HRESULT hr = CreateDXGIFactory1(__uuidof(IDXGIFactory6), (void**)&factory);

    if (FAILED(hr)) {
        print_line("DXGI Factory failed to initialize.");
        return false;
    }

    bool found_igpu = false;
    bool found_dgpu = false;
    IDXGIAdapter1* adapter = nullptr;
    UINT i = 0;

    // Loop through all available GPUs on the system
    while (factory->EnumAdapters1(i, &adapter) != DXGI_ERROR_NOT_FOUND) {
        DXGI_ADAPTER_DESC1 desc;
        adapter->GetDesc1(&desc);

        // Ignore software/emulated renderers (like Microsoft Basic Render Driver)
        if (desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE) {
            adapter->Release();
            i++;
            continue;
        }

        // Convert the wide-character GPU name to a Godot String for logging
        String gpu_name = String(desc.Description);

        // Rule of thumb: If it has more than 512MB of dedicated VRAM, it's a dGPU
        // (iGPUs report 0 or very low dedicated VRAM because they share system RAM)
        if (desc.DedicatedVideoMemory > 512 * 1024 * 1024) {
            found_dgpu = true;
            print_line("Detected dGPU: " + gpu_name);
        } else {
            found_igpu = true;
            print_line("Detected iGPU: " + gpu_name);
        }

        adapter->Release();
        i++;
    }

    factory->Release();

    // Print out the final diagnosis
    if (found_igpu && found_dgpu) {
        return 1;
    } else if (found_dgpu) {
        print_line("Detected only a dedicated GPU.");
        return 2;
    } else if (found_igpu) {
        print_line("Detected only an integrated GPU.");
        return 3;
    }

    return 0;
}

int Terrain::get_json_int_value(const std::string& json_str, const std::string& key) const {
    // 1. Look for the key wrapped in quotes (e.g., "width")
    std::string search_key = "\"" + key + "\"";
    size_t key_pos = json_str.find(search_key);

    if (key_pos == std::string::npos) {
        return -1; // Key not found
    }

    // 2. Find the colon after the key
    size_t colon_pos = json_str.find(":", key_pos);
    if (colon_pos == std::string::npos) return -1;

    // 3. Find the first digit of the number after the colon
    size_t start_pos = json_str.find_first_of("0123456789-", colon_pos);
    if (start_pos == std::string::npos) return -1;

    // 4. Find where the number ends (space, comma, or closing brace)
    size_t end_pos = json_str.find_first_of(" ,\n\r}", start_pos);

    // 5. Extract the substring and convert to an integer
    std::string value_str = json_str.substr(start_pos, end_pos - start_pos);
    return std::stoi(value_str);
}
//this comment is here
int Terrain::load_simple_manifest(const std::string& absolute_path,bool is_width) const //return width if is_width == true
{
    std::ifstream file(absolute_path, std::ios::in | std::ios::binary | std::ios::ate);
    if (!file.is_open()) print_error("Failed to open file");

    std::streamsize size = file.tellg();
    file.seekg(0, std::ios::beg);

    std::string json_content(size, ' ');
    file.read(&json_content[0], size);
    file.close();

    // Extract your two NEW values directly based on the new keys!
    int width = get_json_int_value(json_content, "width");
    int total_chunks = get_json_int_value(json_content, "total_chunks");

    std::cout << "Vertex-HGT: Width = " << width << ", Total Chunks = " << total_chunks << std::endl;

    if (is_width) return width;
    else return total_chunks;
}

PackedInt64Array Terrain::get_template_chunks(int64_t p_offset, int p_command) {
    const std::vector<int64_t>* src = nullptr;
    bool invert = false;

    switch (p_command) {
        case 0: src = &full_circle_template; break;
        case 1: src = &north_template;       break;
        case 2: src = &east_template;        break;
        case 3: src = &ne_template;          break;
        case 4: src = &nw_template;          break;
        case 5: src = &north_template; invert = true; break;
        case 6: src = &east_template;  invert = true; break;
        case 7: src = &ne_template;    invert = true; break;
        case 8: src = &nw_template;    invert = true; break;
        default: return PackedInt64Array();
    }

    PackedInt64Array new_chunks;
    size_t sz = src->size();
    new_chunks.resize(sz);
    
    int64_t* write_ptr = new_chunks.ptrw();
    const int64_t* read_ptr = src->data();

    if (!invert) {
        for (size_t i = 0; i < sz; ++i) {
            write_ptr[i] = read_ptr[i] + p_offset;
        }
    } else {
        for (size_t i = 0; i < sz; ++i) {
            write_ptr[i] = -read_ptr[i] + p_offset;
        }
    }

    return new_chunks;
}

void Terrain::load_chunks()
{
    // ── One-time initialization ────────────────────────────
    // Read manifest and set up GPU pipeline on first call only
    if (total_slots == 0) {
        terrain_width    = load_simple_manifest(absolute_path.utf8().get_data(), true);
        max_chunk_count  = load_simple_manifest(absolute_path.utf8().get_data(), false);

        // Compute how many slots the circular template needs
        total_slots = 0;
        int64_t r2 = (int64_t)render_dis * render_dis;
        for (int z = -render_dis; z <= render_dis; z++)
            for (int x = -render_dis; x <= render_dis; x++)
                if ((int64_t)x*x + (int64_t)z*z <= r2)
                    total_slots++;

        // Initialize slot map — all empty
        slot_chunk_map.assign(total_slots, -1);

        // Build templates once
        update_render_templates();

        // Initialize DirectStorage pipeline
        bool ds_supported = check_direct_storage_support();
        if (ds_supported && using_directx) {
            if (init_directstorage() && create_gpu_buffer()) {
                ds_ready = true;
                print_line("[Vertex-HGT] Tier 1: DirectStorage pipeline ready.");
            }
        } else {
            int gpu_setup = check_dual_gpu_setup();
            if (gpu_setup == 1)      print_line("[Vertex-HGT] Tier 2: Hybrid GPU.");
            else if (gpu_setup == 2) print_line("[Vertex-HGT] Tier 4: dGPU only.");
            else if (gpu_setup == 3) print_line("[Vertex-HGT] Tier 3: iGPU only.");
        }

        // First load: stream full circle
        stream_new_chunks(full_circle_template);
        return;
    }

    // ── Incremental update: determine movement direction ───
    int dx = chunk_x - (int)(last_x / 64.0f); // delta since last chunk pos
    int dz = chunk_z - (int)(last_z / 64.0f);

    const vector<int64_t>* incoming = nullptr;

    if      (dx ==  1 && dz ==  0) incoming = &east_template;
    else if (dx == -1 && dz ==  0) incoming = &west_template;
    else if (dx ==  0 && dz == -1) incoming = &north_template;
    else if (dx ==  0 && dz ==  1) incoming = &south_template;
    else if (dx ==  1 && dz == -1) incoming = &ne_template;
    else if (dx == -1 && dz == -1) incoming = &nw_template;
    else if (dx ==  1 && dz ==  1) incoming = &se_template;
    else if (dx == -1 && dz ==  1) incoming = &sw_template;
    else {
        // Player teleported or large movement — reload full circle
        slot_chunk_map.assign(total_slots, -1);
        stream_new_chunks(full_circle_template);
        return;
    }

    stream_new_chunks(*incoming);

}


void Terrain::_notification(int p_what) {
    switch (p_what) {
        case NOTIFICATION_POST_ENTER_TREE:
            // Enable internal physics processing

            set_physics_process_internal(true);
            break;

        case NOTIFICATION_INTERNAL_PHYSICS_PROCESS: {
            // High-performance logic here
            // 1. Calculate what the chunk coordinates SHOULD be based on current position
            int target_chunk_x = floor((play_x + 32.0 ) / 64.0);
            int target_chunk_z = floor((play_z + 32.0) / 64.0);

            // 2. Check if that differs from the CURRENT chunk coordinates
            if (target_chunk_x != chunk_x || target_chunk_z != chunk_z)
            {
                chunk_x = target_chunk_x;
                chunk_z = target_chunk_z;
                
                load_chunks();
            }
        } break;
    }
}

#include <winerror.h>
#ifdef _WIN32
#include <windows.h>
#else
#include <sys/mman.h>
#include <fcntl.h>
#include <unistd.h>
#endif

#include "terrain.hpp"
//#include "godot_cpp/variant/utility_functions.hpp"
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

using namespace godot;

Terrain::Terrain() { }
Terrain::~Terrain() { }

void Terrain::set_render_dis(int p_render_dis) {
    if (render_dis != p_render_dis) {
        render_dis = p_render_dis;
        update_render_templates();
    }
}

void Terrain::update_render_templates() {
    full_circle_template.clear();
    north_template.clear();
    east_template.clear();
    ne_template.clear();
    nw_template.clear();

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
        }
    }

    // Sort full circle by distance (closest first) then convert to 1D offsets
    std::sort(circle_pairs.begin(), circle_pairs.end(), [](const OffsetPair& a, const OffsetPair& b) {
        return a.dist_sq < b.dist_sq;
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

void Terrain::load_chunks()
{
    terrain_width = load_simple_manifest(absolute_path.utf8().get_data(), true);
    max_chunk_count = load_simple_manifest(absolute_path.utf8().get_data(), false);
    print_line(max_chunk_count);
    print_line(terrain_width);

    bool direct_storage_supported = check_direct_storage_support();
    if (direct_storage_supported && using_directx)
    {
        print_line("DirectStorage is supported on this system.");
    }
    else
    {
        print_line("DirectStorage is NOT supported on this system.");
        int dual_gpu_setup = check_dual_gpu_setup();
        if (dual_gpu_setup == 1)    {
            print_line("Hybrid multi-GPU setup detected!");
        } else if (dual_gpu_setup == 2) {
            print_line("Only a dedicated GPU detected.");
        } else if (dual_gpu_setup == 3) {
            print_line("Only an integrated GPU detected.");
        }
    }
    update_render_templates();
    print_line(north_template.size());
    if (using_directx) {
        print_line("Using DirectX for terrain rendering.");
    } else {
        print_line("Using Vulkan for terrain rendering.");
    }

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

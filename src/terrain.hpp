#pragma once

#include "godot_cpp/variant/string.hpp"
#include "godot_cpp/variant/vector2i.hpp"
#include <godot_cpp/variant/packed_int64_array.hpp>
#include <godot_cpp/classes/node3d.hpp>

#include <string>
#include <vector>
#include <dstorage.h>
#include <dxgi1_6.h>
#include <wrl/client.h>
#include <d3d12.h>

using namespace godot;
using namespace std;

using Microsoft::WRL::ComPtr;

class Terrain : public Node3D { 
	GDCLASS(Terrain, Node3D);

public:
	float play_x = 0;
	float play_z = 0;

	float last_x = 0;
	float last_z = 0;

	int chunk_x = 0;
	int chunk_z = 0;

	int render_dis = 32;

	int max_chunk_count=0;
	int terrain_width=0;

	String absolute_path;

	bool using_directx = true;
	//test comment for Git



protected:
	static void _bind_methods()
	{
		ClassDB::bind_method(D_METHOD("load_chunks"), &Terrain::load_chunks);
		ClassDB::bind_method(D_METHOD("get_template_chunks", "offset", "command"), &Terrain::get_template_chunks);
		
		ClassDB::bind_method(godot::D_METHOD("get_play_x"), &Terrain::get_play_x);
        ClassDB::bind_method(godot::D_METHOD("set_play_x", "p_play_x"), &Terrain::set_play_x);
        ClassDB::add_property("Terrain", godot::PropertyInfo(godot::Variant::FLOAT, "play_x"), "set_play_x", "get_play_x");

		ClassDB::bind_method(godot::D_METHOD("get_play_z"), &Terrain::get_play_z);
        ClassDB::bind_method(godot::D_METHOD("set_play_z", "p_play_z"), &Terrain::set_play_z);
        ClassDB::add_property("Terrain", godot::PropertyInfo(godot::Variant::FLOAT, "play_z"), "set_play_z", "get_play_z");

		ClassDB::bind_method(godot::D_METHOD("get_max_chunk_count"), &Terrain::get_max_chunk_count);
        ClassDB::bind_method(godot::D_METHOD("set_max_chunk_count", "p_max_chunk_count"), &Terrain::set_max_chunk_count);
        ClassDB::add_property("Terrain", godot::PropertyInfo(godot::Variant::INT, "max_chunk_count"), "set_max_chunk_count", "get_max_chunk_count");

		ClassDB::bind_method(godot::D_METHOD("get_terrain_width"), &Terrain::get_terrain_width);
        ClassDB::bind_method(godot::D_METHOD("set_terrain_width", "p_terrain_width"), &Terrain::set_terrain_width);
        ClassDB::add_property("Terrain", godot::PropertyInfo(godot::Variant::INT, "terrain_width"), "set_terrain_width", "get_terrain_width");

		ClassDB::bind_method(godot::D_METHOD("get_render_dis"), &Terrain::get_render_dis);
        ClassDB::bind_method(godot::D_METHOD("set_render_dis", "p_render_dis"), &Terrain::set_render_dis);
        ClassDB::add_property("Terrain", godot::PropertyInfo(godot::Variant::INT, "render_dis"), "set_render_dis", "get_render_dis");

		ClassDB::bind_method(godot::D_METHOD("get_absolute_path"), &Terrain::get_absolute_path);
        ClassDB::bind_method(godot::D_METHOD("set_absolute_path", "p_absolute_path"), &Terrain::set_absolute_path);
        ClassDB::add_property("Terrain", godot::PropertyInfo(godot::Variant::STRING, "absolute_path"), "set_absolute_path", "get_absolute_path");

		ClassDB::bind_method(godot::D_METHOD("get_using_directx"), &Terrain::get_using_directx);
        ClassDB::bind_method(godot::D_METHOD("set_using_directx", "p_using_directx"), &Terrain::set_using_directx);
        ClassDB::add_property("Terrain", godot::PropertyInfo(godot::Variant::BOOL, "using_directx"), "set_using_directx", "get_using_directx");
	}

public: 
	Terrain();
	~Terrain();

	void set_play_x(int p_play_x) { play_x = p_play_x; }
    int get_play_x() const { return play_x; }

	void set_play_z(int p_play_z) { play_z = p_play_z; }
    int get_play_z() const { return play_z; }

	void set_max_chunk_count(int p_max_chunk_count) { max_chunk_count = p_max_chunk_count; }
    int get_max_chunk_count() const { return max_chunk_count; }

	void set_terrain_width(int p_terrain_width) { terrain_width = p_terrain_width; }
    int get_terrain_width() const { return terrain_width; }

	void set_render_dis(int p_render_dis);
    int get_render_dis() const { return render_dis; }

	void set_using_directx(bool p_using_directx) { using_directx = p_using_directx; }
    bool get_using_directx() const { return using_directx; }

	void set_absolute_path(String p_absolute_path) { absolute_path = p_absolute_path; }
    String get_absolute_path() const { return absolute_path; }

	void load_chunks();
	PackedInt64Array get_template_chunks(int64_t p_offset, int p_command);
	void _notification(int p_what);

	int get_json_int_value(const std::string& json_str, const std::string& key) const;
	int load_simple_manifest(const std::string& absolute_path, bool is_width) const;

	bool check_direct_storage_support() const;
	int check_dual_gpu_setup() const;

	bool initialize_directstorage(ID3D12Device* d3d12_device);

private:

	
	bool ds_initialized = false;

	// Movement templates for efficient chunk loading
	vector<int64_t> full_circle_template;
	vector<int64_t> north_template;
	vector<int64_t> east_template;
	vector<int64_t> ne_template;
	vector<int64_t> nw_template;
	vector<int64_t> south_template;
    vector<int64_t> west_template;
    vector<int64_t> sw_template;
    vector<int64_t> se_template;

	vector<int>  slot_chunk_map;
    int          total_slots    = 0;

	ComPtr<IDStorageFactory> ds_factory;
	ComPtr<IDStorageQueue>   ds_queue;
    ID3D12Device*     d3d12_device = nullptr;
    ID3D12Resource*   gpu_buffer   = nullptr;
    ID3D12Fence*      ds_fence     = nullptr;
    UINT64            ds_fence_value = 0;
    bool              ds_ready     = false;

	bool   init_directstorage();
    bool   create_gpu_buffer();
    void   stream_new_chunks(const vector<int64_t>& incoming_offsets);
    int    encode_chunk_id(int abs_x, int abs_z) const;
    Vector2i decode_chunk_id(int chunk_id) const;
    int    get_lod_ring(int dx, int dz) const;
	void update_render_templates();
};

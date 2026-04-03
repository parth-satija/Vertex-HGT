extends Terrain

@export var player_node: Node3D
@export var render_distance: int = 32

func _ready() -> void:

	#disable warning from here
	print_rich("[color=yellow]WARNING: Disable OpenGL fallback in the project setting and always use Vertex-HGT in forward+ mode to prevent things from going horribly wrong. This warning can be disabled from 'main.gd'[/color]")
	#disable warning from here

	render_dis = render_distance
	var api_name = RenderingServer.get_video_adapter_api_version()
	if api_name.begins_with("12_"):
		using_directx = true
	else:
		using_directx = false
	load_chunks()

func _process(delta: float) -> void:
	play_x = player_node.position.x
	play_z = player_node.position.z
	
	


	
	
	
	

extends Terrain

@export var player_node: Node3D
@export var render_distance: int = 32

func _ready() -> void:
	render_dis = render_distance
	printuuu()

func _process(delta: float) -> void:
	play_x = player_node.position.x
	play_z = player_node.position.z
	
	


	
	
	
	

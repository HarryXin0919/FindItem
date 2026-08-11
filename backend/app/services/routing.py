from dataclasses import dataclass

@dataclass(frozen=True)
class Route:
    controller_id: str
    led_index: int

def drawer_to_route(drawer_number: int) -> Route:
    if not 1 <= drawer_number <= 50:
        raise ValueError("drawer_number must be in 1..50")
    controller_number = (drawer_number - 1) // 10 + 1
    led_index = (drawer_number - 1) % 10
    return Route(controller_id=f"CTRL-{controller_number:02d}", led_index=led_index)

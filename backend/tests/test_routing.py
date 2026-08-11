from app.services.routing import drawer_to_route

def test_boundaries():
    assert drawer_to_route(1).controller_id == "CTRL-01"
    assert drawer_to_route(10).led_index == 9
    assert drawer_to_route(11).controller_id == "CTRL-02"
    assert drawer_to_route(50).controller_id == "CTRL-05"
    assert drawer_to_route(50).led_index == 9

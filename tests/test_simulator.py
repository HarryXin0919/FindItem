import json

import pytest

from simulator.locate import SimulatorError, build_locate_plan, find_item, load_items, main


@pytest.fixture
def items():
    return [
        {
            "id": "FINDIT-001",
            "name": "NEO 电机",
            "name_en": "NEO Motor",
            "device_id": "esp32-001",
            "location": "赛季箱 A · 第 2 格",
            "location_en": "Season Box A · slot 2",
            "duration_sec": 15,
        },
        {
            "id": "FINDIT-002",
            "name": "Falcon 编码器",
            "name_en": "Falcon Encoder",
            "device_id": "esp32-002",
            "duration_sec": 20,
        },
    ]


def test_loads_repository_catalog():
    catalog = load_items()
    assert catalog
    assert catalog[0]["id"] == "FINDIT-001"


@pytest.mark.parametrize("query", ["findit-001", "NEO 电机", "neo motor", "neo"])
def test_finds_item_by_id_or_bilingual_name(items, query):
    assert find_item(items, query)["id"] == "FINDIT-001"


def test_unknown_item_lists_available_ids(items):
    with pytest.raises(SimulatorError, match="FINDIT-001, FINDIT-002"):
        find_item(items, "swerve module")


def test_builds_plan_that_matches_reference_firmware(items):
    plan = build_locate_plan(items[0])

    assert plan["location"]["zh"] == "赛季箱 A · 第 2 格"
    assert plan["target"] == {
        "device_id": "esp32-001",
        "led_id": "esp32-001:gpio-2",
        "led_gpio": 2,
        "led_color": "single-color (hardware-defined)",
        "buzzer_gpio": 5,
    }
    assert plan["effects"]["buzzer"]["frequency_hz"] == 2000
    assert plan["effects"]["buzzer"]["beep_ms"] == 15000
    assert plan["mqtt"]["topic"] == "findit/device/esp32-001/command"
    assert plan["mqtt"]["payload"] == {
        "cmd": "start",
        "item_id": "FINDIT-001",
        "event_id": "sim-findit-001",
        "duration": 15,
        "buzzer": True,
    }


def test_led_only_plan_disables_beep(items):
    plan = build_locate_plan(items[1], duration=7, buzzer=False)

    assert plan["effects"]["buzzer"] == {
        "enabled": False,
        "frequency_hz": 0,
        "beep_ms": 0,
    }
    assert plan["mqtt"]["payload"]["duration"] == 7
    assert plan["mqtt"]["payload"]["buzzer"] is False


@pytest.mark.parametrize("duration", [0, 121, True, 1.5])
def test_rejects_invalid_duration(items, duration):
    with pytest.raises(SimulatorError, match="Duration must be"):
        build_locate_plan(items[0], duration=duration)


def test_cli_json_output(capsys):
    assert main(["NEO Motor", "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["target"]["device_id"] == "esp32-001"

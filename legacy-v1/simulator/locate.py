"""Resolve a catalog item and preview the command sent to its ESP32.

Run from the repository root:

    python -m simulator.locate "NEO Motor"
    python -m simulator.locate FINDIT-002 --no-buzzer --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = BASE_DIR / "config" / "items.json"

# These values mirror esp32/findit_esp32.ino. They are deliberately described
# as GPIO outputs rather than addressable RGB pixels: the reference hardware is
# currently one single-color LED per ESP32-C3.
LED_GPIO = 2
LED_COLOR = "single-color (hardware-defined)"
BUZZER_GPIO = 5
BUZZER_FREQUENCY_HZ = 2000
MAX_DURATION_SEC = 120


class SimulatorError(ValueError):
    """Raised when the catalog or lookup cannot produce a locate plan."""


def load_items(path: Path = DEFAULT_CATALOG) -> list[dict[str, Any]]:
    """Load and minimally validate a FindItem catalog."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SimulatorError(f"Catalog not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SimulatorError(f"Catalog is not valid JSON: {exc}") from exc

    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise SimulatorError("Catalog must contain an 'items' list")

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise SimulatorError(f"Catalog item {index} must be an object")
        missing = [key for key in ("id", "name", "device_id") if not item.get(key)]
        if missing:
            raise SimulatorError(
                f"Catalog item {index} is missing: {', '.join(missing)}"
            )
    return items


def _searchable_values(item: dict[str, Any]) -> list[str]:
    return [
        str(item[key]).strip()
        for key in ("id", "name", "name_en")
        if item.get(key)
    ]


def find_item(items: Sequence[dict[str, Any]], query: str) -> dict[str, Any]:
    """Find one item by ID or bilingual name, allowing an unambiguous substring."""
    normalized = query.strip().casefold()
    if not normalized:
        raise SimulatorError("Search query cannot be empty")

    exact = [
        item
        for item in items
        if normalized in {value.casefold() for value in _searchable_values(item)}
    ]
    if len(exact) == 1:
        return exact[0]

    partial = [
        item
        for item in items
        if any(normalized in value.casefold() for value in _searchable_values(item))
    ]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        ids = ", ".join(str(item["id"]) for item in partial)
        raise SimulatorError(f"Search is ambiguous; matches: {ids}")

    available = ", ".join(str(item["id"]) for item in items)
    raise SimulatorError(f"No item matches '{query}'. Available IDs: {available}")


def build_locate_plan(
    item: dict[str, Any],
    *,
    duration: int | None = None,
    buzzer: bool = True,
) -> dict[str, Any]:
    """Build the physical effect and MQTT command preview for one item."""
    selected_duration = item.get("duration_sec", 15) if duration is None else duration
    if (
        isinstance(selected_duration, bool)
        or not isinstance(selected_duration, int)
        or not 1 <= selected_duration <= MAX_DURATION_SEC
    ):
        raise SimulatorError(
            f"Duration must be an integer from 1 to {MAX_DURATION_SEC} seconds"
        )

    item_id = str(item["id"])
    device_id = str(item["device_id"])
    payload = {
        "cmd": "start",
        "item_id": item_id,
        "event_id": f"sim-{item_id.casefold()}",
        "duration": selected_duration,
        "buzzer": buzzer,
    }
    return {
        "mode": "offline",
        "item": {
            "id": item_id,
            "name": item["name"],
            "name_en": item.get("name_en"),
        },
        "location": {
            "zh": item.get("location"),
            "en": item.get("location_en"),
        },
        "target": {
            "device_id": device_id,
            "led_id": f"{device_id}:gpio-{LED_GPIO}",
            "led_gpio": LED_GPIO,
            "led_color": LED_COLOR,
            "buzzer_gpio": BUZZER_GPIO,
        },
        "effects": {
            "led": "on",
            "buzzer": {
                "enabled": buzzer,
                "frequency_hz": BUZZER_FREQUENCY_HZ if buzzer else 0,
                "beep_ms": selected_duration * 1000 if buzzer else 0,
            },
        },
        "mqtt": {
            "topic": f"findit/device/{device_id}/command",
            "payload": payload,
        },
    }


def _human_output(plan: dict[str, Any]) -> str:
    buzzer = plan["effects"]["buzzer"]
    buzzer_text = (
        f"{buzzer['frequency_hz']} Hz for {buzzer['beep_ms']} ms"
        if buzzer["enabled"]
        else "off"
    )
    name_en = plan["item"].get("name_en")
    display_name = plan["item"]["name"]
    if name_en:
        display_name += f" / {name_en}"
    return "\n".join(
        (
            f"Item: {display_name} ({plan['item']['id']})",
            f"Location: {plan['location'].get('zh') or '-'} / {plan['location'].get('en') or '-'}",
            f"Device: {plan['target']['device_id']}",
            f"LED: {plan['target']['led_id']} | {plan['target']['led_color']}",
            f"Buzzer: {buzzer_text}",
            f"MQTT topic: {plan['mqtt']['topic']}",
            "Payload: " + json.dumps(plan["mqtt"]["payload"], ensure_ascii=False),
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preview a FindItem locate command without hardware or MQTT."
    )
    parser.add_argument("query", help="Item ID, Chinese name, or English name")
    parser.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG,
        help="Path to items.json",
    )
    parser.add_argument("--duration", type=int, help="Override duration in seconds")
    parser.add_argument("--no-buzzer", action="store_true", help="Use LED only")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        item = find_item(load_items(args.catalog), args.query)
        plan = build_locate_plan(
            item,
            duration=args.duration,
            buzzer=not args.no_buzzer,
        )
    except SimulatorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        print(_human_output(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


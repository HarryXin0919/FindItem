# WS2812 Single LED Test

- ESP32-C3 GPIO3 -> 330-470 ohm series resistor -> WS2812 DIN
- External regulated 5V -> WS2812 5V
- common GND between ESP32-C3 and LED supply
- use a logic level shifter such as 74AHCT125 for robust 5V data signalling
- add bulk capacitance at the LED power input

Start at low brightness.

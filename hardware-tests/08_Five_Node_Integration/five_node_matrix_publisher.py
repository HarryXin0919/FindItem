import argparse
import time
import paho.mqtt.client as mqtt

parser = argparse.ArgumentParser()
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--delay", type=float, default=0.75)
args = parser.parse_args()

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.connect(args.host, 1883, 60)
client.loop_start()

for drawer in range(1, 51):
    controller = (drawer - 1) // 10 + 1
    local_index = (drawer - 1) % 10
    cid = f"CTRL-{controller:02d}"
    topic = f"findit/controllers/{cid}/command"
    # Complete_Node_Quick_Test uses one ASCII digit for the physical quick-test matrix.
    client.publish(topic, str(local_index), qos=1)
    print(f"drawer={drawer:02d} controller={cid} local_led={local_index}")
    time.sleep(args.delay)

client.loop_stop()
client.disconnect()

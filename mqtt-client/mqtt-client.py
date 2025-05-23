import paho.mqtt.client as paho
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
import threading
import time
import json
import sys
import os

sys.path.insert(1, os.path.join(os.path.dirname(__file__), "TasmotaCLI"))
import tasmotaMQTTClient

class MQTTClient:
    def __init__(self, mqtt_client_name = "reg.reaweb.uk/mqtt-client", loop_forever = True):
        self.influxc = InfluxDBClient(
            url = "http://%s:8086" % INFLUXDB_HOST,
            token = os.environ["DOCKER_INFLUXDB_INIT_ADMIN_TOKEN"],
            org = os.environ["DOCKER_INFLUXDB_INIT_ORG"] 
        )
        self.influxc.ping()

        self.mqttc = paho.Client(mqtt_client_name, clean_session = True)
        if loop_forever:
            self.mqttc.on_connect = self._on_connect_cb
            self.mqttc.on_message = self._on_message_cb

        self.mqttc.username_pw_set(os.environ["MQTT_USER"], password = os.environ["MQTT_PASSWD"])
        self.mqttc.connect(MQTT_HOST, 1883, 60)
        if loop_forever:
            self.mqttc.loop_forever()

    def _on_connect_cb(self, mqtt, userdata, flags, rc):
        #print("Connected to broker")
        self.mqttc.subscribe("tele/+/+/SENSOR")

    def _on_message_cb(self, mqtt, userdata, msg):
        #print('Topic: {0} | Message: {1}'.format(msg.topic, msg.payload))

        # my MQTT naming scheme is tele/<sensor type>/<specific sensor location>/<whatever>
        # e.g.
        #    tele/TasmotaPlug/TasmotaNAS/SENSOR
        #    tele/TasmotaZigbee/TasmotaZigbee/SENSOR (there is only one Tasmota Zigbee bridge)
        type_ = msg.topic.split("/")[1]
        location = msg.topic.split("/")[2]
        msg_j = json.loads(msg.payload.decode())

        if type_ == "TasmotaPlug":
            self.handle_plug(msg_j, location)
        elif type_ == "TasmotaZigbee":
            self.handle_zigbee(msg_j)

    def handle_plug(self, msg_j, location):
        print("'%s' is using %.1fw @ %s. %.1fkWh so far today, %.1fkWh yesterday" % (location, msg_j["ENERGY"]["Power"], msg_j["Time"],  msg_j["ENERGY"]["Today"], msg_j["ENERGY"]["Yesterday"]))
        fields = {k: v for k, v in msg_j["ENERGY"].items() if k not in {"TotalStartTime"}}
        self.append_influxdb(fields, "tasmota_power", {"plug": location})

    def handle_zigbee(self, msg_j):
        def toggle_geoffery():
            print("Starting thread...")
            tasmotaMQTTClient.MQTTClient(MQTT_HOST, "TasmotaGeoffery", os.environ["MQTT_USER"], os.environ["MQTT_PASSWD"], "OFF")
            print("Waiting...")
            time.sleep(8)
            tasmotaMQTTClient.MQTTClient(MQTT_HOST, "TasmotaGeoffery", os.environ["MQTT_USER"], os.environ["MQTT_PASSWD"], "ON")

        zigbee_id = list(msg_j["ZbReceived"].keys())[0]
        fields = msg_j["ZbReceived"][zigbee_id]
        friendlyname = fields.pop("Name")
        del fields["Device"]
        print("Zigbee device '%s' reported: %s" % (friendlyname, str(fields)))
        if "Read" not in fields.keys():
            self.append_influxdb(fields, "zigbee", {"friendlyname": friendlyname, "id": zigbee_id})

        if zigbee_id == "0x7327" and friendlyname == "TVButton" and "Power" in fields.keys():
            if fields["Power"] == 2:
                print("TV Zigbee button pressed, toggling TasmotaTV Tasmota Plug")
                self.toggle_plug("TasmotaTV")
                threading.Thread(target = toggle_geoffery, args = ()).start()

        if zigbee_id == "0x74B3" and friendlyname == "HarveyButton" and "Power" in fields.keys():
            if fields["Power"] == 2:
                print("Harvey's button pressed, toggling TasmotaHarveyPC Plug")
                self.toggle_plug("TasmotaHarveyPC")

    def set_plug(self, friendlyname, payload):
        t = "cmnd/TasmotaPlug/%s/Power" % friendlyname
        self.mqttc.publish(t, payload = payload)
        print("Send payload '%s' to %s" % (payload, t))

    def toggle_plug(self, friendlyname):
        self.set_plug(friendlyname, "TOGGLE")

    def append_influxdb(self, fields, measurement_name, tags):
        points = [{"measurement": measurement_name, "tags": tags, "fields": fields}]
        write_api = self.influxc.write_api(write_options = SYNCHRONOUS)
        write_api.write(
            os.environ["DOCKER_INFLUXDB_INIT_BUCKET"],
            os.environ["DOCKER_INFLUXDB_INIT_ORG"],
            points,
            write_precision = WritePrecision.S
        )

if __name__ == "__main__":
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.env")
    if os.path.exists(env_path):
        import dotenv
        dotenv.load_dotenv(dotenv_path = env_path)
        INFLUXDB_HOST = "dns.athome"
        MQTT_HOST = "dns.athome"
    else:
        INFLUXDB_HOST = "influxdb"
        MQTT_HOST = "mqtt"

    mqtt_client = MQTTClient()

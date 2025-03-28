# Home Assistant extension for GroBro to act as a MQTT bridge between source and target MQTT brokers
# Reads Growatt MQTT packets, decodes them, maps registers and republishes values for Home Assistant auto-discovery

import os
import json
import struct
import ssl
import paho.mqtt.client as mqtt
from paho.mqtt.client import MQTTMessage
from grobro import unscramble, parse_modbus_type, load_modbus_input_register_file

# Load modbus mapping
modbus_input_register_descriptions = load_modbus_input_register_file("growatt_inverter_registers.json")

# Lookup for HA metadata
ha_lookup = {
    reg["variable_name"]: reg.get("ha")
    for reg in modbus_input_register_descriptions if reg.get("ha")
}

# Configuration from environment variables
SOURCE_MQTT_HOST = os.getenv("SOURCE_MQTT_HOST", "localhost")
SOURCE_MQTT_PORT = int(os.getenv("SOURCE_MQTT_PORT", 1883))
SOURCE_MQTT_USER = os.getenv("SOURCE_MQTT_USER")
SOURCE_MQTT_PASS = os.getenv("SOURCE_MQTT_PASS")
SOURCE_MQTT_TLS = os.getenv("SOURCE_MQTT_TLS", "false").lower() == "true"

TARGET_MQTT_HOST = os.getenv("TARGET_MQTT_HOST", SOURCE_MQTT_HOST)
TARGET_MQTT_PORT = int(os.getenv("TARGET_MQTT_PORT", SOURCE_MQTT_PORT))
TARGET_MQTT_USER = os.getenv("TARGET_MQTT_USER", SOURCE_MQTT_USER)
TARGET_MQTT_PASS = os.getenv("TARGET_MQTT_PASS", SOURCE_MQTT_PASS)
TARGET_MQTT_TLS = os.getenv("TARGET_MQTT_TLS", "false").lower() == "true"

HA_BASE_TOPIC = os.getenv("HA_BASE_TOPIC", "homeassistant")

# Setup target MQTT client for publishing
target_client = mqtt.Client(client_id="grobro-target")
if TARGET_MQTT_USER and TARGET_MQTT_PASS:
    target_client.username_pw_set(TARGET_MQTT_USER, TARGET_MQTT_PASS)
if TARGET_MQTT_TLS:
    target_client.tls_set(cert_reqs=ssl.CERT_NONE)
    target_client.tls_insecure_set(True)
target_client.connect(TARGET_MQTT_HOST, TARGET_MQTT_PORT, 60)
target_client.loop_start()

def publish_ha_discovery(serial, reg):
    variable = reg['name']
    ha = ha_lookup.get(variable, {})
    topic = f"{HA_BASE_TOPIC}/sensor/grobro/{serial}_{variable}/config"
    payload = {
        "name": ha.get("name", variable),
        "state_topic": f"{HA_BASE_TOPIC}/grobro/{serial}/state",
        "value_template": f"{{{{ value_json['{variable}'] }}}}",
        "unique_id": f"grobro_{serial}_{variable}",
        "object_id": f"{serial}_{variable}",
        "device": {
            "identifiers": [serial],
            "name": f"Growatt Inverter {serial}",
            "manufacturer": "Growatt"
        }
    }
    for key in ["device_class", "state_class", "unit_of_measurement", "icon"]:
        if key in ha:
            payload[key] = ha[key]
    target_client.publish(topic, json.dumps(payload), retain=True)

def publish_state(serial, registers):
    payload = {
        reg["name"]: round(reg["value"], 2) if isinstance(reg["value"], float) else reg["value"]
        for reg in registers
    }
    topic = f"{HA_BASE_TOPIC}/grobro/{serial}/state"
    # print(f"Publishing state to {topic}")
    # print(json.dumps(payload, indent=2))
    target_client.publish(topic, json.dumps(payload), retain=False)

def on_message(client, userdata, msg: MQTTMessage):
    try:
        unscrambled = unscramble(msg.payload)
        parsed = parse_modbus_type(unscrambled, modbus_input_register_descriptions)
        serial = parsed.get("meta_info", {}).get("device_sn", parsed.get("device_id"))

        all_registers = parsed.get("modbus1", {}).get("registers", []) + \
                        parsed.get("modbus2", {}).get("registers", [])

        publish_state(serial, all_registers)

        for reg in all_registers:
            publish_ha_discovery(serial, reg)

        print(f"Published state for {serial} with {len(all_registers)} registers")

    except Exception as e:
        print(f"Error processing message: {e}")

# Setup source MQTT client for subscribing
source_client = mqtt.Client(client_id="grobro-source")
if SOURCE_MQTT_USER and SOURCE_MQTT_PASS:
    source_client.username_pw_set(SOURCE_MQTT_USER, SOURCE_MQTT_PASS)
if SOURCE_MQTT_TLS:
    source_client.tls_set(cert_reqs=ssl.CERT_NONE)
    source_client.tls_insecure_set(True)

source_client.on_message = on_message
source_client.connect(SOURCE_MQTT_HOST, SOURCE_MQTT_PORT, 60)
source_client.subscribe("c/#")

print(f"Connected to source MQTT at {SOURCE_MQTT_HOST}:{SOURCE_MQTT_PORT}, listening on 'c/#'")

source_client.loop_forever()

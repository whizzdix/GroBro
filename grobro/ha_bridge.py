# Home Assistant extension for GroBro to act as a MQTT bridge between source and target MQTT brokers
# Reads Growatt MQTT packets, decodes them, maps registers and republishes values for Home Assistant auto-discovery

import os
import json
import struct
import ssl
import paho.mqtt.client as mqtt
from paho.mqtt.client import MQTTMessage
from grobro import unscramble, parse_modbus_type, load_modbus_input_register_file, parse_config_type, find_config_offset

# Load modbus mapping
modbus_input_register_descriptions = load_modbus_input_register_file("growatt_inverter_registers.json")

# Lookup for HA metadata
ha_lookup = {
    reg["variable_name"]: reg.get("ha")
    for reg in modbus_input_register_descriptions if reg.get("ha")
}

# Cache for device config
config_cache = {}

for fname in os.listdir("."):
    if fname.startswith("config_") and fname.endswith(".json"):
        try:
            with open(fname, "r") as f:
                config = json.load(f)
                serial = config.get("serial_number") or fname[7:-5]
                if serial:
                    config_cache[serial] = config
        except Exception as e:
            print(f"Failed to load config {fname}: {e}")

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

    device_info = {
        "identifiers": [serial],
        "name": f"Growatt {serial}",
        "manufacturer": "Growatt"
    }

    # Find matching config
    config = config_cache.get("QMN000" + serial)

    # Fallback: try loading from file
    if not config:
        config_path = f"config_QMN000{serial}.json"
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    config = json.load(f)
                    config_cache[serial] = config
                    print(f"Loaded cached config for {serial} from file (fallback)")
            except Exception:
                config = {}


    if isinstance(config, dict):
        device_type_map = {
            "55": "NEO-series",
            "72": "NEXA-series",
            "61": "NOAH-series"
        }

        known_model_id = device_type_map.get(config.get("device_type"))
        if known_model_id:
            device_info["model"] = known_model_id
        elif config.get("model_id"):
            device_info["model"] = config["model_id"]

        if config.get("sw_version"):
            device_info["sw_version"] = config["sw_version"]
        if config.get("hw_version"):
            device_info["hw_version"] = config["hw_version"]
        if config.get("mac_address"):
            device_info["connections"] = [["mac", config["mac_address"]]]

    payload = {
        "name": ha.get("name", variable),
        "state_topic": f"{HA_BASE_TOPIC}/grobro/{serial}/state",
        "value_template": f"{{{{ value_json['{variable}'] }}}}",
        "unique_id": f"grobro_{serial}_{variable}",
        "object_id": f"{serial}_{variable}",
        "device": device_info
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
    target_client.publish(topic, json.dumps(payload), retain=False)

def on_message(client, userdata, msg: MQTTMessage):
    try:
        unscrambled = unscramble(msg.payload)
        msg_counter = struct.unpack_from('>H', unscrambled, 0)[0]

        if msg_counter == 1:
            # Config message
            config_offset = find_config_offset(unscrambled)
            config = parse_config_type(unscrambled, config_offset)
            serial = config.get("serial_number")
            config_path = f"config_{serial}.json"

            save_config = True
            if os.path.exists(config_path):
                try:
                    with open(config_path, "r") as f:
                        existing = json.load(f)
                        if existing == config:
                            save_config = False
                except Exception:
                    pass

            if save_config:
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
                print(f"Saved updated config for {serial}")
            else:
                print(f"No config change for {serial}")

            config_cache[serial] = config  # Update in-memory cache

        else:
            # Modbus message
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

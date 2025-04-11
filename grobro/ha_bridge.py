# Home Assistant extension for GroBro to act as a MQTT bridge between source and target MQTT brokers
# Reads Growatt MQTT packets, decodes them, maps registers and republishes values for Home Assistant auto-discovery

import os
import json
import struct
import ssl
import paho.mqtt.client as mqtt
from paho.mqtt.client import MQTTMessage
from grobro import unscramble, parse_modbus_type, load_modbus_input_register_file, parse_config_type, find_config_offset

config_cache = {}
ha_lookup = {}
for fname in os.listdir("."):
    if fname.startswith("config_") and fname.endswith(".json"):
        try:
            with open(fname, "r") as f:
                config = json.load(f)
                device_id = config.get("serial_number") or fname[7:-5]
                if device_id:
                    config_cache[device_id] = config
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

# Register filter configuration
NEO_SP2_REGISTERS = [
    3001, 3003, 3004, 3005, 3007, 3008, 3009,
    3023, 3025, 3026, 3027, 3028, 3038, 3047,
    3049, 3051, 3053, 3055, 3057, 3059, 3061,
    3087, 3093, 3094, 3095, 3096, 3098, 3100,
    3101, 3115
]

NOAH_REGISTERS = [
    1, 4, 7, 11,
    13, 21, 25, 29,
    71, 73, 75, 77,
    94, 95, 97, 100
]

REGISTER_FILTER_ENV = os.getenv("REGISTER_FILTER", "")
device_filter_alias_map = {}
for entry in REGISTER_FILTER_ENV.split(","):
    if ":" in entry:
        serial, alias = entry.split(":", 1)
        device_filter_alias_map[serial] = alias

alias_to_registers = {
    "NEO600": NEO_SP2_REGISTERS,
    "NEO800": NEO_SP2_REGISTERS,
    "NEO1000": NEO_SP2_REGISTERS,
    "NOAH": NOAH_REGISTERS
}

# Setup target MQTT client for publishing
target_client = mqtt.Client(client_id="grobro-target")
if TARGET_MQTT_USER and TARGET_MQTT_PASS:
    target_client.username_pw_set(TARGET_MQTT_USER, TARGET_MQTT_PASS)
if TARGET_MQTT_TLS:
    target_client.tls_set(cert_reqs=ssl.CERT_NONE)
    target_client.tls_insecure_set(True)
target_client.connect(TARGET_MQTT_HOST, TARGET_MQTT_PORT, 60)
target_client.loop_start()

def parse_percentage(value):
    return int(value / 65535)

def parse_ascii(value):
    try:
        return bytes.fromhex(hex(value)[2:].zfill(4)).decode('ascii').strip()
    except Exception:
        return str(value)

def apply_conversion(register):
    unit = register.get("unit")
    if unit == "int16" and isinstance(register.get("value"), int):
        register["value"] = parse_percentage(register["value"])
    elif unit == "s":
        register["value"] = parse_ascii(register["value"])
    elif isinstance(register.get("value"), (int, float)) and register.get("multiplier"):
        register["value"] *= register["multiplier"]

def publish_ha_discovery(device_id, reg):
    variable = reg['name']
    ha = ha_lookup.get(variable, {})
    topic = f"{HA_BASE_TOPIC}/sensor/grobro/{device_id}_{variable}/config"
    device_info = {
        "identifiers": [device_id],
        "name": f"Growatt {device_id}",
        "manufacturer": "Growatt",
        "serial_number": device_id
    }
    # Find matching config
    config = config_cache.get(device_id)

    # Fallback: try loading from file
    if not config:
        config_path = f"config_{device_id}.json"
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    config = json.load(f)
                    config_cache[device_id] = config
                    print(f"Loaded cached config for {device_id} from file (fallback)")
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
        "state_topic": f"{HA_BASE_TOPIC}/grobro/{device_id}/state",
        "value_template": f"{{{{ value_json['{variable}'] }}}}",
        "unique_id": f"grobro_{device_id}_{variable}",
        "object_id": f"{device_id}_{variable}",
        "device": device_info
    }
    for key in ["device_class", "state_class", "unit_of_measurement", "icon"]:
        if key in ha:
            payload[key] = ha[key]
    target_client.publish(topic, json.dumps(payload), retain=True)

def publish_state(device_id, registers):
    alias = device_filter_alias_map.get(device_id)
    allowed_registers = alias_to_registers.get(alias)
    if allowed_registers:
        registers = [
            reg for reg in registers
            if "register_no" in reg and reg["register_no"] in allowed_registers
        ]
    for reg in registers:
        apply_conversion(reg)
    payload = {
        reg["name"]: round(reg["value"], 2) if isinstance(reg["value"], float) else reg["value"]
        for reg in registers
    }
    print(f"Device {device_id} matched {len(registers)} registers after filtering.")
    topic = f"{HA_BASE_TOPIC}/grobro/{device_id}/state"
    target_client.publish(topic, json.dumps(payload), retain=False)

def on_message(client, userdata, msg: MQTTMessage):
    try:
        unscrambled = unscramble(msg.payload)
        msg_type = struct.unpack_from('>H', unscrambled, 4)[0]

        # NOAH=387 NEO=340
        if msg_type in (387, 340):
            # Config message
            config_offset = find_config_offset(unscrambled)
            config = parse_config_type(unscrambled, config_offset)
            device_id = config.get("serial_number")
            config_path = f"config_{device_id}.json"
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
                print(f"Saved updated config for {device_id}")
            else:
                print(f"No config change for {device_id}")
            config_cache[device_id] = config
        else:
            # Modbus message
            temp_descriptions = load_modbus_input_register_file("growatt_inverter_registers.json")
            parsed = parse_modbus_type(unscrambled, temp_descriptions)
            device_id = parsed.get("device_id")
            alias = device_filter_alias_map.get(device_id)
            regfile = "growatt_inverter_registers.json"
            if alias and alias.strip().upper() == "NOAH":
                regfile = "growatt_noah_registers.json"
            modbus_input_register_descriptions = load_modbus_input_register_file(regfile)

            # Rebuild HA metadata lookup for this register set
            ha_lookup.clear()
            ha_lookup.update({
                reg["variable_name"]: reg.get("ha")
                for reg in modbus_input_register_descriptions if reg.get("ha")
            })
            parsed = parse_modbus_type(unscrambled, modbus_input_register_descriptions)
            device_id = parsed.get("device_id")
            all_registers = parsed.get("modbus1", {}).get("registers", []) + parsed.get("modbus2", {}).get("registers", [])
            alias = device_filter_alias_map.get(device_id)
            allowed_registers = alias_to_registers.get(alias)
            if allowed_registers:
                all_registers = [
                    reg for reg in all_registers
                    if isinstance(reg.get("register_no"), int) and reg["register_no"] in allowed_registers
                ]
            publish_state(device_id, all_registers)
            for reg in all_registers:
                publish_ha_discovery(device_id, reg)
            print(f"Published state for {device_id} with {len(all_registers)} registers")
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

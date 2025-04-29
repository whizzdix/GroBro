#!/usr/bin/env python3

"""
Growatt MQTT Control Script to demonstrate the current development status.

This script provides a command interface via an MQTT broker (TARGET_MQTT) to control Growatt devices via the SOURCE_MQTT broker:

1. SOURCE_MQTT: Used to send commands to the Growatt device
2. TARGET_MQTT: Used to receive command instructions in JSON format

The script subscribes to a configurable topic (default: "grobro/cmd") on the target MQTT broker
and waits for JSON messages with commands. When a valid command is received, it creates
the corresponding binary packet, encrypts it and forwards it to the Growatt device via
the source MQTT broker.

Supported commands:
- charge_limit: Sets the limits for the battery charge (top/bottom)
- output_limit: Sets the limit for the output power (0-800W)
- inverter_config: Set the configuration of the inverter model
- slot_create: Create time-based power control slot
- slot_delete: Delete time-based power control slot

Example of JSON command messages:
1. Setting the load limits:
   {"device_id": "0PVP50xxxxxxxxxx", "action": "charge_limit", "upper": 90, "lower": 20}

2. setting the output power limit:
 {"device_id": "0PVP50xxxxxxxxxx", "action": "output_limit", "power": 600}

3. Setting inverter configuration:
   {"device_id": "0PVP50xxxxxxxxxx", "action": "inverter_config", "model_id": "0204"}

4. Creating a time slot:
   {"device_id": "0PVP50xxxxxxxxxx", "action": "slot_create", "slot": 1, "start": "06:00", "end": "12:00", "power": 500}

5. Deleting a time slot:
   {"device_id": "0PVP50xxxxxxxxxx", "action": "slot_delete", "slot": 1}

usage: gromqtt.py [-h] --source-mqtt-broker SOURCE_MQTT_BROKER [--source-mqtt-port SOURCE_MQTT_PORT]
                  [--source-mqtt-username SOURCE_MQTT_USERNAME] [--source-mqtt-password SOURCE_MQTT_PASSWORD] [--source-mqtt-tls]
                  --target-mqtt-broker TARGET_MQTT_BROKER [--target-mqtt-port TARGET_MQTT_PORT] [--target-mqtt-username TARGET_MQTT_USERNAME]
                  [--target-mqtt-password TARGET_MQTT_PASSWORD] [--target-mqtt-tls] [--topic TOPIC] [--hexdump]
"""

import argparse
import struct
import sys
import random
import time
import json
import paho.mqtt.client as mqtt
import crc
import ssl

crc16 = crc.Calculator(crc.Crc16.MODBUS)

def scramble(pkt: bytes) -> bytes:
    mask = b"Growatt"
    out = bytearray(pkt[:8])
    out += bytes(b ^ mask[i % len(mask)] for i, b in enumerate(pkt[8:]))
    return bytes(out)

def append_crc(pkt: bytes) -> bytes:
    csum = crc16.checksum(pkt)
    return pkt + struct.pack("!H", csum)

def hexdump(data: bytes, width: int = 16) -> None:
    for i in range(0, len(data), width):
        chunk = data[i: i + width]
        hex_part = " ".join(f"{b:02X}" for b in chunk)
        asc_part = "".join(chr(b) if 32 <= b <= 126 else "." for b in chunk)
        print(f"{i:08X}  {hex_part:<{width * 3}} |{asc_part}|")

# --- Message Builders ---

def build_charge_limit(device_id: str, upper: int, lower: int) -> bytes:
    header = struct.pack(">HHH", 1, 7, 40)
    mtype = 0x0110
    dev_bytes = device_id.encode("ascii").ljust(16, b"\x00")
    payload = dev_bytes + (b"\x00" * 15) + b"\xFA\x00\xFB" + struct.pack(">HH", upper, lower)
    return header + struct.pack(">H", mtype) + payload

def build_output_limit(device_id: str, power: int) -> bytes:
    # Ensure power is within valid range (0-800)
    power = max(0, min(power, 800))
    
    header = struct.pack(">HHH", 1, 7, 36)
    mtype = 0x0106
    dev_bytes = device_id.encode("ascii").ljust(16, b"\x00")
    payload = dev_bytes + (b"\x00" * 15) + b"\xFC" + struct.pack(">H", power)
    return header + struct.pack(">H", mtype) + payload

def build_inverter_config(device_id: str, model_hex: str) -> bytes:
    # Hoymiles HMS-1600-4T = 0204
    # APsystems EZ1-M = 0401
    header = struct.pack(">HHH", 1, 7, 36)
    mtype = 0x0106
    dev_bytes = device_id.encode("ascii").ljust(16, b"\x00")
    payload = dev_bytes + (b"\x00" * 14) + b"\x01\x2C" + bytes.fromhex(model_hex)
    return header + struct.pack(">H", mtype) + payload

def build_slot(device_id: str, action: str, slot: int, start: str = None, end: str = None, power: int = 0) -> bytes:
    import struct

    # Ensure power is within valid range (0-800)
    power = max(0, min(power, 800))

    header = struct.pack(">HHH", 1, 7, 46)  # Fixed header fields
    mtype = 0x0110  # Message type
    dev_bytes = device_id.encode("ascii").ljust(16, b"\x00")

    payload = dev_bytes
    payload += b"\x00" * 14  # Reserved padding

    control_bytes = {
        1: (0x01, 0x02, 0x00),  # (Unknown 1, Unknown 2, Unknown 3)
        2: (0x03, 0x01, 0x07),
        3: (0x08, 0x01, 0x0C),
        4: (0x0D, 0x01, 0x11),
        5: (0x12, 0x01, 0x16),
    }

    control1, control2, extra = control_bytes.get(slot, (0x01, 0x01, 0x00))

    if action == "slot_create":
        if slot == 1:
            payload += b"\x00\xFE"
            payload += struct.pack(">BB", control1, control2)
        else:
            payload += b"\x01"
            payload += struct.pack(">BBB", control1, control2, extra)

        sh, sm = map(int, start.split(":"))
        eh, em = map(int, end.split(":"))
        payload += struct.pack(">BBBB", sh, sm, eh, em)

        payload += b"\x00\x00"  # Reserved
        payload += struct.pack(">H", power)
        payload += b"\x00\x01"  # Fixed ending

    elif action == "slot_delete":
        if slot == 1:
            payload += b"\x00\xFE"
            payload += struct.pack(">BB", control1, control2)
        else:
            payload += b"\x01"
            payload += struct.pack(">BBB", control1, control2, extra)

        payload += b"\x00" * 4  # Clear times
        payload += b"\x00\x00"  # Reserved
        payload += b"\x00" * 4  # Clear power/flag

    else:
        raise ValueError(f"Unknown slot action {action}")

    return header + struct.pack(">H", mtype) + payload

# --- MQTT ---

def on_source_connect(client, userdata, flags, rc):
    if rc != 0:
        print(f"Failed to connect to source broker, return code {rc}")
    else:
        print("Connected to source MQTT broker")

def publish_message(broker, port, username, password, tls, device_id, payload):
    topic = f"s/33/{device_id}"
    client_id = f"grobro-{random.randint(0,9999)}"

    client = mqtt.Client(client_id=client_id)
    if username:
        client.username_pw_set(username, password)
    if tls:
        client.tls_set(cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)

    client.on_connect = on_source_connect
    client.connect(broker, port)
    client.loop_start()

    time.sleep(1)  # wait for connection
    result = client.publish(topic, payload)
    status = result[0]
    if status == 0:
        print(f"Sent message to topic {topic}")
    else:
        print(f"Failed to send message")
    time.sleep(1)
    client.loop_stop()

# --- Target MQTT Functions ---

def on_target_connect(client, userdata, flags, rc):
    if rc != 0:
        print(f"Failed to connect to target broker, return code {rc}")
    else:
        print("Connected to target MQTT broker")
        client.subscribe(userdata.get("topic", "grobro/cmd"))
        print(f"Subscribed to {userdata.get('topic', 'grobro/cmd')}")

def on_target_message(client, userdata, msg):
    print(f"Received message on {msg.topic}")
    try:
        # Parse the incoming JSON message
        data = json.loads(msg.payload.decode())
        print(f"Received command: {data}")
        
        # Extract required parameters
        device_id = data.get("device_id")
        action = data.get("action")
        
        if not device_id or not action:
            print("Error: Missing required parameters (device_id or action)")
            return
            
        # Process based on action type
        if action == "charge_limit":
            upper = data.get("upper")
            lower = data.get("lower")
            if upper is None or lower is None:
                print("Error: Missing parameters for charge_limit (upper or lower)")
                return
            pkt = build_charge_limit(device_id, upper, lower)
            
        elif action == "output_limit":
            power = data.get("power")
            if power is None:
                print("Error: Missing power parameter for output_limit")
                return
                
            # Validate power range
            if power < 0 or power > 800:
                print(f"Warning: Power value {power} outside valid range (0-800). Will be clamped.")
                
            pkt = build_output_limit(device_id, power)
            
        elif action == "inverter_config":
            model_id = data.get("model_id")
            if not model_id:
                print("Error: Missing model_id parameter for inverter_config")
                return
            pkt = build_inverter_config(device_id, model_id)
            
        elif action in ("slot_create", "slot_delete"):
            slot = data.get("slot")
            if slot is None:
                print("Error: Missing slot parameter for slot operation")
                return
                
            if action == "slot_create":
                start = data.get("start")
                end = data.get("end")
                power = data.get("power")
                if not start or not end or power is None:
                    print("Error: Missing parameters for slot_create")
                    return
                    
                # Validate power range
                if power < 0 or power > 800:
                    print(f"Warning: Power value {power} outside valid range (0-800). Will be clamped.")
                    
                pkt = build_slot(device_id, action, slot, start, end, power)
            else:
                pkt = build_slot(device_id, action, slot)
                
        else:
            print(f"Error: Unknown action '{action}'")
            return
            
        # Process and send the message
        scrambled = scramble(pkt)
        final_payload = append_crc(scrambled)
        
        if userdata.get("hexdump", False):
            print("\n--- Message ---")
            hexdump(pkt)
            print("\n--- Final Message ---")
            hexdump(final_payload)
            
        # Send via source MQTT broker
        source_config = userdata.get("source_mqtt", {})
        publish_message(
            broker=source_config.get("broker"),
            port=source_config.get("port", 1883),
            username=source_config.get("username"),
            password=source_config.get("password"),
            tls=source_config.get("tls", False),
            device_id=device_id,
            payload=final_payload
        )
        
    except json.JSONDecodeError:
        print("Error: Invalid JSON format in message")
    except Exception as e:
        print(f"Error processing message: {e}")

# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="Growatt MQTT CLI Tool")
    # Source MQTT connection parameters
    parser.add_argument("--source-mqtt-broker", required=True, help="Source MQTT broker address")
    parser.add_argument("--source-mqtt-port", type=int, default=1883, help="Source MQTT broker port")
    parser.add_argument("--source-mqtt-username", help="Source MQTT username (optional)")
    parser.add_argument("--source-mqtt-password", help="Source MQTT password (optional)")
    parser.add_argument("--source-mqtt-tls", action="store_true", help="Enable TLS for source MQTT (optional)")
    
    # Target MQTT connection parameters
    parser.add_argument("--target-mqtt-broker", required=True, help="Target MQTT broker address")
    parser.add_argument("--target-mqtt-port", type=int, default=1883, help="Target MQTT broker port")
    parser.add_argument("--target-mqtt-username", help="Target MQTT username (optional)")
    parser.add_argument("--target-mqtt-password", help="Target MQTT password (optional)")
    parser.add_argument("--target-mqtt-tls", action="store_true", help="Enable TLS for target MQTT (optional)")
    parser.add_argument("--topic", default="grobro/cmd", help="MQTT topic to subscribe to for commands (default: grobro/cmd)")
    
    # Other options
    parser.add_argument("--hexdump", action="store_true", help="Hexdump plain and scrambled message before publishing")

    args = parser.parse_args()
    
    # Create the target MQTT client
    client_id = f"grobro-target-{random.randint(0,9999)}"
    target_client = mqtt.Client(client_id=client_id)
    
    # Store source MQTT config and other settings in userdata
    userdata = {
        "source_mqtt": {
            "broker": args.source_mqtt_broker,
            "port": args.source_mqtt_port,
            "username": args.source_mqtt_username,
            "password": args.source_mqtt_password,
            "tls": args.source_mqtt_tls
        },
        "topic": args.topic,
        "hexdump": args.hexdump
    }
    target_client.user_data_set(userdata)
    
    # Set up target client
    if args.target_mqtt_username:
        target_client.username_pw_set(args.target_mqtt_username, args.target_mqtt_password)
    if args.target_mqtt_tls:
        target_client.tls_set(cert_reqs=ssl.CERT_NONE)
        target_client.tls_insecure_set(True)
        
    # Set callbacks
    target_client.on_connect = on_target_connect
    target_client.on_message = on_target_message
    
    # Connect to target broker and start loop
    try:
        print(f"Connecting to target MQTT broker at {args.target_mqtt_broker}:{args.target_mqtt_port}")
        target_client.connect(args.target_mqtt_broker, args.target_mqtt_port)
        print(f"Listening for commands on topic: {args.topic}")
        target_client.loop_forever()
    except KeyboardInterrupt:
        print("Exiting due to keyboard interrupt")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

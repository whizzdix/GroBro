"""
Client for the grobro mqtt side, handling messages from/to
* growatt cloud
* growatt devices
"""

import os
import struct
import logging
import ssl
from typing import Callable


import paho.mqtt.client as mqtt
from paho.mqtt.client import MQTTMessage

from grobro import model
from grobro.grobro import parser
from dis import Positions
from grobro.grobro.builder import append_crc
from grobro.grobro.builder import scramble
from grobro.model.modbus_function import GrowattModbusFunctionSingle
from grobro.model.modbus_message import GrowattModbusFunction
from grobro.model.modbus_message import GrowattModbusMessage
from grobro.model.mqtt_config import MQTTConfig
from grobro.model.growatt_registers import GrowattRegisterDataType
from grobro.model.growatt_registers import GrowattRegisterDataTypes
from grobro.model.growatt_registers import GrowattRegisterEnumTypes
from grobro.model.growatt_registers import HomeAssistantHoldingRegisterInput
from grobro.model.growatt_registers import HomeAssistantHoldingRegisterValue
from grobro.model.growatt_registers import HomeAssistantInputRegister
from grobro.model.growatt_registers import KNOWN_NEO_REGISTERS, KNOWN_NOAH_REGISTERS, KNOWN_NEXA_REGISTERS


LOG = logging.getLogger(__name__)
HA_BASE_TOPIC = os.getenv("HA_BASE_TOPIC", "homeassistant")

# Updated growatt cloud forwarding config
GROWATT_CLOUD = os.getenv("GROWATT_CLOUD", "false")
if GROWATT_CLOUD.lower() == "true":
    GROWATT_CLOUD_ENABLED = True
    GROWATT_CLOUD_FILTER = set()
elif GROWATT_CLOUD:
    GROWATT_CLOUD_ENABLED = True
    GROWATT_CLOUD_FILTER = set(map(str.strip, GROWATT_CLOUD.split(",")))
else:
    GROWATT_CLOUD_ENABLED = False
    GROWATT_CLOUD_FILTER = set()

DUMP_MESSAGES = os.getenv("DUMP_MESSAGES", "false").lower() == "true"
DUMP_DIR = os.getenv("DUMP_DIR", "/dump")

# Property to flag messages forwarded from growatt cloud
MQTT_PROP_FORWARD_GROWATT = mqtt.Properties(mqtt.PacketTypes.PUBLISH)
MQTT_PROP_FORWARD_GROWATT.UserProperty = [("forwarded-for", "growatt")]

# Property to flag messages forwarded from ha
MQTT_PROP_FORWARD_HA = mqtt.Properties(mqtt.PacketTypes.PUBLISH)
MQTT_PROP_FORWARD_HA.UserProperty = [("forwarded-for", "ha")]

# Property to flag messages as dry-run for debugging purposes
MQTT_PROP_DRY_RUN = mqtt.Properties(mqtt.PacketTypes.PUBLISH)
MQTT_PROP_DRY_RUN.UserProperty = [("dry-run", "true")]


class Client:
    on_config: Callable[[model.DeviceConfig], None]
    on_input_register: Callable[HomeAssistantInputRegister, None]
    on_holding_register_input: Callable[HomeAssistantHoldingRegisterInput, None]

    _client: mqtt.Client
    _forward_mqtt_config: model.MQTTConfig
    _forward_clients = {}

    def __init__(self, grobro_mqtt: MQTTConfig, forward_mqtt: MQTTConfig):
        LOG.info(
            f"Connecting to GroBro broker at '{grobro_mqtt.host}:{grobro_mqtt.port}'"
        )
        self._client = mqtt.Client(
            client_id="grobro-grobro",
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            protocol=mqtt.MQTTv5,
        )
        if grobro_mqtt.username and grobro_mqtt.password:
            self._client.username_pw_set(grobro_mqtt.username, grobro_mqtt.password)
        if grobro_mqtt.use_tls:
            self._client.tls_set(cert_reqs=ssl.CERT_NONE)
            self._client.tls_insecure_set(True)
        self._client.connect(grobro_mqtt.host, grobro_mqtt.port, 60)
        self._client.on_message = self.__on_message
        self._client.subscribe("c/#")
        self._forward_mqtt_config = forward_mqtt

    def start(self):
        LOG.debug("GroBro: Start")
        self._client.loop_start()

    def stop(self):
        LOG.debug("GroBro: Stop")
        self._client.loop_stop()
        self._client.disconnect()
        for key, client in self._forward_clients.items():
            client.loop_stop()
            client.disconnect()

    def send_command(self, cmd: GrowattModbusFunctionSingle):
        scrambled = scramble(cmd.build_grobro())
        final_payload = append_crc(scrambled)

        topic = f"s/33/{cmd.device_id}"
        LOG.debug("Send command: %s: %s: %s", type(cmd).__name__, topic, cmd)

        result = self._client.publish(
            topic,
            final_payload,
            properties=MQTT_PROP_FORWARD_HA,
        )
        status = result[0]
        if status != 0:
            LOG.warning("Sending failed: %s", result)

    def __on_message(self, client, userdata, msg: MQTTMessage):
        # check for forwarded messages and ignore them
        forwarded_for = get_property(msg, "forwarded-for")
        if forwarded_for and forwarded_for in ["ha", "growatt"]:
            LOG.debug("Message forwarded from %s. Skipping...", forwarded_for)
            return

        file = get_property(msg, "file")
        LOG.debug(f"Received message (%s): %s: %s", file, msg.topic, msg.payload)
        if DUMP_MESSAGES:
            dump_message_binary(msg.topic, msg.payload)
        try:
            device_id = msg.topic.split("/")[-1]
            if GROWATT_CLOUD_ENABLED:
                if GROWATT_CLOUD == "true" or device_id in GROWATT_CLOUD_FILTER:
                    forward_client = self.__connect_to_growatt_server(device_id)
                    forward_client.publish(
                        msg.topic,
                        payload=msg.payload,
                        qos=msg.qos,
                        retain=msg.retain,
                    )

            unscrambled = parser.unscramble(msg.payload)
            LOG.debug(f"Received: %s %s", msg.topic, unscrambled.hex(" "))

            modbus_message = GrowattModbusMessage.parse_grobro(unscrambled)
            LOG.debug("Received modbus message: %s", modbus_message)
            if modbus_message:
                known_registers = None
                if device_id.startswith("QMN"):
                    known_registers = KNOWN_NEO_REGISTERS
                elif device_id.startswith("0PVP"):
                    known_registers = KNOWN_NOAH_REGISTERS
                elif device_id.startswith("0HVR"):
                    known_registers = KNOWN_NEXA_REGISTERS
                if not known_registers:
                    LOG.info("Modbus message from unknown device type: %s", device_id)
                    return

                if (
                    modbus_message.function
                    == GrowattModbusFunction.READ_SINGLE_REGISTER
                ):
                    state = HomeAssistantHoldingRegisterInput(device_id=device_id)
                    
                    for name, register in known_registers.holding_registers.items():
                        data_raw = modbus_message.get_data(register.growatt.position)
                        value = register.growatt.data.parse(data_raw)
                        if value is None:
                            continue
                        if register.homeassistant.type=="switch":
                            value = "ON" if value==1 else "OFF"
                        state.payload.append(
                            HomeAssistantHoldingRegisterValue(
                                name=name,
                                value=value,
                                register=register.homeassistant,
                            )
                        )
                    self.on_holding_register_input(state)

                if modbus_message.function == GrowattModbusFunction.READ_INPUT_REGISTER:
                    state = HomeAssistantInputRegister(device_id=device_id)
                    
                    for name, register in known_registers.input_registers.items():
                        data_raw = modbus_message.get_data(register.growatt.position)
                        data_type = register.growatt.data
                        value = register.growatt.data.parse(data_raw)
                        # TODO: this is a workaround for broken messages sent by neo inverters at night.
                        # They emmit state updates with incredible high wattage, which spoils HA statistics.
                        # Assuming no one runs a balkony plant with more than a million peak wattage, we drop such messages.
                        if name == "Ppv" and value > 1000000:
                            LOG.debug("Dropping bad payload: %s", device_id)
                            return
                        state.payload[name] = value
                    self.on_input_register(state)
                    return

                return

            msg_type = struct.unpack_from(">H", unscrambled, 4)[0]

            # NOAH: MSG-TYPE 37 is response when setting a register was succeful
            # TODO impmlement a proper response handling
            #example hex: 00 01 00 07 00 25 01 06 30 50 56 50 46 24 6a 52 32 31 42 54 30 30 32 52 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 fc 00 00 14 8c af

            # NOAH: MSG-TYPE 831 looks like published Holding Register??
            ## Example Hex: 00 01 00 07 03 3f 01 03 30 50 56 50 46 24 6a 52 32 31 42 54 30 30 32 52 00 00 00 00 00 00 00 00 00 00 00 00 00 00 30 50 56 50 46 24 6a 52 32 31 42 54 30 30 32 52 00 00 00 00 00 00 00 00 00 00 00 00 00 00 49 06 14 0f 20 01 03 00 00 00 7c 00 64 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 75 58 00 00 00 19 00 06 00 14 00 0f 00 20 00 01 00 00 00 00 00 00 00 00 00 00 00 31 50 42 46 55 00 00 00 00 00 00 32 31 32 30 31 33 32 31 31 30 31 30 32 31 33 30 30 36 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 7d 00 f9 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 30 50 56 50 46 24 6a 52 32 31 42 54 30 30 32 52 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 fa 01 76 00 64 00 03 00 14 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 c8 00 00 00 00 00 00 00 00 00 c8 00 00 00 00 00 00 00 00 00 c8 00 00 00 00 00 00 00 00 00 c8 00 00 00 00 00 00 00 00 00 c8 00 00 00 00 00 00 00 00 00 c8 00 00 00 00 00 00 00 00 00 c8 00 00 03 20 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 5c 83

            # NOAH=387 NEO=340,341
            if msg_type in (387, 340, 341):
                # Config message
                config_offset = parser.find_config_offset(unscrambled)
                config = parser.parse_config_type(unscrambled, config_offset)
                self.on_config(config)
                LOG.info(f"Received config message for {device_id}")
                return

            LOG.debug("Unknown msg_type %s: %s", msg_type, unscrambled.hex())
        except Exception as e:
            LOG.error(f"Processing message: {e}")

    def __on_message_forward_client(self, client, userdata, msg: MQTTMessage):
        if DUMP_MESSAGES:
            dump_message_binary(msg.topic, msg.payload)
        try:
            device_id = msg.topic.split("/")[-1]
            if not GROWATT_CLOUD_ENABLED:
                return
            if GROWATT_CLOUD != "true" and device_id not in GROWATT_CLOUD_FILTER:
                LOG.debug(
                    "Dropping Growatt message for device %s not in GROWATT_CLOUD filter",
                    device_id,
                )
                return
            LOG.debug("Forwarding message from Growatt for client %s", device_id)
            # We need to publish the messages from Growatt on the Topic
            # s/33/{deviceid}. Growatt sends them on Topic s/{deviceid}
            self._client.publish(
                msg.topic.split("/")[0] + "/33/" + device_id,
                payload=msg.payload,
                qos=msg.qos,
                retain=msg.retain,
                properties=MQTT_PROP_FORWARD_GROWATT,
            )
        except Exception as e:
            LOG.error(f"Forwarding message: {e}")

    # Setup Growatt MQTT broker for forwarding messages
    def __connect_to_growatt_server(self, client_id):
        if f"forward_client_{client_id}" not in self._forward_clients:
            LOG.info(
                "Connecting to Growatt broker at '%s:%s', subscribed to '+/%s'",
                self._forward_mqtt_config.host,
                self._forward_mqtt_config.port,
                client_id,
            )
            client = mqtt.Client(
                client_id=client_id,
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            )
            client.tls_set(cert_reqs=ssl.CERT_NONE)
            client.tls_insecure_set(True)
            client.on_message = self.__on_message_forward_client
            client.connect(
                self._forward_mqtt_config.host,
                self._forward_mqtt_config.port,
                60,
            )
            client.subscribe(f"+/{client_id}")
            client.loop_start()
            self._forward_clients[f"forward_client_{client_id}"] = client
        return self._forward_clients[f"forward_client_{client_id}"]


# Ensure that the dump directory exists
if DUMP_MESSAGES and not os.path.exists(DUMP_DIR):
    os.makedirs(DUMP_DIR, exist_ok=True)
    LOG.info(f"Dump directory created: {DUMP_DIR}")


def dump_message_binary(topic, payload):
    try:
        # Build path following topic structure
        topic_parts = topic.strip("/").split("/")
        dir_path = os.path.join(DUMP_DIR, *topic_parts)
        os.makedirs(dir_path, exist_ok=True)

        # Write each message to a new file with timestamp
        import time

        timestamp = int(time.time() * 1000)
        file_path = os.path.join(dir_path, f"{timestamp}.bin")

        with open(file_path, "wb") as f:
            f.write(payload)
    except Exception as e:
        LOG.error(f"Failed to dump message for topic {topic}: {e}")


def get_property(msg, prop) -> str:
    props = msg.properties.json().get("UserProperty", [])
    for key, value in props:
        if key == prop:
            return value
    return None

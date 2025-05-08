# Home Assistant extension for GroBro to act as a MQTT bridge between source and target MQTT brokers
# Reads Growatt MQTT packets, decodes them, maps registers and republishes values for Home Assistant auto-discovery

import os
from dataclasses import asdict
import json
import signal
import ssl
import paho.mqtt.client as mqtt
import importlib.resources as resources
import threading
import logging
import time
import grobro.model as model
import grobro.ha as ha
import grobro.grobro as grobro

REGISTER_FILTER_ENV = os.getenv("REGISTER_FILTER", "")
REGISTER_FILTER: dict[str, model.DeviceAlias] = {}
for entry in REGISTER_FILTER_ENV.split(","):
    if ":" in entry:
        serial, alias = entry.split(":", 1)
        REGISTER_FILTER[serial] = model.DeviceAlias(alias)

# Configuration from environment variables
grobro_mqtt_config = model.MQTTConfig.from_env(
    prefix="SOURCE", defaults=model.MQTTConfig(host="localhost", port=1883)
)
ha_mqtt_config = model.MQTTConfig.from_env(prefix="TARGET", defaults=grobro_mqtt_config)
forward_mqtt_config = model.MQTTConfig.from_env(
    prefix="FORWARD", defaults=model.MQTTConfig(host="mqtt.growatt.com", port=7006)
)

# Setup Logger
LOG_LEVEL = os.getenv("LOG_LEVEL", "ERROR").upper()
try:
    logging.basicConfig(
        level=LOG_LEVEL,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
except Exception as e:
    logging.basicConfig(
        level=logging.ERROR,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    print(f"Failed to setup Logger {e} USING DEFAULT LOG Level(Error)")
LOG = logging.getLogger(__name__)


ha_client = ha.Client(ha_mqtt_config, REGISTER_FILTER)
grobro_client = grobro.Client(grobro_mqtt_config)

grobro_client.on_state = ha_client.publish_state
grobro_client.on_config = ha_client.set_config

running = True


def signal_handler(signum, frame):
    global running
    print(f"Signal {signum} received, shutting down...")
    running = False


# Register signal handlers for graceful shutdown
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# Assume client1 and client2 have .start() and .stop()
ha_client.start()
grobro_client.start()

try:
    while running:
        time.sleep(0.1)
finally:
    ha_client.stop()
    grobro_client.stop()
    print("Stopped both clients. Exiting.")

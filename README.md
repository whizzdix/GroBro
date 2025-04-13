# GroBro - Growatt MQTT Message Broker

GroBro is a bridge service that decodes encrypted MQTT packets from Growatt inverters and republishes them in a format compatible with Home Assistant. 
It supports auto-discovery via MQTT and allows full integration of Growatt data into your smart home.

![GroBro Logo](./assets/grobro_logo.png)

---

## Features
- Decodes and maps encrypted register payloads from Growatt NEO-series inverters and NOAH-series batteries
- Bridges inverter data from a dedicated MQTT source
- Proxies messages to the Growatt Cloud to keep the ShinePhone app functional (optional)
- Enables a local-only setup - keeping your device off the cloud
- Supports Home Assistant MQTT auto-discovery
- Containerized and configurable via environment variables

---

## Setup Instructions

1. Configure your **Growatt NEO inverter** or **NOAH battery** to send data to a custom MQTT broker
2. Configure a Mosquitto instance **with TLS**
3. Run **GroBro HA Bridge** Container

[View the Configuration Guide](CONFIGURATION.md) for details.

### Minimal Example 
Mosquitto TLS for Growatt to plain Mosquitto configured in Home Assistant

```bash
docker run --rm \
  -e SOURCE_MQTT_HOST=<source-mqtt-host> \
  -e SOURCE_MQTT_PORT=<source-mqtt-port> \
  -e SOURCE_MQTT_TLS=true \
  -e TARGET_MQTT_HOST=<target-mqtt-host> \
  -e TARGET_MQTT_PORT=<target-mqtt-port> \
  ghcr.io/robertzaage/grobro:latest
```

## Hint
Growatt NEO and NOAH devices rely on a TLS-enabled Mosquitto broker to send their packages. 
The full trust chain must be present, including the root certificate. [View the Certificates Guide](CERTIFICATES.md) for setup instructions.

## Contributions
Questions? Issues? PRs welcome!

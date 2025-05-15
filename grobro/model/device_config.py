from dataclasses import dataclass, asdict
import logging
import json
from typing import Optional
import os
from pydantic import BaseModel
from enum import Enum

LOG = logging.getLogger(__name__)

class DeviceAlias(str, Enum):
    NEO600 = "NEO600"
    NEO800 = "NEO800"
    NEO1000 = "NEO1000"
    NOAH = "NOAH"
    


class DeviceState(BaseModel):
    variable_name: str
    name: Optional[str] = None
    device_class: Optional[str] = None
    state_class: Optional[str] = None
    unit_of_measurement: Optional[str] = None
    icon: Optional[str] = None


class DeviceConfig(BaseModel):
    data_interval: Optional[str] = None
    unknown_5: Optional[str] = None
    unknown_6: Optional[str] = None
    password: Optional[str] = None
    serial_number: Optional[str] = None
    protocol_version: Optional[str] = None
    unknown_10: Optional[str] = None
    unknown_11: Optional[str] = None
    dns_address: Optional[str] = None
    device_type: Optional[str] = None
    local_ip: Optional[str] = None
    unknown_port: Optional[str] = None
    mac_address: Optional[str] = None
    remote_ip: Optional[str] = None
    remote_port: Optional[str] = None
    remote_url: Optional[str] = None
    model_id: Optional[str] = None
    sw_version: Optional[str] = None
    hw_version: Optional[str] = None
    unknown_23: Optional[str] = None
    unknown_24: Optional[str] = None
    subnet_mask: Optional[str] = None
    default_gateway: Optional[str] = None
    unknown_27: Optional[str] = None
    unknown_28: Optional[str] = None
    unknown_29: Optional[str] = None
    timezone: Optional[str] = None
    datetime: Optional[str] = None
    wifi_signal: Optional[str] = None
    raw: Optional[str] = None

    @property
    def device_id(self) -> str:
        return self.serial_number

    def to_file(self, file_path: str) -> str:
        with open(file_path, "w") as f:
            f.write(self.model_dump_json(exclude_none=True))

    @staticmethod
    def from_file(file_path: str) -> Optional["DeviceConfig"]:
        if not os.path.exists(file_path):
            return None
        try:
            with open(file_path, "r") as f:
                data = json.load(f)
                LOG.debug(f"loaded {data}")
                return DeviceConfig(**data)
        except Exception as e:
            LOG.error(f"Failed to load config {file_path}: {e}")
            return None

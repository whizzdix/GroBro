from typing import Optional, Union
from enum import Enum
from pydantic import BaseModel
import importlib.resources as resources
import json
import struct


class GrowattRegisterDataTypes(str, Enum):
    ENUM = "ENUM"
    STRING = "STRING"
    FLOAT = "FLOAT"
    INT = "INT"
    TIME_HHMM = "TIME_HHMM"


class GrowattRegisterEnumTypes(str, Enum):
    INT_MAP = "INT_MAP"
    BITFIELD = "BITFIELD"


class GrowattRegisterFloatOptions(BaseModel):
    delta: float = 1
    multiplier: float = 1


class GrowattRegisterEnumOptions(BaseModel):
    enum_type: GrowattRegisterEnumTypes
    values: dict[int, str]


class GrowattRegisterDataType(BaseModel):
    data_type: GrowattRegisterDataTypes
    float_options: Optional[GrowattRegisterFloatOptions] = None
    enum_options: Optional[GrowattRegisterEnumOptions] = None

    def parse(self, data_raw: bytes):
        if not data_raw:
            return None
        unpack_type = {1: "!B", 2: "!H", 4: "!I"}[len(data_raw)]
        if self.data_type == GrowattRegisterDataTypes.FLOAT:
            opts = self.float_options
            value = struct.unpack(unpack_type, data_raw)[0]
            value *= opts.multiplier
            value += opts.delta
            return round(value, 3)
        elif self.data_type == GrowattRegisterDataTypes.TIME_HHMM:
            value = struct.unpack(unpack_type, data_raw)[0]
            h = value // 256
            m = value % 256
            return (h * 100) + m
        elif self.data_type == GrowattRegisterDataTypes.INT:
            value = struct.unpack(unpack_type, data_raw)[0]
            return value
        elif self.data_type == GrowattRegisterDataTypes.ENUM:
            opts = self.enum_options
            value = struct.unpack(unpack_type, data_raw)[0]
            if opts.enum_type == GrowattRegisterEnumTypes.BITFIELD:
                return None  # TODO: implement
            elif opts.enum_type == GrowattRegisterEnumTypes.INT_MAP:
                enum_value = opts.values.get(int(value), None)
                if not enum_value:
                    return None
                return value
        elif self.data_type == GrowattRegisterDataTypes.STRING:
            value = data_raw.decode("ascii", errors="ignore").strip("\x00")
            return value


class GrowattRegisterPosition(BaseModel):
    register_no: int
    offset: int = 0
    size: int = 2


class GrowattInputRegister(BaseModel):
    position: GrowattRegisterPosition
    data: GrowattRegisterDataType


class HomeAssistantHoldingRegister(BaseModel):
    name: str
    publish: bool
    type: str
    min: Optional[int] = None
    max: Optional[int] = None
    step: Optional[int] = None
    state_class: Optional[str] = None
    device_class: Optional[str] = None
    unit_of_measurement: Optional[str] = None
    icon: Optional[str] = None

    class Config:
        extra = "forbid"


class HomeassistantInputRegister(BaseModel):
    name: str
    publish: bool
    state_class: Optional[str] = None
    device_class: Optional[str] = None
    unit_of_measurement: Optional[str] = None
    icon: Optional[str] = None


class HomeAssistantHoldingRegisterValue(BaseModel):
    name: str
    value: Union[str, float, int]
    register: HomeAssistantHoldingRegister


class HomeAssistantHoldingRegisterInput(BaseModel):
    device_id: str
    payload: list[HomeAssistantHoldingRegisterValue] = []


class HomeAssistantInputRegister(BaseModel):
    device_id: str
    payload: dict[str, Union[str, float, int]] = {}


class GroBroInputRegister(BaseModel):
    growatt: GrowattInputRegister
    homeassistant: HomeassistantInputRegister


class GroBroHoldingRegister(BaseModel):
    growatt: Optional[GrowattInputRegister] = None
    homeassistant: HomeAssistantHoldingRegister


class GroBroRegisters(BaseModel):
    input_registers: dict[str, GroBroInputRegister]
    holding_registers: dict[str, GroBroHoldingRegister]


with resources.files(__package__).joinpath("growatt_neo_registers.json").open(
    "rb"
) as f:
    KNOWN_NEO_REGISTERS = GroBroRegisters.parse_obj(json.load(f))
with resources.files(__package__).joinpath("growatt_noah_registers.json").open(
    "rb"
) as f:
    KNOWN_NOAH_REGISTERS = GroBroRegisters.parse_obj(json.load(f))

with resources.files(__package__).joinpath("growatt_nexa_registers.json").open(
    "rb"
) as f:
    KNOWN_NEXA_REGISTERS = GroBroRegisters.parse_obj(json.load(f))

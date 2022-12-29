from enum import auto
from dataclasses import dataclass
from typing import Dict, List

from . import enum_helper

HAS_TOBII_RESEARCH = False
ET_class = None
try:
    import tobii_research
    HAS_TOBII_RESEARCH = True
    ET_class = tobii_research.EyeTracker
except ImportError:
    pass


@enum_helper.get('eye tracker attributes')
class Attribute(enum_helper.AutoNameSpace):
    Connected       = auto()    # is an eye tracker connected or not?
    Serial          = auto()
    Name            = auto()
    Model           = auto()
    Firmware_version= auto()
    Address         = auto()
    Frequency       = auto()
    Tracking_mode   = auto()

@dataclass
class EyeTracker:
    connected       : bool = False
    serial          : str = None
    name            : str = None
    model           : str = None
    firmware_version: str = None
    address         : str = None
    frequency       : int = None
    tracking_mode   : str = None

def get():
    if not HAS_TOBII_RESEARCH:
        return None

    eye_trackers = tobii_research.find_all_eyetrackers()
    # if any, return first
    if eye_trackers:
        return eye_trackers[0]

def subscribe_to_notifications():
    pass

def get_attribute(eye_tracker: 'tobii_research.EyeTracker', attributes: List[Attribute]|str):
    def return_not_connected():
        if Attribute.Connected in attributes:
            return {Attribute.Connected: False}
        else:
            return {}

    if attributes=='*':
        attributes = [a for a in Attribute]

    # try and check we're still connected by doing a cheap call
    # this will also fail if eye_tracker is None, that is fine,
    # any failure is after all interpreted as not connected
    try:
        eye_tracker.get_gaze_output_frequency()
    except:
        return return_not_connected()

    out = {}
    try:
        for attr in attributes:
            match attr:
                case Attribute.Connected:
                    out[Attribute.Connected] = True
                case Attribute.Serial:
                    out[Attribute.Serial] = eye_tracker.serial_number
                case Attribute.Name:
                    out[Attribute.Name] = eye_tracker.device_name
                case Attribute.Model:
                    out[Attribute.Model] = eye_tracker.model
                case Attribute.Firmware_version:
                    out[Attribute.Firmware_version] = 'not supported'
                case Attribute.Address:
                    out[Attribute.Address] = eye_tracker.address
                case Attribute.Frequency:
                    out[Attribute.Frequency] = eye_tracker.get_gaze_output_frequency()
                case Attribute.Tracking_mode:
                    out[Attribute.Tracking_mode] = eye_tracker.get_eye_tracking_mode()
    except:
        return return_not_connected()

    return out

def update_attributes(eye_tracker: EyeTracker, attributes: Dict[Attribute,bool|str|int]):
    for attr in attributes:
        match attr:
            case Attribute.Connected:
                eye_tracker.connected = attributes[attr]
            case Attribute.Serial:
                eye_tracker.serial = attributes[attr]
            case Attribute.Name:
                eye_tracker.name = attributes[attr]
            case Attribute.Model:
                eye_tracker.model = attributes[attr]
            case Attribute.Firmware_version:
                eye_tracker.firmware_version = attributes[attr]
            case Attribute.Address:
                eye_tracker.address = attributes[attr]
            case Attribute.Frequency:
                eye_tracker.frequency = attributes[attr]
            case Attribute.Tracking_mode:
                eye_tracker.tracking_mode = attributes[attr]
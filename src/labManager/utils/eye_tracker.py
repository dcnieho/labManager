from enum import auto
from dataclasses import dataclass
from typing import Dict, List

from . import async_thread, enum_helper
from .network import comms, message

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
    Serial          = auto()
    Name            = auto()
    Model           = auto()
    Firmware_version= auto()
    Address         = auto()
    Frequency       = auto()
    Tracking_mode   = auto()

class Status(enum_helper.AutoNameSpace):
    Not_connected   = auto()
    Connected       = auto()
    Calibrating     = auto()

class Event(enum_helper.AutoNameSpace):
    Connection_lost     = auto()
    Connection_restored = auto()
    Calibration_changed = auto()
    Device_fault        = auto()
    Device_warning      = auto()

@dataclass
class EyeTracker:
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

def _get_notifications():
    return (
        tobii_research.EYETRACKER_NOTIFICATION_CONNECTION_LOST,
        tobii_research.EYETRACKER_NOTIFICATION_CONNECTION_RESTORED,
        tobii_research.EYETRACKER_NOTIFICATION_CALIBRATION_MODE_ENTERED,
        tobii_research.EYETRACKER_NOTIFICATION_CALIBRATION_MODE_LEFT,
        tobii_research.EYETRACKER_NOTIFICATION_CALIBRATION_CHANGED,
        tobii_research.EYETRACKER_NOTIFICATION_GAZE_OUTPUT_FREQUENCY_CHANGED,
        tobii_research.EYETRACKER_NOTIFICATION_EYE_TRACKING_MODE_CHANGED,
        tobii_research.EYETRACKER_NOTIFICATION_DEVICE_FAULTS,
        tobii_research.EYETRACKER_NOTIFICATION_DEVICE_WARNINGS)

def notification_callback(notification, data, eye_tracker, writer):
    msg   = {'serial': eye_tracker.serial_number, 'timestamp': data.system_time_stamp}
    mtype = None
    match notification:
        case tobii_research.EYETRACKER_NOTIFICATION_CONNECTION_LOST | \
             tobii_research.EYETRACKER_NOTIFICATION_CONNECTION_RESTORED | \
             tobii_research.EYETRACKER_NOTIFICATION_CALIBRATION_MODE_ENTERED | \
             tobii_research.EYETRACKER_NOTIFICATION_CALIBRATION_MODE_LEFT:
            mtype = message.Message.ET_STATUS_INFORM
            if   notification==tobii_research.EYETRACKER_NOTIFICATION_CONNECTION_LOST:
                msg['status'] = Status.Not_connected
            elif notification==tobii_research.EYETRACKER_NOTIFICATION_CALIBRATION_MODE_ENTERED:
                msg['status'] = Status.Calibrating
            else:
                msg['status'] = Status.Connected

        case tobii_research.EYETRACKER_NOTIFICATION_DEVICE_FAULTS:
            mtype = message.Message.ET_EVENT
            msg['event'] = 'fault'
            msg['info']  = data.faults
        case tobii_research.EYETRACKER_NOTIFICATION_DEVICE_WARNINGS:
            mtype = message.Message.ET_EVENT
            msg['event'] = 'warning'
            msg['info']  = data.warnings
        case tobii_research.EYETRACKER_NOTIFICATION_CALIBRATION_CHANGED:
            mtype = message.Message.ET_EVENT
            msg['event'] = 'calibration_changed'

        case tobii_research.EYETRACKER_NOTIFICATION_GAZE_OUTPUT_FREQUENCY_CHANGED:
            mtype = message.Message.ET_ATTR_UPDATE
            msg['attributes'] = {Attribute.Frequency: data.gaze_output_frequency}
        case tobii_research.EYETRACKER_NOTIFICATION_EYE_TRACKING_MODE_CHANGED:
            mtype = message.Message.ET_ATTR_UPDATE
            msg['attributes'] = {Attribute.Tracking_mode: eye_tracker.get_eye_tracking_mode()}
    
    # if handled notification type, send to master
    if mtype:
        async_thread.run(comms.send_typed_message(writer, mtype, message))

def subscribe_to_notifications(eye_tracker: ET_class, writer):
    for notification in _get_notifications():
        eye_tracker.subscribe_to(notification,
            lambda x, note: notification_callback(note, x, writer))

def unsubscribe_from_notifications(eye_tracker: ET_class):
    for notification in _get_notifications():
        eye_tracker.unsubscribe_from(notification)

def get_attribute(eye_tracker: ET_class, attributes: List[Attribute]|str):
    if attributes=='*':
        attributes = [a for a in Attribute]

    # Try and check we're still connected by doing a cheap call.
    # This will also fail if eye_tracker is None, that is fine,
    # any failure is after all interpreted as not connected
    try:
        eye_tracker.get_gaze_output_frequency()
    except:
        return {}

    out = {}
    try:
        for attr in attributes:
            match attr:
                case Attribute.Serial:
                    out[Attribute.Serial] = eye_tracker.serial_number
                case Attribute.Name:
                    out[Attribute.Name] = eye_tracker.device_name
                case Attribute.Model:
                    out[Attribute.Model] = eye_tracker.model
                case Attribute.Firmware_version:
                    out[Attribute.Firmware_version] = eye_tracker.firmware_version
                case Attribute.Address:
                    out[Attribute.Address] = eye_tracker.address
                case Attribute.Frequency:
                    out[Attribute.Frequency] = eye_tracker.get_gaze_output_frequency()
                case Attribute.Tracking_mode:
                    out[Attribute.Tracking_mode] = eye_tracker.get_eye_tracking_mode()
    except:
        return {}

    return out

def update_attributes(eye_tracker: EyeTracker, attributes: Dict[Attribute,bool|str|int]):
    for attr in attributes:
        match attr:
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
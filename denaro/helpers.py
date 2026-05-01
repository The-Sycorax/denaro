import hashlib
import json
from enum import Enum
from math import ceil
from datetime import datetime, timezone
from typing import Union

import base58
from fastecdsa.point import Point
from fastecdsa.util import mod_sqrt

from .constants import ENDIAN, CURVE

class AddressFormat(Enum):
    """
    Enumeration to represent different address formats.
    """
    FULL_HEX = 'hex'
    COMPRESSED = 'compressed'


def get_json(obj):
    """
    Convert an object to its JSON representation and then back to a dictionary.
    
    Parameters:
        obj: Object to convert. Can be a dictionary, list, or custom object.
        
    Returns:
        dict: Object as a dictionary.
    """
    return json.loads(
        json.dumps(obj, default=lambda o: getattr(o, 'as_dict', getattr(o, '__dict__', str(o))))
    )


def timestamp():
    """
    Get the current UTC timestamp.
    
    Returns:
        int: Current timestamp in UTC timezone.
    """
    return int(datetime.now(timezone.utc).replace(tzinfo=timezone.utc).timestamp())


def sha256(message: Union[str, bytes]):
    """
    Compute the SHA-256 hash of the given message.
    
    Parameters:
        message (Union[str, bytes]): Message to hash. Can be a string or bytes.
        
    Returns:
        str: SHA-256 hash in hexadecimal format.
    """
    if isinstance(message, str):
        message = bytes.fromhex(message)
    return hashlib.sha256(message).hexdigest()


def byte_length(i: int):
    """
    Calculate the byte length of an integer.
    
    Parameters:
        i (int): Integer whose byte length is to be calculated.
        
    Returns:
        int: Byte length of the integer.
    """
    return ceil(i.bit_length() / 8.0)


def normalize_block(block) -> dict:
    """
    Normalize a block by trimming spaces and converting timestamps.
    
    Parameters:
        block (dict): Block data to normalize.
        
    Returns:
        dict: Normalized block data.
    """
    block = dict(block)
    block['address'] = block['address'].strip(' ')
    block['timestamp'] = int(block['timestamp'])
    return block


def x_to_y(x: int, is_odd: bool = False):
    """
    Given the x-coordinate, compute the y-coordinate on the elliptic curve.
    
    Parameters:
        x (int): x-coordinate on the elliptic curve.
        is_odd (bool, optional): Whether the y-coordinate should be odd. Defaults to False.
        
    Returns:
        int: Computed y-coordinate based on the x-coordinate.
    """
    a, b, p = CURVE.a, CURVE.b, CURVE.p
    y2 = x ** 3 + a * x + b
    y_res, y_mod = mod_sqrt(y2, p)
    return y_res if y_res % 2 == is_odd else y_mod


def point_to_bytes(point: Point, address_format: AddressFormat = AddressFormat.FULL_HEX) -> bytes:
    """
    Convert an ECDSA point to bytes based on the address format.
    
    Parameters:
        point (Point): ECDSA point to convert.
        address_format (AddressFormat, optional): Format to use for the conversion. Defaults to AddressFormat.FULL_HEX.
        
    Returns:
        bytes: Point in byte format.
    """
    if address_format is AddressFormat.FULL_HEX:
        return point.x.to_bytes(32, byteorder=ENDIAN) + point.y.to_bytes(32, byteorder=ENDIAN)
    elif address_format is AddressFormat.COMPRESSED:
        return string_to_bytes(point_to_string(point, AddressFormat.COMPRESSED))
    else:
        raise NotImplementedError()


def bytes_to_point(point_bytes: bytes) -> Point:
    """
    Convert bytes to an ECDSA point.
    
    Parameters:
        point_bytes (bytes): Bytes to convert.
        
    Returns:
        Point: Converted ECDSA point.
    """
    if len(point_bytes) == 64:
        x, y = int.from_bytes(point_bytes[:32], ENDIAN), int.from_bytes(point_bytes[32:], ENDIAN)
        return Point(x, y, CURVE)
    elif len(point_bytes) == 33:
        specifier = point_bytes[0]
        x = int.from_bytes(point_bytes[1:], ENDIAN)
        return Point(x, x_to_y(x, specifier == 43))
    else:
        raise NotImplementedError()


def bytes_to_string(point_bytes: bytes) -> str:
    """
    Convert point bytes to its string representation based on its format (full or compressed).
    
    Parameters:
        point_bytes (bytes): Bytes representing the point.
        
    Returns:
        str: String representation of the point.
    """
    point = bytes_to_point(point_bytes)
    if len(point_bytes) == 64:
        address_format = AddressFormat.FULL_HEX
    elif len(point_bytes) == 33:
        address_format = AddressFormat.COMPRESSED
    else:
        raise NotImplementedError()
    return point_to_string(point, address_format)


def point_to_string(point: Point, address_format: AddressFormat = AddressFormat.COMPRESSED) -> str:
    """
    Convert an ECDSA point to its string representation.
    
    Parameters:
        point (Point): ECDSA point to convert.
        address_format (AddressFormat, optional): The format to use for the conversion. Defaults to AddressFormat.COMPRESSED.
        
    Returns:
        str: String representation of the point.
    """
    if address_format is AddressFormat.FULL_HEX:
        point_bytes = point_to_bytes(point)
        return point_bytes.hex()
    elif address_format is AddressFormat.COMPRESSED:
        x, y = point.x, point.y
        address = base58.b58encode((42 if y % 2 == 0 else 43).to_bytes(1, ENDIAN) + x.to_bytes(32, ENDIAN))
        return address if isinstance(address, str) else address.decode('utf-8')
    else:
        raise NotImplementedError()


def string_to_bytes(string: str) -> bytes:
    """
    Convert a string to bytes. The function handles both hexadecimal and Base58 encoded strings.
    
    Parameters:
        string (str): The string to convert.
        
    Returns:
        bytes: The converted bytes.
    """
    try:
        point_bytes = bytes.fromhex(string)
    except ValueError:
        point_bytes = base58.b58decode(string)
    return point_bytes


def string_to_point(string: str) -> Point:
    """
    Converts a string to an ECDSA point. The function handles both hexadecimal and Base58 encoded strings.
    
    Parameters:
        string (str): The string to convert.
        
    Returns:
        Point: The converted ECDSA point.
    """
    return bytes_to_point(string_to_bytes(string))

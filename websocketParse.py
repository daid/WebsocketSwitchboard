import struct
import itertools


fin_mask = 0x80
rsv_mask = 0x70
opcode_mask = 0x0f
    
mask_mask = 0x80;
payload_length_mask = 0x7f
payload_length_16bit = 126
payload_length_64bit = 127
    
opcode_continuation = 0x00
opcode_text = 0x01
opcode_binary = 0x02
opcode_close = 0x08
opcode_ping = 0x09
opcode_pong = 0x0a


def readFrame(stream):
    header = stream.read(2)
    if len(header) != 2:
        return None
    payload_length = header[1] & payload_length_mask
    opcode = header[0] & opcode_mask
    fin = header[0] & fin_mask
    rsv = header[0] & rsv_mask
    mask = header[1] & mask_mask
    
    if rsv != 0x00:
        return None
    
    if payload_length == payload_length_16bit:
        payload_length_buffer = stream.read(2)
        if len(payload_length_buffer) != 2:
            return None
        payload_length = struct.unpack(">H", payload_length_buffer)[0]
    elif payload_length == payload_length_64bit:
        payload_length_buffer = stream.read(8)
        if len(payload_length_buffer) != 8:
            return None
        payload_length = struct.unpack(">Q", payload_length_buffer)[0]
    mask_data = None
    if mask:
        mask_data = stream.read(4)
    message = stream.read(payload_length)
    if mask:
        message = bytes(b ^ m for b, m in zip(message, itertools.cycle(mask_data)))
    return fin, opcode, message


def writeFrame(stream, opcode, message):
    if len(message) < payload_length_16bit:
        stream.write(struct.pack(">BB", fin_mask | opcode, len(message)))
    elif len(message) < (1 << 16):
        stream.write(struct.pack(">BBH", fin_mask | opcode, payload_length_16bit, len(message)))
    else:
        stream.write(struct.pack(">BBQ", fin_mask | opcode, payload_length_64bit, len(message)))
    stream.write(message)

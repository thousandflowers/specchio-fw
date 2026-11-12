#!/usr/bin/env python3
"""Read the air (PM2.5, humidity, temperature, filter life) from the Xiaomi purifier on the home network.

    purifier.py          # print the values now
    purifier.py check    # self-check, no network

Needs purifier.json (address and key), written by xiaomi_login.py. Talks to the
device directly (miIO protocol, UDP 54321): no cloud. Where each value lives
inside the device is looked up once in Xiaomi's public device descriptions
(miot-spec.org) by model, then remembered in purifier.json.
Standard library plus the Mac's own openssl for AES.
"""
import hashlib
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

CONFIG = Path(__file__).with_name("purifier.json")
PORT = 54321
HELLO = bytes.fromhex("21310020" + "ff" * 28)
HEADER = 32
TIMEOUT_S = 3
TRIES = 3
SPEC_LIST = "https://miot-spec.org/miot-spec-v2/instances?status=all"
SPEC_ONE = "https://miot-spec.org/miot-spec-v2/instance?type={urn}"
PM25_NAME = "pm2.5-density"
READINGS = (PM25_NAME, "relative-humidity", "temperature", "filter-life-level")   # names from the device description


def aes(key: bytes, vector: bytes, data: bytes, is_decrypt: bool) -> bytes:
    """AES-128-CBC with PKCS7 padding through the system openssl (the standard library has no AES)."""
    command = ["/usr/bin/openssl", "enc", "-aes-128-cbc", "-K", key.hex(), "-iv", vector.hex()]
    result = subprocess.run(command + (["-d"] if is_decrypt else []), input=data, capture_output=True, check=True)
    return result.stdout


def keys(token: bytes) -> tuple[bytes, bytes]:
    key = hashlib.md5(token).digest()
    return key, hashlib.md5(key + token).digest()


def pack(token: bytes, device_id: bytes, stamp: int, message: dict) -> bytes:
    body = aes(*keys(token), json.dumps(message).encode(), False)
    head = bytes.fromhex("2131") + (HEADER + len(body)).to_bytes(2, "big") + bytes(4) + device_id + stamp.to_bytes(4, "big")
    return head + hashlib.md5(head + token + body).digest() + body


def unpack(token: bytes, packet: bytes) -> dict:
    return json.loads(aes(*keys(token), packet[HEADER:], True).rstrip(b"\x00"))


def udp(address: str, payload: bytes) -> bytes:
    """One datagram out, one back. Falls back to the Mac's own nc where macOS keeps Python off the home network."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as link:
            link.settimeout(TIMEOUT_S)
            link.sendto(payload, (address, PORT))
            return link.recvfrom(4096)[0]
    except TimeoutError:
        raise
    except OSError:
        result = subprocess.run(["/usr/bin/nc", "-u", "-w", str(TIMEOUT_S), address, str(PORT)],
                                input=payload, capture_output=True, check=False)
        if not result.stdout:
            raise TimeoutError("il purificatore non risponde") from None
        return result.stdout


def ask(address: str, token: bytes, method: str, params: list) -> dict:
    for attempt in range(TRIES):
        try:
            hello = udp(address, HELLO)
            stamp = int.from_bytes(hello[12:16], "big") + 1
            message = {"id": int(time.time()) % 100000 + attempt, "method": method, "params": params}
            return unpack(token, udp(address, pack(token, hello[8:12], stamp, message)))
        except (TimeoutError, subprocess.CalledProcessError, ValueError) as error:
            last = error
    raise OSError(f"il purificatore non risponde ({last})")


def find_property(spec: dict, name: str) -> tuple[int, int]:
    """(service id, property id) of the property whose type names it, e.g. '...:property:pm2.5-density:...'."""
    for service in spec["services"]:
        for prop in service.get("properties", []):
            if f":property:{name}:" in prop["type"]:
                return service["iid"], prop["iid"]
    raise KeyError(f"{name} non c'è nella descrizione del dispositivo")


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as reply:
        return json.load(reply)


def locate(model: str) -> dict[str, list[int]]:
    released = [entry for entry in fetch_json(SPEC_LIST)["instances"] if entry["model"] == model]
    if not released:
        raise KeyError(f"modello {model} sconosciuto a miot-spec.org")
    newest = max(released, key=lambda entry: entry.get("version", 0))
    spec = fetch_json(SPEC_ONE.format(urn=newest["type"]))
    return {name: list(find_property(spec, name)) for name in READINGS}


def read_air() -> dict[str, float]:
    """Every reading the device answers, by name (see READINGS)."""
    config = json.loads(CONFIG.read_text())
    if set(READINGS) - set(config.get("properties", {})):
        config = {**config, "properties": locate(config["model"])}
        CONFIG.write_text(json.dumps(config, indent=1))
    places = {tuple(place): name for name, place in config["properties"].items() if name in READINGS}
    reply = ask(config["localip"], bytes.fromhex(config["token"]), "get_properties",
                [{"did": str(config["did"]), "siid": service, "piid": prop} for service, prop in places])
    return {places[(item["siid"], item["piid"])]: item["value"] for item in reply["result"] if item.get("code") == 0}


def read_pm25() -> int:
    return int(read_air()[PM25_NAME])


def check() -> None:
    token = bytes.fromhex("00112233445566778899aabbccddeeff")
    packet = pack(token, b"\x13\xff\x6b\x35", 1234, {"id": 1, "method": "miIO.info", "params": []})
    assert packet[:2] == b"\x21\x31" and int.from_bytes(packet[2:4], "big") == len(packet)
    assert unpack(token, packet) == {"id": 1, "method": "miIO.info", "params": []}, "what we pack, the device can read"
    spec = {"services": [{"iid": 2, "properties": [{"iid": 1, "type": "urn:miot-spec-v2:property:on:1"}]},
                         {"iid": 3, "properties": [{"iid": 4, "type": "urn:miot-spec-v2:property:pm2.5-density:00000034:x:1"}]}]}
    assert find_property(spec, PM25_NAME) == (3, 4)
    print("purifier ok")


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["check"]:
            check()
        else:
            print("  ".join(f"{name}: {value}" for name, value in read_air().items()))
    except (OSError, KeyError, ValueError) as error:
        sys.exit(f"Non riuscito: {error}")

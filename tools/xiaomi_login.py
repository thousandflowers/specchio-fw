#!/usr/bin/env python3
"""Get the local key (token) of one Xiaomi device, signing in through Xiaomi's own page.

    xiaomi_login.py 192.0.2.131      # the device's address on the home network
    xiaomi_login.py check                # self-check, no network

The browser opens Xiaomi's sign-in page: the password is typed there, never here.
Only the entry of the device asked for is kept, in purifier.json next to this
file (not in git); the key is never printed. Standard library only.

Protocol as documented by the open-source Xiaomi-cloud-tokens-extractor
(QR sign-in, then RC4-signed calls to the Mi Home cloud).
"""
import base64
import hashlib
import http.cookiejar
import json
import os
import random
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

ACCOUNT = "https://account.xiaomi.com/longPolling/loginUrl"
SERVERS = ("de", "cn", "us", "ru", "tw", "sg", "in", "i2")   # Europe first: the likely one
KEPT_FIELDS = ("name", "model", "did", "localip", "token")
DESTINATION = Path(__file__).with_name("purifier.json")
RC4_DROP = 1024          # Xiaomi discards the first 1024 bytes of the key stream
POLL_TIMEOUT_S = 10
NET_TIMEOUT_S = 20
JSON_PREFIX = "&&&START&&&"


def rc4(key: bytes, data: bytes, drop: int = RC4_DROP) -> bytes:
    state = list(range(256))
    j = 0
    for i in range(256):
        j = (j + state[i] + key[i % len(key)]) % 256
        state[i], state[j] = state[j], state[i]
    i = j = 0
    output = bytearray()
    for index in range(drop + len(data)):
        i = (i + 1) % 256
        j = (j + state[i]) % 256
        state[i], state[j] = state[j], state[i]
        if index >= drop:
            output.append(data[index - drop] ^ state[(state[i] + state[j]) % 256])
    return bytes(output)


def encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def signed_nonce(ssecurity: str, nonce: str) -> str:
    return encode(hashlib.sha256(base64.b64decode(ssecurity) + base64.b64decode(nonce)).digest())


def signature(path: str, fields: dict[str, str], key: str) -> str:
    parts = ["POST", path, *(f"{name}={value}" for name, value in fields.items()), key]
    return encode(hashlib.sha1("&".join(parts).encode()).digest())


def signed_fields(path: str, data: str, ssecurity: str, nonce: str) -> dict[str, str]:
    """The query Xiaomi expects: every value RC4-encrypted, signed before and after encryption."""
    key = signed_nonce(ssecurity, nonce)
    plain = {"data": data}
    plain = {**plain, "rc4_hash__": signature(path, plain, key)}
    secret = {name: encode(rc4(base64.b64decode(key), value.encode())) for name, value in plain.items()}
    return {**secret, "signature": signature(path, secret, key), "ssecurity": ssecurity, "_nonce": nonce}


class Session:
    """What a finished sign-in gives: who we are and the keys to sign the calls."""

    def __init__(self, user_id: str, ssecurity: str, service_token: str, agent: str) -> None:
        self.user_id = user_id
        self.ssecurity = ssecurity
        self.service_token = service_token
        self.agent = agent

    def call(self, server: str, path: str, data: str) -> dict | None:
        nonce = encode(os.urandom(8) + int(time.time() / 60).to_bytes(4, "big"))
        fields = signed_fields(path, data, self.ssecurity, nonce)
        host = "api.io.mi.com" if server == "cn" else f"{server}.api.io.mi.com"
        request = urllib.request.Request(
            f"https://{host}/app{path}?{urllib.parse.urlencode(fields)}", data=b"", method="POST",
            headers={
                "Accept-Encoding": "identity",
                "User-Agent": self.agent,
                "Content-Type": "application/x-www-form-urlencoded",
                "x-xiaomi-protocal-flag-cli": "PROTOCAL-HTTP2",
                "MIOT-ENCRYPT-ALGORITHM": "ENCRYPT-RC4",
                "Cookie": f"userId={self.user_id}; yetAnotherServiceToken={self.service_token}; "
                          f"serviceToken={self.service_token}; locale=en_GB; timezone=GMT+02:00; "
                          "is_daylight=1; dst_offset=3600000; channel=MI_APP_STORE",
            })
        try:
            with urllib.request.urlopen(request, timeout=NET_TIMEOUT_S) as reply:
                body = reply.read().decode()
            key = base64.b64decode(signed_nonce(self.ssecurity, nonce))
            return json.loads(rc4(key, base64.b64decode(body)))
        except (OSError, ValueError):
            return None


def parse(text: str) -> dict:
    return json.loads(text.replace(JSON_PREFIX, ""))


def sign_in() -> Session:
    agent_tail = "".join(random.choices("ABCDE", k=13))
    agent = f"{''.join(random.choices(string.ascii_lowercase, k=18))}-{agent_tail} APP/com.xiaomi.mihome APPV/10.5.201"
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [("User-Agent", agent)]
    query = urllib.parse.urlencode({
        "_qrsize": "480", "qs": "%3Fsid%3Dxiaomiio%26_json%3Dtrue", "callback": "https://sts.api.io.mi.com/sts",
        "_hasLogo": "false", "sid": "xiaomiio", "serviceParam": "", "_locale": "en_GB",
        "_dc": str(int(time.time() * 1000)),
    })
    with opener.open(f"{ACCOUNT}?{query}", timeout=NET_TIMEOUT_S) as reply:
        start = parse(reply.read().decode())
    print("Si apre la pagina di Xiaomi nel browser: entra con il tuo account e conferma.")
    print("Se la pagina chiede di usare il telefono, apri l'app Xiaomi Home e inquadra questo codice:")
    print(f"  {start['qr']}")
    webbrowser.open(start["loginUrl"])

    deadline = time.monotonic() + float(start["timeout"])
    print(f"Aspetto la conferma (fino a {float(start['timeout']) / 60:.0f} minuti)…")
    while True:
        try:
            with opener.open(start["lp"], timeout=POLL_TIMEOUT_S) as reply:
                done = parse(reply.read().decode())
            break
        except (TimeoutError, urllib.error.URLError, OSError) as error:   # nothing yet: ask again
            if time.monotonic() > deadline:
                raise OSError("tempo scaduto senza conferma") from error
    with opener.open(urllib.request.Request(done["location"], headers={"content-type": "application/x-www-form-urlencoded"}),
                     timeout=NET_TIMEOUT_S):
        pass
    tokens = [cookie.value for cookie in jar if cookie.name == "serviceToken"]
    if not tokens:
        raise OSError("Xiaomi non ha dato il permesso di leggere i dispositivi")
    return Session(str(done["userId"]), done["ssecurity"], tokens[0], agent)


def devices_on(session: Session, server: str) -> list[dict]:
    homes = session.call(server, "/v2/homeroom/gethome",
                         '{"fg": true, "fetch_share": true, "fetch_share_dev": true, "limit": 300, "app_ver": 7}')
    shared = session.call(server, "/v2/user/get_device_cnt", '{ "fetch_own": true, "fetch_share": true}')
    owners = [(home["id"], session.user_id) for home in ((homes or {}).get("result") or {}).get("homelist") or []]
    owners += [(home["home_id"], home["home_owner"])
               for home in (((shared or {}).get("result") or {}).get("share") or {}).get("share_family") or []]
    found = []
    for home_id, owner in owners:
        reply = session.call(server, "/v2/home/home_device_list",
                             '{"home_owner": ' + str(owner) + ',"home_id": ' + str(home_id)
                             + ',  "limit": 200,  "get_split_device": true, "support_smart_home": true}')
        found += ((reply or {}).get("result") or {}).get("device_info") or []
    return found


def main(address: str) -> None:
    session = sign_in()
    print("Accesso riuscito, cerco il dispositivo…")
    seen = []
    for server in SERVERS:
        devices = devices_on(session, server)
        seen += [f"{device.get('name')} ({device.get('model')}, {device.get('localip')})" for device in devices]
        match = [device for device in devices if device.get("localip") == address]
        if match:
            kept = {**{field: match[0].get(field) for field in KEPT_FIELDS}, "server": server}
            DESTINATION.write_text(json.dumps(kept, indent=1))
            DESTINATION.chmod(0o600)
            print(f"Fatto: trovato \"{kept['name']}\" ({kept['model']}). Chiave salvata, torna da Claude.")
            return
    sys.exit(f"Nessun dispositivo con indirizzo {address}. Visti nell'account: {', '.join(seen) or 'nessuno'}")


def check() -> None:
    assert rc4(b"Key", b"Plaintext", drop=0).hex() == "bbf316e8d940af0ad3", "RFC 6229-style known vector"
    key = base64.b64decode(signed_nonce(encode(b"s" * 16), encode(b"n" * 12)))
    assert rc4(key, rc4(key, b"hello")) == b"hello", "decrypting is the same operation"
    fields = signed_fields("/v2/x", "{}", encode(b"s" * 16), encode(b"n" * 12))
    assert list(fields) == ["data", "rc4_hash__", "signature", "ssecurity", "_nonce"]
    assert rc4(key, base64.b64decode(fields["data"])) == b"{}"
    assert parse(JSON_PREFIX + '{"a": 1}') == {"a": 1}
    print("xiaomi ok")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    try:
        check() if sys.argv[1] == "check" else main(sys.argv[1])
    except (OSError, KeyError, ValueError) as error:
        sys.exit(f"Non riuscito: {error}")
    except KeyboardInterrupt:
        sys.exit("\nfermato")

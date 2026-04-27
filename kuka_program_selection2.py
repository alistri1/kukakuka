import base64
import json
import pickle
import re
import requests
import urllib3
from pathlib import Path

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = "https://192.168.43.1"
COOKIES_FILE = Path("session_cookies.pkl")

HEADERS = {
    "accept": "application/grpc-web-text",
    "accept-language": "bs-BA,bs;q=0.9,en-US;q=0.8,en;q=0.7",
    "content-type": "application/grpc-web-text",
    "origin": BASE,
    "referer": f"{BASE}/Program/Program/Parameters",
    "user-agent": "Mozilla/5.0",
    "x-grpc-web": "1",
    "x-user-agent": "grpc-web-javascript/0.1",
}


def load_session():
    s = requests.Session()
    s.verify = False
    if not COOKIES_FILE.exists():
        raise FileNotFoundError("Run login first; session_cookies.pkl missing.")
    with open(COOKIES_FILE, "rb") as f:
        s.cookies.update(pickle.load(f))
    return s


def grpc_encode(payload: bytes) -> str:
    frame = b"\x00" + len(payload).to_bytes(4, "big") + payload
    return base64.b64encode(frame).decode("ascii")


def decode_grpc(text: str):
    chunks = []
    remaining = text.strip()

    while remaining:
        found = False
        for i in range(4, len(remaining) + 1, 4):
            part = remaining[:i]
            try:
                chunks.append(base64.b64decode(part, validate=True))
                remaining = remaining[i:]
                found = True
                break
            except Exception:
                continue
        if not found:
            break

    raw = b"".join(chunks)
    frames = []
    i = 0

    while i + 5 <= len(raw):
        frame_type = raw[i]
        frame_len = int.from_bytes(raw[i + 1:i + 5], "big")
        frame_data = raw[i + 5:i + 5 + frame_len]
        frames.append((frame_type, frame_data))
        i += 5 + frame_len

    return frames


def read_varint(buf, start):
    value = 0
    shift = 0
    i = start

    while i < len(buf):
        b = buf[i]
        value |= (b & 0x7F) << shift
        i += 1
        if not b & 0x80:
            return value, i
        shift += 7

    raise ValueError("Bad varint")


def post_grpc(session, path, body):
    r = session.post(BASE + path, headers=HEADERS, data=body, timeout=15)
    return r, decode_grpc(r.text)


def empty_body():
    return grpc_encode(b"")


def tree_body(tree_id):
    payload = b"\x0A" + bytes([len(tree_id)]) + tree_id.encode()
    return grpc_encode(payload)


def select_body(tree_id, node_id):
    payload = (
        b"\x0A" + bytes([len(tree_id)]) + tree_id.encode() +
        b"\x12" + bytes([len(node_id)]) + node_id.encode()
    )
    return grpc_encode(payload)


def find_uuids(data: bytes):
    text = data.decode("utf-8", errors="ignore")
    return re.findall(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", text)


def get_tree_id(session):
    r, frames = post_grpc(
        session,
        "/kuka.easyprogramming.programtree.v1.ProgramTreeService/GetLoadedPrograms",
        empty_body(),
    )

    print("GetLoadedPrograms HTTP:", r.status_code)

    for frame_type, frame_data in frames:
        if frame_type == 0x00:
            uuids = find_uuids(frame_data)
            if uuids:
                print("Auto tree ID:", uuids[0])
                return uuids[0]

    raise RuntimeError("Could not extract tree_id from GetLoadedPrograms.")


def get_nodes_json(session, tree_id):
    body = tree_body(tree_id)

    preload = [
        "GetLoadedPrograms",
        "LoadProgramTree",
        "GetLoadedPrograms",
        "GetNodes",
    ]

    for method in preload:
        r, _ = post_grpc(
            session,
            f"/kuka.easyprogramming.programtree.v1.ProgramTreeService/{method}",
            body,
        )
        print(f"{method} HTTP:", r.status_code)

    r, frames = post_grpc(
        session,
        "/kuka.easyprogramming.programtree.v1.ProgramTreeService/GetNodesAsJson",
        body,
    )

    print("GetNodesAsJson HTTP:", r.status_code)
    return frames


def extract_nodes(frames):
    nodes = []

    for frame_type, frame_data in frames:
        if frame_type != 0x00:
            continue

        i = 0
        while i < len(frame_data):
            try:
                tag, i = read_varint(frame_data, i)
            except Exception:
                break

            wire_type = tag & 0x07

            if wire_type == 2:
                try:
                    size, i = read_varint(frame_data, i)
                except Exception:
                    break

                chunk = frame_data[i:i + size]
                i += size

                try:
                    text = chunk.decode("utf-8", errors="ignore")
                    if text.startswith("{"):
                        nodes.append(json.loads(text))
                except Exception:
                    pass

            elif wire_type == 0:
                _, i = read_varint(frame_data, i)
            else:
                break

    return nodes


def select_program(session, tree_id, node_id):
    body = select_body(tree_id, node_id)

    r, _ = post_grpc(
        session,
        "/kuka.easyprogramming.programtree.v1.ProgramTreeService/GetNodes",
        body,
    )

    print("Select GetNodes HTTP:", r.status_code)
    print("Response preview:", r.text[:250])

    return r.status_code == 200


def main():
    session = load_session()

    tree_id = get_tree_id(session)
    frames = get_nodes_json(session, tree_id)
    nodes = extract_nodes(frames)

    programs = [
        n for n in nodes
        if n.get("__st") == "TtSequenceNode" and str(n.get("id", "")).startswith("sequence_")
    ]

    if not programs:
        print("No sequence programs found.")
        return

    print("\nAvailable programs:")
    for i, p in enumerate(programs):
        print(f"{i}. name={p.get('name')!r} id={p.get('id')}")

    idx = int(input("\nSelect program index: ").strip())
    program = programs[idx]

    print("\nSelected:")
    print("Name:", program.get("name"))
    print("ID:", program.get("id"))

    ok = select_program(session, tree_id, program["id"])

    if ok:
        print("\nProgram selection request sent.")
    else:
        print("\nProgram selection failed.")


if __name__ == "__main__":
    main()
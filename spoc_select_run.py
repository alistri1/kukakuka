import base64, pickle, threading, time, re, json, requests, urllib3
from pathlib import Path

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = "https://192.168.43.1"
COOKIES_FILE = Path("session_cookies.pkl")

HEADERS_AUTH = {
    "accept": "application/grpc-web-text",
    "content-type": "application/grpc-web-text",
    "origin": BASE,
    "referer": f"{BASE}/Program/Program/Parameters",
    "user-agent": "Mozilla/5.0",
    "x-grpc-web": "1",
    "x-user-agent": "grpc-web-javascript/0.1",
}

HEADERS_OMIT = {
    "accept": "application/grpc-web-text",
    "content-type": "application/grpc-web-text",
    "x-grpc-web": "1",
    "x-user-agent": "grpc-web-javascript/0.1",
    "user-agent": "Mozilla/5.0",
}


def load_session():
    s = requests.Session()
    s.verify = False
    with open(COOKIES_FILE, "rb") as f:
        s.cookies.update(pickle.load(f))
    return s


def grpc(payload: bytes):
    return base64.b64encode(b"\x00" + len(payload).to_bytes(4, "big") + payload).decode()


def body_empty():
    return grpc(b"")


def body_uuid(uid):
    b = uid.encode()
    return grpc(b"\x0a" + bytes([len(b)]) + b)


def body_select(tree_id, node_id):
    t, n = tree_id.encode(), node_id.encode()
    return grpc(b"\x0a" + bytes([len(t)]) + t + b"\x12" + bytes([len(n)]) + n)


def decode(text):
    raw = base64.b64decode(text + "===") if text else b""
    frames, i = [], 0
    while i + 5 <= len(raw):
        ft = raw[i]
        ln = int.from_bytes(raw[i+1:i+5], "big")
        frames.append((ft, raw[i+5:i+5+ln]))
        i += 5 + ln
    return frames


def find_uuid(data):
    s = data.decode(errors="ignore")
    m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", s, re.I)
    return m.group(0) if m else None


def post_auth(path, body):
    s = load_session()
    r = s.post(BASE + path, headers=HEADERS_AUTH, data=body, verify=False, timeout=15)
    return r, decode(r.text)


def post_omit(path, body, headers=None):
    h = HEADERS_OMIT.copy()
    if headers:
        h.update(headers)
    r = requests.post(BASE + path, headers=h, data=body, verify=False, timeout=15)
    return r, decode(r.text)


def register_source():
    r, frames = post_auth("/kuka.operationmanagement.spocservice.v1.OperationSourceRegistryService/RegisterOperationSource", body_empty())
    for _, f in frames:
        uid = find_uuid(f)
        if uid:
            print("operation_source_id:", uid)
            return uid
    raise RuntimeError("No operation source ID returned")


def observer(name, path, op_id, stop):
    s = load_session()
    try:
        with s.post(BASE + path, headers=HEADERS_AUTH, data=body_uuid(op_id), stream=True, timeout=(5, None)) as r:
            print(f"{name}: connected HTTP {r.status_code}")
            for chunk in r.iter_content(1024, decode_unicode=True):
                if stop.is_set():
                    break
                if chunk:
                    print(f"{name}: {chunk[:80]!r}")
    except Exception as e:
        print(f"{name}: {e}")


def start_observers(op_id):
    stop = threading.Event()
    paths = [
        ("ObserveRequestedOperation", "/kuka.operationmanagement.spocservice.v1.OperationSourceRegistryService/ObserveRequestedOperation"),
        ("ObservePermissionChange", "/kuka.operationmanagement.spocservice.v1.SpocValidationService/ObservePermissionChange"),
    ]
    for name, path in paths:
        threading.Thread(target=observer, args=(name, path, op_id, stop), daemon=True).start()
    time.sleep(4)
    return stop


def request_spoc(op_id):
    r, _ = post_auth("/kuka.operationmanagement.spocservice.v1.SpocValidationService/RequestPermission", body_uuid(op_id))
    print("RequestPermission HTTP:", r.status_code, "preview:", r.text[:120])


def get_tree_id():
    r, frames = post_omit("/kuka.easyprogramming.programtree.v1.ProgramTreeService/GetLoadedPrograms", body_empty())
    print("GetLoadedPrograms public HTTP:", r.status_code)
    for _, f in frames:
        uid = find_uuid(f)
        if uid:
            print("tree_id:", uid)
            return uid
    raise RuntimeError("No tree_id found")


def parse_nodes(frames):
    nodes = []
    for ft, f in frames:
        if ft != 0:
            continue
        text = f.decode(errors="ignore")
        for m in re.finditer(r'\{.*?\}', text):
            try:
                nodes.append(json.loads(m.group(0)))
            except Exception:
                pass
    return nodes


def select_program(tree_id):
    post_auth("/kuka.easyprogramming.programtree.v1.ProgramTreeService/LoadProgramTree", body_uuid(tree_id))
    r, frames = post_auth("/kuka.easyprogramming.programtree.v1.ProgramTreeService/GetNodesAsJson", body_uuid(tree_id))
    nodes = parse_nodes(frames)

    seqs = [n for n in nodes if n.get("__st") == "TtSequenceNode"]
    print("Sequences:", [(s.get("name"), s.get("id")) for s in seqs])

    seq = seqs[0]["id"]
    r, _ = post_auth("/kuka.easyprogramming.programtree.v1.ProgramTreeService/GetNodes", body_select(tree_id, seq))
    print("Select program HTTP:", r.status_code)
    return seq


def resume_safety_controller():
    print("\nResuming Safety Controller...")
    r, _ = post_auth("/kuka.safetyservices.safetycontrollerresume.v1.SafetyControllerResumeService/ResumeSafetyController", body_empty())
    print(f"ResumeSafetyController HTTP: {r.status_code}")


def execute_command(action, op_id, tree_id):
    """Handles Start, Pause, Resume, Stop commands"""
    print(f"\nSending {action} command...")
    path = f"/kuka.executioncontroller.v1.ExecutionControllerService/{action}"
    
    # Needs to be passed as an extra header per the 'omit' credentials mode in the fetch requests
    headers = {
        "kuka-operation-source-id": op_id
    }
    
    # The body string translates directly to the UUID of the program tree
    r, _ = post_omit(path, body_uuid(tree_id), headers=headers)
    print(f"{action} HTTP: {r.status_code}")


def main():
    op_id = register_source()
    stop = start_observers(op_id)
    request_spoc(op_id)

    tree_id = get_tree_id()
    seq_id = select_program(tree_id)

    print("\n" + "="*40)
    print("SESSION INITIALIZED")
    print("operation_source_id (SPOC):", op_id)
    print("tree_id (Program):", tree_id)
    print("sequence_id (Selected):", seq_id)
    print("="*40)

    # Make sure safety controller is resumed before trying to interact with execution controller
    resume_safety_controller()

    # Interactive Command Loop
    while True:
        try:
            cmd = input("\nEnter command (start/pause/resume/stop) or 'quit' to exit: ").strip().lower()
            
            if cmd == 'quit':
                break
            elif cmd in ['start', 'pause', 'resume', 'stop']:
                execute_command(cmd.capitalize(), op_id, tree_id)
            elif cmd == 'safety':
                resume_safety_controller()
            else:
                print("Invalid command. Available options: start, pause, resume, stop, safety, quit")
        except KeyboardInterrupt:
            break

    print("\nStopping observers and exiting...")
    stop.set()


if __name__ == "__main__":
    main()
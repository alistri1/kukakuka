import base64
import pickle
import threading
import time
import urllib.parse
import requests
import urllib3
from pathlib import Path

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = "https://192.168.43.1"
COOKIES_FILE = Path("session_cookies.pkl")
ACTIVE_UUID_FILE = Path("active_spoc_uuid.txt")

COMMON_GRPC_HEADERS = {
    "accept": "application/grpc-web-text",
    "accept-language": "en-GB,en-US;q=0.9,en;q=0.8",
    "content-type": "application/grpc-web-text",
    "origin": BASE,
    "referer": f"{BASE}/Scene/3D_Visualization/Parameters",
    "user-agent": "Mozilla/5.0",
    "x-grpc-web": "1",
    "x-user-agent": "grpc-web-javascript/0.1",
}

# observer state for current script run
observer_stop_event = None
observer_threads = []
observer_operation_source_id = None


def load_saved_session() -> requests.Session:
    session = requests.Session()
    session.verify = False

    if not COOKIES_FILE.exists():
        raise FileNotFoundError(f"Session cookie file not found: {COOKIES_FILE}")

    with open(COOKIES_FILE, "rb") as f:
        session.cookies.update(pickle.load(f))

    return session


def save_active_uuid(operation_source_id: str) -> None:
    ACTIVE_UUID_FILE.write_text(operation_source_id, encoding="utf-8")


def load_active_uuid() -> str:
    if not ACTIVE_UUID_FILE.exists():
        raise FileNotFoundError(
            f"No active operation source ID found. Register first. Missing file: {ACTIVE_UUID_FILE}"
        )
    return ACTIVE_UUID_FILE.read_text(encoding="utf-8").strip()


def clear_active_uuid() -> None:
    if ACTIVE_UUID_FILE.exists():
        ACTIVE_UUID_FILE.unlink()


def build_empty_grpc_web_text_body() -> str:
    return base64.b64encode(b"\x00\x00\x00\x00\x00").decode("ascii")


def build_uuid_grpc_web_text_body(uuid_str: str) -> str:
    uuid_bytes = uuid_str.encode("utf-8")
    protobuf_payload = bytes([0x0A, len(uuid_bytes)]) + uuid_bytes
    grpc_frame = bytes([0x00]) + len(protobuf_payload).to_bytes(4, "big") + protobuf_payload
    return base64.b64encode(grpc_frame).decode("ascii")


def decode_grpc_web_text_response(response_text: str) -> dict:
    result = {
        "ok_base64": False,
        "raw_bytes": b"",
        "data_frames": [],
        "trailers": None,
        "grpc_status": None,
        "grpc_message": None,
        "grpc_message_decoded": None,
        "parse_error": None,
    }

    try:
        chunks = []
        remaining = response_text.strip()

        while remaining:
            found = False
            for i in range(4, len(remaining) + 1, 4):
                part = remaining[:i]
                try:
                    decoded_part = base64.b64decode(part, validate=True)
                    chunks.append(decoded_part)
                    remaining = remaining[i:]
                    found = True
                    break
                except Exception:
                    continue

            if not found:
                raise ValueError("Could not split concatenated base64 grpc-web-text response")

        raw = b"".join(chunks)
        result["ok_base64"] = True
        result["raw_bytes"] = raw

    except Exception as e:
        result["parse_error"] = str(e)
        return result

    i = 0
    while i + 5 <= len(raw):
        frame_type = raw[i]
        frame_len = int.from_bytes(raw[i + 1:i + 5], "big")
        frame_end = i + 5 + frame_len

        if frame_end > len(raw):
            result["parse_error"] = "Truncated gRPC-Web frame"
            break

        frame_data = raw[i + 5:frame_end]

        if frame_type == 0x00:
            result["data_frames"].append(frame_data)
        elif frame_type == 0x80:
            trailers_text = frame_data.decode("utf-8", errors="replace")
            result["trailers"] = trailers_text

            for line in trailers_text.split("\r\n"):
                lower = line.lower()
                if lower.startswith("grpc-status:"):
                    result["grpc_status"] = line.split(":", 1)[1].strip()
                elif lower.startswith("grpc-message:"):
                    msg = line.split(":", 1)[1].strip()
                    result["grpc_message"] = msg
                    result["grpc_message_decoded"] = urllib.parse.unquote(msg)

        i = frame_end

    return result


def extract_uuid_from_frame(frame: bytes) -> str | None:
    if len(frame) >= 38 and frame[0] == 0x0A and frame[1] == 0x24:
        try:
            return frame[2:38].decode("utf-8")
        except Exception:
            return None
    return None


def validate_grpc_result(response, decoded: dict, action_name: str) -> dict:
    reasons = [f"HTTP {response.status_code}"]

    if decoded["grpc_status"] == "0":
        success = True
        reasons.append("grpc-status=0")
    elif decoded["grpc_status"] is not None:
        success = False
        reasons.append(f"grpc-status={decoded['grpc_status']}")
    elif response.status_code == 200 and decoded["ok_base64"]:
        success = False
        reasons.append("response decoded, but grpc-status was not parsed")
    else:
        success = False
        reasons.append("grpc-status trailer not found")

    return {
        "action": action_name,
        "success": success,
        "reasons": reasons,
        "grpc_status": decoded["grpc_status"],
        "grpc_message": decoded["grpc_message"],
        "grpc_message_decoded": decoded["grpc_message_decoded"],
    }


def call_grpc_web(session: requests.Session, url: str, body: str, timeout: int = 15):
    response = session.post(url, headers=COMMON_GRPC_HEADERS, data=body, timeout=timeout)
    decoded = decode_grpc_web_text_response(response.text)
    return response, decoded


def register_operation_source():
    session = load_saved_session()
    url = f"{BASE}/kuka.operationmanagement.spocservice.v1.OperationSourceRegistryService/RegisterOperationSource"
    body = build_empty_grpc_web_text_body()

    response, decoded = call_grpc_web(session, url, body)
    validation = validate_grpc_result(response, decoded, "RegisterOperationSource")

    operation_source_id = None
    for frame in decoded["data_frames"]:
        operation_source_id = extract_uuid_from_frame(frame)
        if operation_source_id:
            break

    if validation["success"] and operation_source_id:
        save_active_uuid(operation_source_id)

    return response, decoded, validation, operation_source_id, body


def call_spoc(method_name: str, operation_source_id: str):
    session = load_saved_session()
    url = f"{BASE}/kuka.operationmanagement.spocservice.v1.SpocValidationService/{method_name}"
    body = build_uuid_grpc_web_text_body(operation_source_id)

    response, decoded = call_grpc_web(session, url, body)
    validation = validate_grpc_result(response, decoded, method_name)

    return response, decoded, validation, body


def request_spoc_permission():
    operation_source_id = load_active_uuid()
    response, decoded, validation, body = call_spoc("RequestPermission", operation_source_id)
    return response, decoded, validation, operation_source_id, body


def release_spoc_permission():
    operation_source_id = load_active_uuid()
    response, decoded, validation, body = call_spoc("ReleasePermission", operation_source_id)

    if validation["success"]:
        clear_active_uuid()

    return response, decoded, validation, operation_source_id, body


def observe_stream_worker(name: str, url: str, body: str, stop_event: threading.Event):
    session = load_saved_session()

    try:
        with session.post(
            url,
            headers=COMMON_GRPC_HEADERS,
            data=body,
            timeout=(5, None),
            stream=True,
        ) as response:
            print(f"\n[{name}] connected: HTTP {response.status_code}")

            for chunk in response.iter_content(chunk_size=1024, decode_unicode=True):
                if stop_event.is_set():
                    print(f"[{name}] stop requested")
                    break

                if not chunk:
                    continue

                print(f"[{name}] chunk: {chunk[:200]!r}")

    except Exception as e:
        print(f"[{name}] ERROR: {e}")


def start_observers(operation_source_id: str):
    global observer_stop_event, observer_threads, observer_operation_source_id

    if observer_stop_event is not None:
        print("\nObservers are already running.")
        return observer_stop_event, observer_threads

    stop_event = threading.Event()

    observe_requested_url = (
        f"{BASE}/kuka.operationmanagement.spocservice.v1.OperationSourceRegistryService/ObserveRequestedOperation"
    )
    observe_permission_url = (
        f"{BASE}/kuka.operationmanagement.spocservice.v1.SpocValidationService/ObservePermissionChange"
    )

    body = build_uuid_grpc_web_text_body(operation_source_id)

    t1 = threading.Thread(
        target=observe_stream_worker,
        args=("ObserveRequestedOperation", observe_requested_url, body, stop_event),
        daemon=True,
    )
    t2 = threading.Thread(
        target=observe_stream_worker,
        args=("ObservePermissionChange", observe_permission_url, body, stop_event),
        daemon=True,
    )

    t1.start()
    t2.start()

    observer_stop_event = stop_event
    observer_threads = [t1, t2]
    observer_operation_source_id = operation_source_id

    return stop_event, observer_threads


def stop_observers():
    global observer_stop_event, observer_threads, observer_operation_source_id

    if observer_stop_event is None:
        print("\nNo observers are currently running.")
        return

    observer_stop_event.set()
    time.sleep(1)

    observer_stop_event = None
    observer_threads = []
    observer_operation_source_id = None
    print("\nObservers stopped.")


def print_result(response, decoded, validation, request_body: str, operation_source_id: str | None = None):
    print(f"\n=== {validation['action']} ===")

    if operation_source_id:
        print("Operation source ID:", operation_source_id)

    print("Encoded request body:", request_body)
    print("HTTP status:", response.status_code)
    print("Success:", validation["success"])
    print("Validation:", " | ".join(validation["reasons"]))

    if validation["grpc_status"] is not None:
        print("gRPC status:", validation["grpc_status"])

    if validation["grpc_message"]:
        print("gRPC message:", validation["grpc_message"])

    if validation["grpc_message_decoded"]:
        print("gRPC message decoded:", validation["grpc_message_decoded"])

    if decoded["parse_error"]:
        print("Parse error:", decoded["parse_error"])

    print("\nResponse headers:")
    for k, v in response.headers.items():
        print(f"  {k}: {v}")

    print("\nResponse text preview:")
    print(response.text[:500])

    if decoded["trailers"]:
        print("\nDecoded trailers:")
        print(decoded["trailers"])

    if decoded["data_frames"]:
        print("\nDecoded data frames:")
        for idx, frame in enumerate(decoded["data_frames"], start=1):
            if len(frame) == 0:
                print(f"  Frame {idx}: <empty>")
            else:
                print(f"  Frame {idx} hex: {frame.hex()}")
                print(f"  Frame {idx} raw: {frame}")


def do_register():
    resp, dec, val, op_id, body = register_operation_source()
    print_result(resp, dec, val, body, op_id)


def do_start_observers():
    operation_source_id = load_active_uuid()
    start_observers(operation_source_id)
    print(f"\nObservers started for: {operation_source_id}")


def do_request():
    global observer_operation_source_id

    operation_source_id = load_active_uuid()

    if observer_stop_event is None:
        print("\nObservers are not running. Starting them automatically...")
        start_observers(operation_source_id)
        time.sleep(2)

    elif observer_operation_source_id != operation_source_id:
        print("\nObservers are running for a different operation source. Restarting them...")
        stop_observers()
        start_observers(operation_source_id)
        time.sleep(2)

    resp, dec, val, op_id, body = request_spoc_permission()
    print_result(resp, dec, val, body, op_id)


def do_release():
    if observer_stop_event is None:
        print("\nWarning: observers are not running. Release may fail.")

    resp, dec, val, op_id, body = release_spoc_permission()
    print_result(resp, dec, val, body, op_id)


def observer_flow():
    print("\n[1/4] Registering operation source...")
    reg_resp, reg_dec, reg_val, op_id, reg_body = register_operation_source()
    print_result(reg_resp, reg_dec, reg_val, reg_body, op_id)
    if not (reg_val["success"] and op_id):
        print("\nFlow stopped: registration failed.")
        return

    print("\n[2/4] Starting observers...")
    start_observers(op_id)
    time.sleep(2)

    print("\n[3/4] Requesting SPOC...")
    req_resp, req_dec, req_val, op_id, req_body = request_spoc_permission()
    print_result(req_resp, req_dec, req_val, req_body, op_id)

    time.sleep(2)

    print("\n[4/4] Releasing SPOC...")
    rel_resp, rel_dec, rel_val, op_id, rel_body = release_spoc_permission()
    print_result(rel_resp, rel_dec, rel_val, rel_body, op_id)

    stop_observers()


def main():
    while True:
        print("\nChoose action:")
        print("1. Register operation source")
        print("2. Start observers")
        print("3. Request SPOC")
        print("4. Release SPOC")
        print("5. Full flow (register -> observers -> request -> release)")
        print("6. Stop observers")
        print("7. Exit")

        choice = input("Enter choice: ").strip()

        try:
            if choice == "1":
                do_register()
            elif choice == "2":
                do_start_observers()
            elif choice == "3":
                do_request()
            elif choice == "4":
                do_release()
            elif choice == "5":
                observer_flow()
            elif choice == "6":
                stop_observers()
            elif choice == "7":
                stop_observers()
                print("\nBye.")
                break
            else:
                print("Invalid choice.")
        except Exception as e:
            print("\nERROR:", e)


if __name__ == "__main__":
    main()
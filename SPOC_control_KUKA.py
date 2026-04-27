import base64
import pickle
import requests
import urllib3
from pathlib import Path

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = "https://192.168.43.1"
COOKIES_FILE = Path("session_cookies.pkl")

REQUEST_UUID = "177a5f77-bdb3-4f37-9710-be1723736075"
RELEASE_UUID = "8d27ccec-8bcb-4dc3-bb90-6ea94fa4714a"

COMMON_GRPC_HEADERS = {
    "accept": "application/grpc-web-text",
    "content-type": "application/grpc-web-text",
    "x-grpc-web": "1",
    "x-user-agent": "grpc-web-javascript/0.1",
    "origin": BASE,
    "referer": f"{BASE}/Scene/3D_Visualization/Parameters",
    "user-agent": "Mozilla/5.0",
}


def load_saved_session():
    s = requests.Session()
    s.verify = False

    if not COOKIES_FILE.exists():
        raise FileNotFoundError(f"Session cookie file not found: {COOKIES_FILE}")

    with open(COOKIES_FILE, "rb") as f:
        s.cookies.update(pickle.load(f))

    return s


def build_grpc_web_text_body(uuid_str: str) -> str:
    """
    Build grpc-web-text request body for a protobuf message like:
        string field_1 = 1;

    Protobuf:
        0a <len> <uuid bytes>

    gRPC-Web frame:
        00 <4-byte big-endian length> <protobuf bytes>

    Encoded as base64 because content-type is grpc-web-text.
    """
    uuid_bytes = uuid_str.encode("utf-8")
    protobuf_payload = bytes([0x0A, len(uuid_bytes)]) + uuid_bytes
    grpc_frame = bytes([0x00]) + len(protobuf_payload).to_bytes(4, "big") + protobuf_payload
    return base64.b64encode(grpc_frame).decode("ascii")


def decode_grpc_web_text_response(response_text: str):
    """
    Decode a grpc-web-text response into raw frames.
    Returns a dict with parsed information.
    """
    result = {
        "ok_base64": False,
        "raw_bytes": b"",
        "data_frames": [],
        "trailers": None,
        "grpc_status": None,
        "grpc_message": None,
    }

    try:
        raw = base64.b64decode(response_text)
        result["ok_base64"] = True
        result["raw_bytes"] = raw
    except Exception:
        return result

    i = 0
    while i + 5 <= len(raw):
        frame_type = raw[i]
        frame_len = int.from_bytes(raw[i + 1:i + 5], "big")
        frame_data = raw[i + 5:i + 5 + frame_len]

        if len(frame_data) != frame_len:
            break

        if frame_type == 0x00:
            result["data_frames"].append(frame_data)
        elif frame_type == 0x80:
            try:
                trailers_text = frame_data.decode("utf-8", errors="replace")
            except Exception:
                trailers_text = repr(frame_data)

            result["trailers"] = trailers_text

            for line in trailers_text.split("\r\n"):
                lower = line.lower()
                if lower.startswith("grpc-status:"):
                    result["grpc_status"] = line.split(":", 1)[1].strip()
                elif lower.startswith("grpc-message:"):
                    result["grpc_message"] = line.split(":", 1)[1].strip()

        i += 5 + frame_len

    return result


def validate_grpc_result(response, decoded, action_name: str):
    """
    Best-effort validation.
    Success is usually:
    - HTTP 200
    - grpc-status: 0
    or
    - HTTP 200 with no grpc-status trailer but a decodable response
    """
    success = False
    reasons = []

    if response.status_code == 200:
        reasons.append("HTTP 200")
    else:
        reasons.append(f"HTTP {response.status_code}")

    if decoded["grpc_status"] == "0":
        success = True
        reasons.append("grpc-status=0")
    elif decoded["grpc_status"] is not None:
        reasons.append(f"grpc-status={decoded['grpc_status']}")
    elif response.status_code == 200 and decoded["ok_base64"]:
        # fallback when no trailers are visible
        success = True
        reasons.append("decodable grpc-web-text response")
    else:
        reasons.append("response could not be validated")

    return {
        "action": action_name,
        "success": success,
        "reasons": reasons,
        "grpc_status": decoded["grpc_status"],
        "grpc_message": decoded["grpc_message"],
    }


def call_spoc(method_name: str, uuid_str: str, send_cookies: bool):
    url = f"{BASE}/kuka.operationmanagement.spocservice.v1.SpocValidationService/{method_name}"
    body = build_grpc_web_text_body(uuid_str)
    headers = COMMON_GRPC_HEADERS.copy()

    if send_cookies:
        session = load_saved_session()
        response = session.post(url, headers=headers, data=body)
    else:
        response = requests.post(url, headers=headers, data=body, verify=False)

    decoded = decode_grpc_web_text_response(response.text)
    validation = validate_grpc_result(response, decoded, method_name)

    return response, decoded, validation


def request_spoc_permission():
    return call_spoc(
        method_name="RequestPermission",
        uuid_str=REQUEST_UUID,
        send_cookies=True,
    )


def release_spoc_permission():
    return call_spoc(
        method_name="ReleasePermission",
        uuid_str=RELEASE_UUID,
        send_cookies=True,
    )


def print_result(response, decoded, validation):
    print(f"\n=== {validation['action']} ===")
    print("HTTP status:", response.status_code)
    print("Success:", validation["success"])
    print("Validation:", " | ".join(validation["reasons"]))

    if validation["grpc_status"] is not None:
        print("gRPC status:", validation["grpc_status"])

    if validation["grpc_message"]:
        print("gRPC message:", validation["grpc_message"])

    print("Response headers:")
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
            print(f"  Frame {idx}: {frame.hex()}  |  raw={frame}")


def main():
    print("Choose action:")
    print("1. Request SPOC")
    print("2. Release SPOC")

    choice = input("Enter choice (1 or 2): ").strip()

    try:
        if choice == "1":
            response, decoded, validation = request_spoc_permission()
        elif choice == "2":
            response, decoded, validation = release_spoc_permission()
        else:
            print("Invalid choice.")
            return

        print_result(response, decoded, validation)

        if validation["success"]:
            if choice == "1":
                print("\nResult: SPOC was likely requested successfully.")
            else:
                print("\nResult: SPOC was likely released successfully.")
        else:
            if choice == "1":
                print("\nResult: Could not confirm SPOC request succeeded.")
            else:
                print("\nResult: Could not confirm SPOC release succeeded.")

    except Exception as e:
        print("\nERROR:", e)


if __name__ == "__main__":
    main()
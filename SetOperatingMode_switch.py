import base64
import pickle
import urllib.parse
import requests
import urllib3
from pathlib import Path

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = "https://192.168.43.1"
COOKIES_FILE = Path("session_cookies.pkl")

URL = f"{BASE}/kuka.safetyservices.safetyoperatingmode.v1.SafetyOperatingModeService/SetOperatingMode"

HEADERS = {
    "accept": "application/grpc-web-text",
    "content-type": "application/grpc-web-text",
    "origin": BASE,
    "referer": f"{BASE}/Scene/3D_Visualization/Parameters",
    "user-agent": "Mozilla/5.0",
    "x-grpc-web": "1",
    "x-user-agent": "grpc-web-javascript/0.1",
}


# ---------------- SESSION ----------------

def load_session():
    session = requests.Session()
    session.verify = False

    if not COOKIES_FILE.exists():
        raise FileNotFoundError("Missing session_cookies.pkl. Run login first.")

    with open(COOKIES_FILE, "rb") as f:
        session.cookies.update(pickle.load(f))

    return session


# ---------------- DECODER ----------------

def decode_grpc_web_text_response(response_text: str):
    result = {
        "grpc_status": None,
        "grpc_message": None,
        "grpc_message_decoded": None,
        "data_frames": [],
    }

    try:
        chunks = []
        remaining = response_text.strip()

        while remaining:
            for i in range(4, len(remaining) + 1, 4):
                part = remaining[:i]
                try:
                    chunks.append(base64.b64decode(part, validate=True))
                    remaining = remaining[i:]
                    break
                except:
                    continue

        raw = b"".join(chunks)

    except Exception as e:
        print("Decode error:", e)
        return result

    i = 0
    while i + 5 <= len(raw):
        frame_type = raw[i]
        frame_len = int.from_bytes(raw[i + 1:i + 5], "big")
        frame_data = raw[i + 5:i + 5 + frame_len]

        if frame_type == 0x00:
            result["data_frames"].append(frame_data)

        elif frame_type == 0x80:
            trailers = frame_data.decode("utf-8", errors="replace")
            for line in trailers.split("\r\n"):
                if line.lower().startswith("grpc-status:"):
                    result["grpc_status"] = line.split(":", 1)[1].strip()
                elif line.lower().startswith("grpc-message:"):
                    msg = line.split(":", 1)[1].strip()
                    result["grpc_message"] = msg
                    result["grpc_message_decoded"] = urllib.parse.unquote(msg)

        i += 5 + frame_len

    return result


# ---------------- CORE ----------------

def set_mode(mode: str):
    session = load_session()

    if mode.upper() == "T1":
        body = "AAAAAAQKAggB"
    elif mode.upper() == "AUT":
        body = "AAAAAAQKAggD"
    else:
        raise ValueError("Mode must be T1 or AUT")

    print(f"\nSetting mode: {mode}")

    response = session.post(URL, headers=HEADERS, data=body, timeout=10)

    decoded = decode_grpc_web_text_response(response.text)

    print("\nHTTP:", response.status_code)

    if decoded["grpc_status"] == "0":
        print("SUCCESS")
    else:
        print("FAILED")

    if decoded["grpc_status"] is not None:
        print("gRPC status:", decoded["grpc_status"])

    if decoded["grpc_message"]:
        print("gRPC message:", decoded["grpc_message_decoded"])


# ---------------- MAIN ----------------

def main():
    print("Choose mode:")
    print("1. T1")
    print("2. AUT")

    choice = input("Enter choice: ").strip()

    try:
        if choice == "1":
            set_mode("T1")
        elif choice == "2":
            set_mode("AUT")
        else:
            print("Invalid choice")
    except Exception as e:
        print("ERROR:", e)


if __name__ == "__main__":
    main()
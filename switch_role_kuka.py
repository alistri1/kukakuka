import requests
import urllib3
import pickle
import json
from pathlib import Path

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = "https://192.168.43.1"
PASSWORD = "kukakuka"

SESSION_FILE = Path("session_state.pkl")
TOKEN_FILE = Path("session_tokens.json")

s = requests.Session()
s.verify = False

COMMON_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": BASE,
    "Referer": f"{BASE}/Scene/3D_Visualization/Parameters",
    "User-Agent": "Mozilla/5.0",
}

# ===== ROLE SELECTION =====
ROLES = {
    "1": {"label": "user", "username": "user"},
    "2": {"label": "guest", "username": "guest"},
    "3": {"label": "vip_guest", "username": "vip_guest"},
}


def choose_role():
    print("\nSelect role:")
    for key, role in ROLES.items():
        print(f"{key}. {role['label']}")

    choice = input("Enter choice: ").strip()

    if choice not in ROLES:
        raise ValueError("Invalid selection")

    selected = ROLES[choice]
    print(f"Selected role: {selected['label']}")
    return selected["username"]


# ===== LOGIN FLOW =====
def get_login_flow():
    res = s.get(f"{BASE}/auth/self-service/login/browser", headers=COMMON_HEADERS)
    res.raise_for_status()

    data = res.json()
    flow_id = data["id"]

    csrf_token = next(
        node["attributes"]["value"]
        for node in data["ui"]["nodes"]
        if node.get("attributes", {}).get("name") == "csrf_token"
    )

    return flow_id, csrf_token


def login(username):
    flow_id, csrf_token = get_login_flow()

    print(f"\n[DEBUG] Logging in as: {username}")

    payload = {
        "csrf_token": csrf_token,
        "identifier": username,
        "password": PASSWORD,
        "method": "password",
        "password_identifier": username,
    }

    res = s.post(
        f"{BASE}/auth/self-service/login",
        params={"flow": flow_id},
        json=payload,
        headers={**COMMON_HEADERS, "Content-Type": "application/json"},
    )

    print("Login status:", res.status_code)
    print("Login response:", res.text[:500])

    res.raise_for_status()

    data = res.json()
    session_info = data.get("session", {})

    if session_info.get("active"):
        print("✓ Login successful")
    else:
        raise RuntimeError("Login failed")

    return session_info


# ===== GET LOGOUT TOKEN =====
def get_logout_token():
    res = s.get(f"{BASE}/auth/self-service/logout/browser", headers=COMMON_HEADERS)

    print("\nLogout-browser status:", res.status_code)
    res.raise_for_status()

    data = res.json()
    logout_token = data.get("logout_token")

    if not logout_token:
        raise RuntimeError("No logout token returned")

    print("Logout token:", logout_token)
    return logout_token


# ===== SAVE SESSION + TOKENS =====
def save_state(logout_token):
    data = {
        "logout_token": logout_token,
        "cookies": s.cookies  # full cookie jar
    }

    with open("session_cookies.pkl", "wb") as f:
        pickle.dump(data, f)

    print("\n✓ Session + logout token saved to session_state.pkl")


# ===== MAIN =====
if __name__ == "__main__":
    try:
        print("=== SWITCH USER ===")

        username = choose_role()

        print("\n=== LOGIN ===")
        login(username)

        print("\n=== GET LOGOUT TOKEN ===")
        logout_token = get_logout_token()

        print("\n=== SAVE SESSION ===")
        save_state(logout_token)

        print("\n✓ Done. You can now run this again to switch user.")

    except Exception as e:
        print("\nERROR:", e)
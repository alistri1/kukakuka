import requests
import urllib3
import pickle
from pathlib import Path

COOKIES_FILE = Path("session_cookies.pkl")

def save_session():
    """Save session cookies to file for later use."""
    with open(COOKIES_FILE, "wb") as f:
        pickle.dump(s.cookies, f)
    print(f"\nSession saved to {COOKIES_FILE}")


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROLES = {
    "1": {"label": "user", "username": "user"},
    "2": {"label": "safetycommissioningengineer", "username": "safetycommissioningengineer"},
    "3": {"label": "admin", "username": "admin"},
}
BASE = "https://192.168.43.1"
USERNAME = None
PASSWORD = "kukakuka"

s = requests.Session()
s.verify = False

COMMON_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": BASE,
    "Referer": f"{BASE}/Scene/3D_Visualization/Parameters",
    "User-Agent": "Mozilla/5.0",
}

def choose_role():
    print("Select role:")
    for key, role in ROLES.items():
        print(f"{key}. {role['label']}")

    choice = input("Enter choice: ").strip()

    if choice not in ROLES:
        raise ValueError("Invalid selection")

    selected = ROLES[choice]
    print(f"Selected role: {selected['label']}")
    return selected

def get_login_flow():
    url = f"{BASE}/auth/self-service/login/browser"

    res = s.get(url, headers=COMMON_HEADERS, allow_redirects=True)
    print("GET /login/browser status:", res.status_code)
    print("GET /login/browser headers:", dict(res.headers))
    print("GET /login/browser text:", res.text[:500])

    res.raise_for_status()
    data = res.json()

    flow_id = data.get("id")
    if not flow_id:
        raise RuntimeError("Flow ID not found")

    csrf_token = None
    for node in data.get("ui", {}).get("nodes", []):
        attrs = node.get("attributes", {})
        if attrs.get("name") == "csrf_token":
            csrf_token = attrs.get("value")
            break

    if not csrf_token:
        raise RuntimeError("CSRF token not found")

    return flow_id, csrf_token


def login(flow_id, csrf_token, username):
    url = f"{BASE}/auth/self-service/login"

    payload = {
        "csrf_token": csrf_token,
        "identifier": username,
        "password": PASSWORD,
        "method": "password",
        "password_identifier": username,
    }

    headers = COMMON_HEADERS.copy()
    headers["Content-Type"] = "application/json"

    res = s.post(
        url,
        params={"flow": flow_id},
        json=payload,
        headers=headers,
        allow_redirects=True,
    )

    print("\nPOST /login status:", res.status_code)
    print("POST /login headers:", dict(res.headers))
    print("POST /login text:", res.text[:1000])

    res.raise_for_status()

    try:
        data = res.json()
        session_info = data.get("session", {})
        
        identity = session_info.get("identity", {})
        traits = identity.get("traits", {})
        print("\n[DEBUG] Logged in as:", traits.get("name"))
        print("[DEBUG] Email:", traits.get("email"))
        if session_info.get("active") is True:
            print("\nLogin successful: session is active")
        else:
            print("\nLogin response received, but session.active was not true")
    except Exception:
        print("\nLogin returned non-JSON response")

    return res


def print_cookies():
    print("\nSession cookies:")
    for cookie in s.cookies:
        print(f"  {cookie.name} = {cookie.value}")


def check_with_logout_browser():
    url = f"{BASE}/auth/self-service/logout/browser"

    res = s.get(url, headers=COMMON_HEADERS, allow_redirects=True)

    print("\nGET /logout/browser status:", res.status_code)
    print("GET /logout/browser headers:", dict(res.headers))
    print("GET /logout/browser text:", res.text[:1000])

    res.raise_for_status()

    data = res.json()
    logout_token = data.get("logout_token")

    if logout_token:
        print("\nLogout check successful")
        print("Logout token:", logout_token)
        print("This confirms the Python session is authenticated.")
    else:
        print("\nNo logout token found. Session may not be authenticated.")

    return logout_token


def check_root_page():
    url = f"{BASE}/"
    res = s.get(url, headers=COMMON_HEADERS, allow_redirects=True)

    print("\nGET / status:", res.status_code)
    print("GET / final URL:", res.url)
    print("GET / text preview:", res.text[:500])

    return res


if __name__ == "__main__":
    try:
        print("=== SELECT ROLE ===")
        selected_username = choose_role()
        username = selected_username["username"]
        print(f"\n[DEBUG] Selected role username: {selected_username}")

        print("=== STEP 1: GET LOGIN FLOW ===")
        flow_id, csrf_token = get_login_flow()
        print("Flow ID:", flow_id)
        print("CSRF token:", csrf_token)

        print("\n=== STEP 2: LOGIN ===")
        login(flow_id, csrf_token, username)

        print("\n=== STEP 3: PRINT COOKIES ===")
        print_cookies()

        print("\n=== STEP 4: CHECK ROOT PAGE ===")
        check_root_page()

        print("\n=== STEP 5: LOGOUT-BROWSER CHECK ===")
        check_with_logout_browser()

        print("\n=== STEP 6: SAVE SESSION ===")
        save_session()

    except Exception as e:
        print("\nERROR:", e)
import requests
import urllib3
import pickle
from pathlib import Path

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = "https://192.168.43.1"
COOKIES_FILE = Path("session_cookies.pkl")

COMMON_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": BASE,
    "Referer": f"{BASE}/Scene/3D_Visualization/Parameters",
    "User-Agent": "Mozilla/5.0",
}


def load_session():
    """Load saved session cookies from file."""
    s = requests.Session()
    s.verify = False
    
    if not COOKIES_FILE.exists():
        raise RuntimeError(f"No saved session found at {COOKIES_FILE}. Please login first.")
    
    with open(COOKIES_FILE, "rb") as f:
        s.cookies.update(pickle.load(f))
    
    print("Loaded session cookies:")
    for cookie in s.cookies:
        print(f"  {cookie.name} = {cookie.value[:20]}...")
    
    return s


def get_logout_token(session):
    """Step 1: Get logout token from /logout/browser."""
    url = f"{BASE}/auth/self-service/logout/browser"
    res = session.get(url, headers=COMMON_HEADERS, allow_redirects=True)
    
    print(f"\nGET /logout/browser status: {res.status_code}")
    
    if res.status_code == 401:
        raise RuntimeError("Session is not authenticated or has expired.")
    
    res.raise_for_status()
    data = res.json()
    
    logout_token = data.get("logout_token")
    logout_url = data.get("logout_url")
    
    if not logout_token:
        raise RuntimeError(f"No logout token in response: {data}")
    
    print(f"Logout token: {logout_token}")
    if logout_url:
        print(f"Logout URL: {logout_url}")
    
    return logout_token


def execute_logout(session, logout_token):
    """Step 2: Execute logout with the token."""
    url = f"{BASE}/auth/self-service/logout"
    res = session.get(
        url, 
        params={"token": logout_token}, 
        headers=COMMON_HEADERS, 
        allow_redirects=True
    )
    
    print(f"\nGET /logout?token=... status: {res.status_code}")
    print(f"Final URL: {res.url}")
    
    # Successful logout typically returns 200 or redirects
    if res.status_code in [200, 204]:
        print("\n✓ Logout successful!")
    elif res.status_code == 303 or "login" in res.url:
        print("\n✓ Logout successful! (redirected to login)")
    else:
        print(f"\nLogout response: {res.text[:500]}")
    
    return res


def verify_logged_out(session):
    """Verify the session is no longer authenticated."""
    url = f"{BASE}/auth/self-service/logout/browser"
    res = session.get(url, headers=COMMON_HEADERS, allow_redirects=True)
    
    print(f"\nVerification - GET /logout/browser status: {res.status_code}")
    
    if res.status_code == 401:
        print("✓ Confirmed: session is logged out")
        return True
    else:
        print("⚠ Session may still be active")
        return False


def cleanup_saved_session():
    """Remove the saved session file after logout."""
    if COOKIES_FILE.exists():
        COOKIES_FILE.unlink()
        print(f"\nDeleted saved session file: {COOKIES_FILE}")




def logout():
    """Main logout function."""
    print("=== LOADING SAVED SESSION ===")
    session = load_session()
    
    print("\n=== STEP 1: GET LOGOUT TOKEN ===")
    logout_token = get_logout_token(session)
    
    print("\n=== STEP 2: EXECUTE LOGOUT ===")
    execute_logout(session, logout_token)
    
    print("\n=== STEP 3: VERIFY LOGOUT ===")
    verify_logged_out(session)
    
    print("\n=== STEP 4: CLEANUP ===")
    cleanup_saved_session()


if __name__ == "__main__":
    try:
        logout()
    except Exception as e:
        print(f"\nERROR: {e}")

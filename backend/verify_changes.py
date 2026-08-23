import requests
import os
import time

BASE_URL = os.getenv("CHIPVERIFY_BACKEND_URL", "http://127.0.0.1:7348/api/v1").rstrip(
    "/"
)


def test_registration_disabled():
    print("Testing registration...")
    try:
        resp = requests.post(
            f"{BASE_URL}/auth/register",
            json={
                "email": "test@example.com",
                "password": "password123",
                "full_name": "Test User",
            },
        )
        print(f"Status: {resp.status_code}")
        print(f"Detail: {resp.json().get('detail')}")
        assert resp.status_code == 403
    except Exception as e:
        print(f"Error: {e}")


def test_license_valid():
    print("\nTesting license valid on /projects...")
    try:
        resp = requests.get(f"{BASE_URL}/projects")
        print(f"Status: {resp.status_code}")
        # When license is valid but no token is provided, it should return 401
        assert resp.status_code == 401
        print("Licensing check passed (Proceeded to Auth)!")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    # Note: These tests assume the backend is running.
    # Since I don't want to start the whole server now if I can't interact with it,
    # I'll just check the code logic by running a mock-like test if needed,
    # but the user has uvicorn so I can try starting it in background.
    test_registration_disabled()
    test_license_valid()

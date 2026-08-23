import os
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from jose import jwt

# This script should be run with the same environment variables as the backend
# (specifically CHIPVERIFY_SECRET_KEY)

def generate_license(org_id: str, days: int, output_path: str):
    secret_key = os.environ.get("CHIPVERIFY_SECRET_KEY")
    if not secret_key:
        print("Error: CHIPVERIFY_SECRET_KEY environment variable not set.")
        return

    expiry = datetime.now(timezone.utc) + timedelta(days=days)
    payload = {
        "org_id": org_id,
        "exp": int(expiry.timestamp()),
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "iss": "chipverify-vendor",
    }

    token = jwt.encode(payload, secret_key, algorithm="HS256")
    
    with open(output_path, "w") as f:
        f.write(token)
    
    print(f"License generated successfully!")
    print(f"Organization ID: {org_id}")
    print(f"Expires on: {expiry.isoformat()}")
    print(f"Saved to: {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a ChipVerify local license.")
    parser.add_argument("--org_id", type=str, required=True, help="Organization ID")
    parser.add_argument("--days", type=int, default=365, help="License duration in days (default: 365)")
    parser.add_argument("--out", type=str, default="license.key", help="Output file path (default: license.key)")

    args = parser.parse_args()
    generate_license(args.org_id, args.days, args.out)

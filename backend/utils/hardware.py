import uuid
import hashlib
from pathlib import Path

# Persistent file to store the ID. Critical for Docker deployments where MAC changes.
SYS_ID_PATH = Path(".sys_id")

def get_machine_fingerprint() -> str:
    """
    Generates or retrieves a consistent machine fingerprint.
    Prefers a persistent .sys_id file to survive container restarts.
    Builds a secure hash based on physical hardware (MAC address) if no file exists.
    """
    if SYS_ID_PATH.exists():
        try:
            with open(SYS_ID_PATH, "r") as f:
                fingerprint = f.read().strip()
                if fingerprint:
                    return fingerprint
        except Exception:
            pass
            
    # Combine MAC address and a generated UUID for a strong unique string
    node = uuid.getnode()
    mac = ':'.join(['{:02x}'.format((node >> elements) & 0xff) for elements in range(0,2*6,2)][::-1])
    
    raw_id = f"{mac}-{uuid.uuid4()}"
    fingerprint = hashlib.sha256(raw_id.encode()).hexdigest()[:16]
    
    try:
        with open(SYS_ID_PATH, "w") as f:
            f.write(fingerprint)
    except Exception as e:
        print(f"Warning: Could not save .sys_id: {e}")
        
    return fingerprint

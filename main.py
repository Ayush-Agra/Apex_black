import os
import httpx
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import firebase_admin
from firebase_admin import credentials, firestore

# Initialize FastAPI App
app = FastAPI(
    title="ApexBlack Waitlist Backend API",
    description="Secure, server-side processing for ApexBlack waitlist allocations.",
    version="1.0.0"
)

# Enable CORS to allow your static front-end (hosted on Netlify or GitHub) to securely make calls here
# Replace '*' with your specific Netlify/GitHub domain in production for tightened security
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Define Custom Constants
APP_ID_PATH = "apex-black-pipeline-db"

# ==============================================================================
# 🔐 INITIALIZE FIREBASE ADMIN SDK SECURELY
# ==============================================================================
# Professional Practice: Instead of hardcoding keys in plain text files, the Admin
# SDK is loaded via a JSON key file or environment variable.
# Step 1: Go to your Firebase Console -> Project Settings -> Service Accounts.
# Step 2: Click "Generate new private key".
# Step 3: Save the file as "firebase-adminsdk-credentials.json" in this directory,
#         or set the FIREBASE_CREDENTIALS_JSON environment variable.
# ==============================================================================

firebase_creds_path = os.environ.get("FIREBASE_CREDENTIALS_JSON", "firebase-adminsdk-credentials.json")

if not firebase_admin._apps:
    try:
        if os.path.exists(firebase_creds_path):
            cred = credentials.Certificate(firebase_creds_path)
            firebase_admin.initialize_app(cred)
            print("✓ Firebase Admin SDK initialized successfully via local service account.")
        else:
            print(f"❌ CRITICAL ERROR: Cannot find file at {firebase_creds_path}")
    except ValueError:
        # Ignore the reloader's duplicate initialization warning
        pass
    except Exception as e:
        print(f"⚠️ Initialization Error: {e}")

# 2. Connect to the database independently 
try:
    db = firestore.client()
    print("✓ Firestore Database Connected!")
except Exception as e:
    print(f"⚠️ Database connection failed: {e}")
    print("Backend will run in Local/Sandbox fallback mode.")
    db = None

# ==============================================================================
# Pydantic Schemas
# ==============================================================================
class WaitlistRequest(BaseModel):
    contact: str = Field(..., description="Email or phone number of the target requester", min_length=3)

# ==============================================================================
# Helper Geolocation Methods (Server-Side Client Tracking)
# ==============================================================================
async def gather_client_geolocation(ip_address: str) -> dict:
    """
    Uses server-side HTTP calls to gather robust location data relative to the Client's IP.
    By doing this on the server, we bypass browser permission prompts and faked headers.
    """
    default_location = {"city": "Unknown", "region": "Unknown", "country": "Unknown", "ip": ip_address}
    
    # Ignore local/private IP addresses
    if ip_address in ["127.0.0.1", "localhost", "::1"] or ip_address.startswith("192.168."):
        return default_location

    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            response = await client.get(f"https://ipapi.co/{ip_address}/json/")
            if response.status_code == 200:
                data = response.json()
                print(data)
                return {
                    "city": data.get("city", "Unknown"),
                    "region": data.get("region", "Unknown"),
                    "country": data.get("country_name", "Unknown"),
                    "ip": ip_address
                }
    except Exception as e:
        print(f"⚠️ Server Geolocation Lookup failed: {e}")
    
    return default_location

# ==============================================================================
# API Endpoints
# ==============================================================================
@app.post("/api/waitlist", status_code=status.HTTP_201_CREATED)
async def register_waitlist(payload: WaitlistRequest, request: Request):
    # 1. Retrieve the Client's IP securely from standard HTTP forwarding headers
    client_ip = request.headers.get("x-forwarded-for")
    if client_ip:
        client_ip = client_ip.split(",")[0].strip()
    else:
        client_ip = request.client.host if request.client else "Unknown"

    # 2. Process Geolocation on server side
    geolocation = await gather_client_geolocation(client_ip)

    # 3. Create waitlist record payload
    waitlist_data = {
        "contact": payload.contact,
        "location": geolocation,
        "timestamp": firestore.SERVER_TIMESTAMP if db else firestore.client().field_path()
    }

    # 4. Write data securely to Firebase Firestore
    if db:
        try:
            # Matches exactly with standard path rules:
            # /artifacts/apex-black-pipeline-db/public/data/waitlist
            waitlist_ref = db.collection("artifacts").document(APP_ID_PATH).collection("public").document("data").collection("waitlist")
            
            # Replaced Server Timestamp with standard string format if server functions aren't fully resolved
            waitlist_data["timestamp"] = firestore.SERVER_TIMESTAMP
            
            # Fire and forget the document additions
            waitlist_ref.add(waitlist_data)
            return {"success": True, "message": "Priority spot secured.", "location": geolocation}
            
        except Exception as err:
            print(f"❌ Firestore Database Write Error: {err}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database transmission error. Please retry."
            )
    else:
        # Fallback print statement for local test development
        import datetime
        waitlist_data["timestamp"] = datetime.datetime.utcnow().isoformat()
        print(f"[SANDBOX FALLBACK WRITES] Local Lead Captured: {waitlist_data}")
        return {"success": True, "message": "Priority spot logged (Sandbox Mode).", "location": geolocation}

@app.get("/health")
def health_check():
    return {"status": "healthy", "database_connected": db is not None}

if __name__ == "__main__":
    import uvicorn
    # In development, you can run: python main.py
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

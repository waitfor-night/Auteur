#
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

BASE = Path(__file__).parent
CREDS = BASE / "secrets" / "credentials.json"
TOKEN = BASE / "secrets" / "token.json"

flow = InstalledAppFlow.from_client_secrets_file(str(CREDS), SCOPES)
creds = flow.run_local_server(port=0)

TOKEN.parent.mkdir(parents=True, exist_ok=True)
TOKEN.write_text(creds.to_json(), encoding="utf-8")
print(f"saved token to {TOKEN}")
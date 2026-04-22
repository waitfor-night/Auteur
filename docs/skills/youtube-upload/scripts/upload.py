from pathlib import Path
import argparse

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

BASE = Path(__file__).parent
TOKEN = BASE / "secrets" / "token.json"

def load_credentials():
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return creds

def upload_video(file_path, title, description="", tags=None, category_id="22", privacy_status="private"):
    creds = load_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
        },
    }

    if tags:
        body["snippet"]["tags"] = tags

    media = MediaFileUpload(file_path, chunksize=-1, resumable=True)

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"progress: {int(status.progress() * 100)}%")

    print("videoId =", response["id"])

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--file", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--description", default="")
    p.add_argument("--tags", default="")
    p.add_argument("--category", default="22")
    p.add_argument("--privacy", default="private")
    args = p.parse_args()

    tags = [x.strip() for x in args.tags.split(",") if x.strip()]
    upload_video(args.file, args.title, args.description, tags, args.category, args.privacy)
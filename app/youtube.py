import os
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
ROOT = Path(__file__).resolve().parent.parent

def youtube_client():
    token = ROOT / os.getenv("GOOGLE_TOKEN_FILE", "token.json")
    secrets = ROOT / os.getenv("GOOGLE_CLIENT_SECRETS_FILE", "client_secret.json")
    creds = None
    if token.exists(): creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if not creds or not creds.valid:
        if not secrets.exists(): raise RuntimeError("Google OAuth setup missing. Add client_secret.json from Google Cloud Console.")
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
        creds = flow.run_local_server(port=0)
        token.write_text(creds.to_json())
    return build("youtube", "v3", credentials=creds)

def upload_short(path: Path, title: str, description: str, privacy: str = "private"):
    yt = youtube_client()
    body = {"snippet": {"title": title[:100], "description": description[:5000], "categoryId": "22"},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
    request = yt.videos().insert(part="snippet,status", body=body,
        media_body=MediaFileUpload(str(path), mimetype="video/mp4", resumable=True))
    response = None
    while response is None:
        _, response = request.next_chunk()
    return {"video_id": response["id"], "url": f"https://www.youtube.com/shorts/{response['id']}"}

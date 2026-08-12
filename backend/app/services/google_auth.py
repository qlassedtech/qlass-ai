from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from app.config import settings

_request = google_requests.Request()


class GoogleAuthError(Exception):
    pass


def verify_google_id_token(token: str) -> dict:
    """
    Verifies a Google Identity Services ID token (a signed JWT the frontend
    gets directly from Google, never from our own backend) and returns its
    payload — sub (Google account id), email, email_verified, name, etc.

    Verification checks the signature against Google's own public keys
    AND that the token's `aud` (audience) claim matches our own OAuth
    client ID — without that second check, a valid ID token issued for a
    COMPLETELY DIFFERENT app (obtained some other way) would still pass
    signature verification, letting an attacker log in as whoever they
    could get a token for elsewhere. Raises GoogleAuthError on any
    verification failure (expired, wrong audience, bad signature, ...).
    """
    if not settings.google_oauth_client_id:
        raise GoogleAuthError("Google sign-in isn't configured yet")
    try:
        return google_id_token.verify_oauth2_token(token, _request, settings.google_oauth_client_id)
    except ValueError as exc:
        raise GoogleAuthError(str(exc)) from exc

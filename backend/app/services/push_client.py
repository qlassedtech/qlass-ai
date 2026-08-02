"""
Thin wrapper around firebase-admin for sending push notifications to the
native Android student app (see android/app — FCM token is registered via
POST /student-app/device-token and stored on Student.fcm_token).

firebase-admin has been in requirements.txt from the start but was never
actually wired up anywhere in the app until now. This stays a no-op (same
"not configured" pattern as send_whatsapp_message when Wati credentials are
missing) until FIREBASE_CREDENTIALS_PATH is set to a real service account
JSON downloaded from the Firebase console — that requires a Firebase
project the user creates themselves; this module only prepares for one to
be dropped in.
"""
from app.config import settings

_firebase_app = None
_init_attempted = False
# Set only if _get_app's own init attempt raised — distinct from "never
# configured at all" (FIREBASE_CREDENTIALS_PATH unset), so a real credential
# failure (bad file, malformed key) never gets reported as the same generic
# "not configured" message that would mask what's actually wrong.
_init_error: str | None = None


def _get_app():
    global _firebase_app, _init_attempted, _init_error
    if _init_attempted:
        return _firebase_app
    _init_attempted = True
    if not settings.firebase_credentials_path:
        return None
    try:
        import firebase_admin
        from firebase_admin import credentials

        cred = credentials.Certificate(settings.firebase_credentials_path)
        _firebase_app = firebase_admin.initialize_app(cred)
    except Exception as exc:
        _firebase_app = None
        _init_error = str(exc)
    return _firebase_app


async def send_push(token: str, title: str, body: str) -> dict:
    app = _get_app()
    if app is None:
        reason = _init_error or "Firebase credentials not configured (FIREBASE_CREDENTIALS_PATH)"
        return {"sent": False, "reason": reason}

    try:
        from firebase_admin import messaging

        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body), token=token,
        )
        message_id = messaging.send(message, app=app)
        return {"sent": True, "message_id": message_id}
    except Exception as exc:
        return {"sent": False, "reason": str(exc)}

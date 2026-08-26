import firebase_admin

import config


def ensure_initialized() -> None:
    try:
        firebase_admin.get_app()
    except ValueError:
        firebase_admin.initialize_app(options={"projectId": config.GOOGLE_CLOUD_PROJECT_ID})

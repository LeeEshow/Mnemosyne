import base64
import json
import os
import sys

import firebase_admin
from firebase_admin import credentials

import config


def ensure_initialized() -> None:
    try:
        firebase_admin.get_app()
    except ValueError:
        cred_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
        if cred_json:
            try:
                # 部署慣例（比照 NoCode_Project）是把金鑰檔案 base64 編碼後存進這個環境變數；
                # 若解不出來則退回當作未編碼的原始 JSON 字串處理，兩種格式都能吃。
                try:
                    cred_dict = json.loads(base64.b64decode(cred_json).decode())
                except Exception:
                    cred_dict = json.loads(cred_json)
                cred = credentials.Certificate(cred_dict)
                firebase_admin.initialize_app(cred, options={"projectId": config.GOOGLE_CLOUD_PROJECT_ID})
                return
            except Exception as e:
                print(
                    f"Warning: Failed to initialize Firebase using GOOGLE_APPLICATION_CREDENTIALS_JSON: {e}",
                    file=sys.stderr,
                )

        firebase_admin.initialize_app(options={"projectId": config.GOOGLE_CLOUD_PROJECT_ID})

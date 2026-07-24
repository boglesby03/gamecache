import base64
import hashlib
import json
from urllib.parse import quote

import requests


class BackblazeB2Client:
    def __init__(self, key_id, application_key, bucket_id, session=None):
        self.key_id = key_id
        self.application_key = application_key
        self.bucket_id = bucket_id
        self.session = session or requests.Session()
        self.api_url = None
        self.auth_token = None
        self.upload_url = None
        self.upload_auth_token = None

    def authorize(self):
        credentials = f"{self.key_id}:{self.application_key}".encode("utf-8")
        encoded = base64.b64encode(credentials).decode("ascii")
        headers = {"Authorization": f"Basic {encoded}"}

        response = self.session.get(
            "https://api.backblazeb2.com/b2api/v2/b2_authorize_account",
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()

        self.api_url = payload["apiUrl"]
        self.auth_token = payload["authorizationToken"]

    def _ensure_upload_url(self):
        if self.upload_url and self.upload_auth_token:
            return

        if not self.api_url or not self.auth_token:
            self.authorize()

        response = self.session.post(
            f"{self.api_url}/b2api/v2/b2_get_upload_url",
            headers={"Authorization": self.auth_token},
            json={"bucketId": self.bucket_id},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        self.upload_url = payload["uploadUrl"]
        self.upload_auth_token = payload["authorizationToken"]

    def upload_bytes(self, data, destination_path, content_type="b2/x-auto"):
        self._ensure_upload_url()

        sha1 = hashlib.sha1(data).hexdigest()
        headers = {
            "Authorization": self.upload_auth_token,
            "X-Bz-File-Name": quote(destination_path, safe="/"),
            "Content-Type": content_type,
            "X-Bz-Content-Sha1": sha1,
        }

        response = self.session.post(
            self.upload_url,
            headers=headers,
            data=data,
            timeout=120,
        )

        # Upload URLs can expire; retry once by requesting a new upload URL.
        if response.status_code in (401, 403):
            self.upload_url = None
            self.upload_auth_token = None
            self._ensure_upload_url()
            headers["Authorization"] = self.upload_auth_token
            response = self.session.post(
                self.upload_url,
                headers=headers,
                data=data,
                timeout=120,
            )

        response.raise_for_status()
        payload = response.json()
        payload["sha1"] = sha1
        payload["destination_path"] = destination_path
        return payload

    @staticmethod
    def format_public_file_url(download_url, bucket_name, file_name):
        # Useful when buckets are public and indexers can fetch directly.
        return f"{download_url}/file/{bucket_name}/{quote(file_name, safe='/')}"

    @staticmethod
    def describe_http_error(exc):
        response = getattr(exc, "response", None)
        if response is None:
            return str(exc)

        detail = response.text
        try:
            payload = response.json()
            detail = json.dumps(payload, ensure_ascii=True)
        except Exception:
            pass

        return f"HTTP {response.status_code}: {detail}"

#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


class GarageBootstrapError(RuntimeError):
    pass


@dataclass(frozen=True)
class BootstrapConfig:
    garage_admin_url: str
    garage_admin_token: str
    garage_zone: str
    garage_capacity: int
    garage_key_name: str
    s3_url: str
    s3_bucket: str
    s3_username: str
    s3_password: str

    @staticmethod
    def _read_required_env(name: str) -> str:
        value = os.environ.get(name, "").strip()
        if not value:
            raise GarageBootstrapError(f"{name} is required. Set it in .env or example.env.")
        return value

    @staticmethod
    def _parse_capacity(raw: str) -> int:
        match = re.fullmatch(r"(\d+)([KMGTP]?B?)?", raw.strip().upper())
        if not match:
            raise GarageBootstrapError(
                "Unsupported GARAGE_CAPACITY value. Use a plain number or suffix like 1G, 500M, 10KB."
            )

        value = int(match.group(1))
        suffix = match.group(2) or ""
        multipliers = {
            "": 1,
            "B": 1,
            "K": 1_000,
            "KB": 1_000,
            "M": 1_000_000,
            "MB": 1_000_000,
            "G": 1_000_000_000,
            "GB": 1_000_000_000,
            "T": 1_000_000_000_000,
            "TB": 1_000_000_000_000,
            "P": 1_000_000_000_000_000,
            "PB": 1_000_000_000_000_000,
        }
        return value * multipliers[suffix]

    @classmethod
    def from_env(cls) -> "BootstrapConfig":
        return cls(
            garage_admin_url=cls._read_required_env("GARAGE_ADMIN_URL").rstrip("/"),
            garage_admin_token=cls._read_required_env("GARAGE_ADMIN_TOKEN"),
            garage_zone=cls._read_required_env("GARAGE_ZONE"),
            garage_capacity=cls._parse_capacity(cls._read_required_env("GARAGE_CAPACITY")),
            garage_key_name=cls._read_required_env("GARAGE_KEY_NAME"),
            s3_url=cls._read_required_env("S3_URL").rstrip("/"),
            s3_bucket=cls._read_required_env("S3_BUCKET"),
            s3_username=cls._read_required_env("S3_USERNAME"),
            s3_password=cls._read_required_env("S3_PASSWORD"),
        )


class GarageAdminClient:
    def __init__(self, config: BootstrapConfig, timeout: int = 5):
        self.base_url = config.garage_admin_url
        self.timeout = timeout
        self.headers = {
            "Authorization": f"Bearer {config.garage_admin_token}",
            "Content-Type": "application/json",
        }

    def request_required(self, method: str, path: str, payload: Any = None):
        status, body = self._request_json(method, path, payload)
        if 200 <= status < 300:
            return body
        raise GarageBootstrapError(f"{method} {path} failed with {status}: {body}")

    def get_optional(self, path: str):
        status, body = self._request_json("GET", path)
        if status == 404:
            return None
        if 200 <= status < 300:
            return body
        raise GarageBootstrapError(f"GET {path} failed with {status}: {body}")

    def wait_for_cluster(self, attempts: int = 60, delay_seconds: int = 1):
        for _ in range(attempts):
            try:
                status, body = self._request_json("GET", "GetClusterStatus")
            except urllib.error.URLError:
                time.sleep(delay_seconds)
                continue

            if 200 <= status < 300 and body and body.get("nodes"):
                return body

            time.sleep(delay_seconds)

        raise GarageBootstrapError("Garage admin API did not become ready in time.")

    def get_bucket(self, bucket_name: str):
        query = urllib.parse.quote(bucket_name)
        return self.get_optional(f"GetBucketInfo?globalAlias={query}")

    def create_bucket(self, bucket_name: str):
        return self.request_required("POST", "CreateBucket", {"globalAlias": bucket_name})

    def get_access_key(self, access_key_id: str):
        key_id = urllib.parse.quote(access_key_id)
        return self.get_optional(f"GetKeyInfo?id={key_id}&showSecretKey=true")

    def import_access_key(self, access_key_id: str, secret_access_key: str, name: str):
        return self.request_required(
            "POST",
            "ImportKey",
            {
                "accessKeyId": access_key_id,
                "secretAccessKey": secret_access_key,
                "name": name,
            },
        )

    def allow_bucket_key(self, bucket_id: str, access_key_id: str):
        return self.request_required(
            "POST",
            "AllowBucketKey",
            {
                "bucketId": bucket_id,
                "accessKeyId": access_key_id,
                "permissions": {"read": True, "write": True, "owner": True},
            },
        )

    def _request_json(self, method: str, path: str, payload: Any = None):
        url = f"{self.base_url}/{path.lstrip('/')}"
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(url, data=data, headers=self.headers, method=method)

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode()
                return response.status, json.loads(raw) if raw and raw != "null" else None
        except urllib.error.HTTPError as error:
            raw = error.read().decode()
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = raw
            return error.code, parsed


class GarageBootstrapper:
    def __init__(self, config: BootstrapConfig, client: GarageAdminClient):
        self.config = config
        self.client = client

    def run(self):
        cluster = self.client.wait_for_cluster()
        self.ensure_cluster_layout(cluster)
        bucket = self.ensure_bucket()
        access_key_id = self.ensure_access_key()
        self.client.allow_bucket_key(bucket["id"], access_key_id)
        self.print_summary(access_key_id)

    def ensure_cluster_layout(self, cluster: dict[str, Any]):
        node = self.select_node(cluster)
        desired_role = {
            "id": node["id"],
            "zone": self.config.garage_zone,
            "capacity": self.config.garage_capacity,
            "tags": [],
        }

        layout = self.client.request_required(
            "POST",
            "UpdateClusterLayout",
            {"roles": [desired_role]},
        )
        if layout.get("stagedRoleChanges") or layout.get("stagedParameters"):
            self.client.request_required(
                "POST",
                "ApplyClusterLayout",
                {"version": layout["version"] + 1},
            )

    def ensure_bucket(self):
        bucket = self.client.get_bucket(self.config.s3_bucket)
        if bucket is None:
            bucket = self.client.create_bucket(self.config.s3_bucket)
        return bucket

    def ensure_access_key(self) -> str:
        access_key_id = self.config.s3_username
        key = self.client.get_access_key(access_key_id)
        if key is None:
            self.client.import_access_key(
                access_key_id,
                self.config.s3_password,
                self.config.garage_key_name,
            )
            return access_key_id

        secret = key.get("secretAccessKey")
        if secret and secret != self.config.s3_password:
            raise GarageBootstrapError(
                "Garage access key already exists with a different secret. "
                "Update S3_PASSWORD or remove the existing key before retrying."
            )

        return access_key_id

    def print_summary(self, access_key_id: str):
        print("Garage bootstrap completed.")
        print(f"S3 endpoint: {self.config.s3_url}")
        print(f"Bucket: {self.config.s3_bucket}")
        print(f"Access key: {access_key_id}")

    @staticmethod
    def select_node(cluster: dict[str, Any]):
        return next(
            (item for item in cluster["nodes"] if item.get("isUp")),
            cluster["nodes"][0],
        )


def main():
    config = BootstrapConfig.from_env()
    client = GarageAdminClient(config)
    bootstrapper = GarageBootstrapper(config, client)
    bootstrapper.run()


if __name__ == "__main__":
    main()

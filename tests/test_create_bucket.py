import unittest
from unittest import mock

from scripts import create_bucket


def build_config(**overrides):
    values = {
        "garage_admin_url": "http://garage:3903/v2",
        "garage_admin_token": "token",
        "garage_zone": "dc1",
        "garage_capacity": 1_000_000_000,
        "garage_key_name": "dsw-engine-wizard",
        "s3_url": "http://garage:3900",
        "s3_bucket": "engine-wizard",
        "s3_username": "access-key",
        "s3_password": "secret-key",
    }
    values.update(overrides)
    return create_bucket.BootstrapConfig(**values)


class BootstrapConfigTC(unittest.TestCase):
    def test_parse_capacity(self):
        parse_capacity = create_bucket.BootstrapConfig._parse_capacity

        cases = {
            "1": 1,
            "1B": 1,
            "10KB": 10_000,
            "500M": 500_000_000,
            "1G": 1_000_000_000,
            "2gb": 2_000_000_000,
            "3TB": 3_000_000_000_000,
            "4pb": 4_000_000_000_000_000,
        }

        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(expected, parse_capacity(raw))

    def test_invalid_capacity(self):
        parse_capacity = create_bucket.BootstrapConfig._parse_capacity

        for raw in ("abc", "1.5G", "-1G", "1KiB"):
            with self.subTest(raw=raw):
                with self.assertRaises(create_bucket.GarageBootstrapError):
                    parse_capacity(raw)

    def test_from_env(self):
        env_values = {
            "GARAGE_ADMIN_URL": "http://garage:3903/v2/",
            "GARAGE_ADMIN_TOKEN": "token",
            "GARAGE_ZONE": "dc1",
            "GARAGE_CAPACITY": "1G",
            "GARAGE_KEY_NAME": "dsw-engine-wizard",
            "S3_URL": "http://garage:3900/",
            "S3_BUCKET": "engine-wizard",
            "S3_USERNAME": "access-key",
            "S3_PASSWORD": "secret-key",
        }

        with mock.patch.dict(
            create_bucket.os.environ,
            env_values,
            clear=False,
        ):
            config = create_bucket.BootstrapConfig.from_env()

        self.assertEqual("http://garage:3903/v2", config.garage_admin_url)
        self.assertEqual("http://garage:3900", config.s3_url)
        self.assertEqual(1_000_000_000, config.garage_capacity)


class GarageBootstrapperTC(unittest.TestCase):
    def test_ensure_bucket(self):
        client = mock.Mock()
        client.get_bucket.return_value = None
        client.create_bucket.return_value = {"id": "bucket-1"}

        bootstrapper = create_bucket.GarageBootstrapper(
            build_config(),
            client,
        )

        bucket = bootstrapper.ensure_bucket()

        client.get_bucket.assert_called_once_with("engine-wizard")
        client.create_bucket.assert_called_once_with("engine-wizard")
        self.assertEqual({"id": "bucket-1"}, bucket)

    def test_ensure_access_key_import(self):
        client = mock.Mock()
        client.get_access_key.return_value = None

        bootstrapper = create_bucket.GarageBootstrapper(
            build_config(),
            client,
        )

        access_key_id = bootstrapper.ensure_access_key()

        client.get_access_key.assert_called_once_with("access-key")
        client.import_access_key.assert_called_once_with(
            "access-key",
            "secret-key",
            "dsw-engine-wizard",
        )
        self.assertEqual("access-key", access_key_id)

    def test_ensure_access_key_secret_mismatch(self):
        client = mock.Mock()
        client.get_access_key.return_value = {
            "secretAccessKey": "different-secret"
        }

        bootstrapper = create_bucket.GarageBootstrapper(
            build_config(),
            client,
        )

        with self.assertRaises(create_bucket.GarageBootstrapError):
            bootstrapper.ensure_access_key()

        client.import_access_key.assert_not_called()

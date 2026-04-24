from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import bootstrap_env


def build_resolved_values(**overrides):
    resolved_values = {
        "CLIENT_URL": "http://localhost:8080/wizard",
        "GENERAL_SECRET": "general-secret",
        "DATABASE_CONNECTION_STRING": (
            "postgresql://postgres:postgres-password@postgres:5432/"
            "engine-wizard"
        ),
        "S3_URL": "http://s3.example.invalid:9000",
        "S3_USERNAME": "access-key",
        "S3_PASSWORD": "secret-key",
        "S3_BUCKET": "engine-wizard",
        "S3_REGION": "garage",
        "MAIL_ENABLED": "false",
        "MAIL_NAME": "",
        "MAIL_EMAIL": "",
        "MAIL_PROVIDER": "smtp",
        "MAIL_SMTP_HOST": "",
        "MAIL_SMTP_PORT": "25",
        "MAIL_SMTP_SECURITY": "plain",
        "MAIL_SMTP_USERNAME": "",
        "MAIL_SMTP_PASSWORD": "",
    }
    resolved_values.update(overrides)
    return resolved_values


class EnvTemplateTC(unittest.TestCase):
    def test_parse_literal(self):
        parse_literal = bootstrap_env.EnvTemplate.parse_literal

        self.assertEqual("plain", parse_literal("plain"))
        self.assertEqual("quoted value", parse_literal("'quoted value'"))
        self.assertEqual("line1\nline2", parse_literal('"line1\\nline2"'))

    def test_format_value(self):
        format_value = bootstrap_env.EnvTemplate.format_value

        self.assertEqual("plain-value", format_value("plain-value"))
        self.assertEqual('"hello world"', format_value("hello world"))
        self.assertEqual('"line1\\nline2"', format_value("line1\nline2"))

    def test_resolve_env_values(self):
        template = bootstrap_env.EnvTemplate(
            lines=[],
            assignments={
                "GENERAL_SECRET": "<auto>",
                "POSTGRES_USER": "postgres",
                "POSTGRES_PASSWORD": "<auto>",
                "POSTGRES_HOST": "postgres",
                "POSTGRES_PORT": "5432",
                "POSTGRES_DB": "engine-wizard",
                "DATABASE_CONNECTION_STRING": "<derived>",
            },
        )

        resolved = bootstrap_env.resolve_env_values(template)

        self.assertTrue(resolved["GENERAL_SECRET"])
        self.assertEqual(24, len(resolved["POSTGRES_PASSWORD"]))
        self.assertEqual(
            (
                "postgresql://postgres:"
                f"{resolved['POSTGRES_PASSWORD']}@postgres:5432/"
                "engine-wizard"
            ),
            resolved["DATABASE_CONNECTION_STRING"],
        )


class StateGuardTC(unittest.TestCase):
    def test_existing_volume(self):
        guard = bootstrap_env.BootstrapStateGuard(
            ("dsw-deployment_db-data", "dsw-deployment_garage-data")
        )
        guard._exists = (
            lambda volume_name: volume_name == "dsw-deployment_db-data"
        )

        with self.assertRaisesRegex(RuntimeError, "dsw-deployment_db-data"):
            guard.ensure_clean_state()


class ApplicationSkeletonTC(unittest.TestCase):
    def test_missing_sections(self):
        with tempfile.TemporaryDirectory() as tempdir:
            skeleton_path = Path(tempdir) / "application.yml"
            skeleton_path.write_text("general:\ndatabase:\n")

            with self.assertRaisesRegex(RuntimeError, "s3:, mail:"):
                bootstrap_env.validate_application_skeleton(skeleton_path)


class ApplicationConfigTC(unittest.TestCase):
    @mock.patch.object(
        bootstrap_env,
        "generate_rsa_private_key",
        return_value="RSA-LINE-1\nRSA-LINE-2",
    )
    def test_render(self, _generate_rsa_private_key):
        rendered = bootstrap_env.render_application_config(
            build_resolved_values()
        )

        self.assertIn("general:", rendered)
        self.assertIn('  clientUrl: "http://localhost:8080/wizard"', rendered)
        self.assertIn('  secret: "general-secret"', rendered)
        self.assertIn(
            "  rsaPrivateKey: |\n    RSA-LINE-1\n    RSA-LINE-2",
            rendered,
        )
        self.assertIn("database:", rendered)
        self.assertIn(
            (
                '  connectionString: '
                '"postgresql://postgres:postgres-password'
                '@postgres:5432/engine-wizard"'
            ),
            rendered,
        )
        self.assertIn("s3:", rendered)
        self.assertIn('  url: "http://s3.example.invalid:9000"', rendered)
        self.assertIn('  username: "access-key"', rendered)
        self.assertIn('  password: "secret-key"', rendered)
        self.assertIn("mail:", rendered)
        self.assertIn("  enabled: false", rendered)
        self.assertIn("    port: 25", rendered)
        self.assertNotIn(bootstrap_env.AUTO_VALUE_PLACEHOLDER, rendered)
        self.assertNotIn(bootstrap_env.DERIVED_VALUE_PLACEHOLDER, rendered)

    @mock.patch.object(
        bootstrap_env,
        "generate_rsa_private_key",
        return_value="RSA-LINE-1",
    )
    def test_invalid_mail_enabled(self, _generate_rsa_private_key):
        with self.assertRaisesRegex(RuntimeError, "MAIL_ENABLED"):
            bootstrap_env.render_application_config(
                build_resolved_values(MAIL_ENABLED="maybe")
            )

    @mock.patch.object(
        bootstrap_env,
        "generate_rsa_private_key",
        return_value="RSA-LINE-1",
    )
    def test_invalid_smtp_port(self, _generate_rsa_private_key):
        with self.assertRaisesRegex(RuntimeError, "MAIL_SMTP_PORT"):
            bootstrap_env.render_application_config(
                build_resolved_values(MAIL_SMTP_PORT="abc")
            )


class BootstrapEnvGeneratorTC(unittest.TestCase):
    class DummyStateGuard:
        def __init__(self):
            self.called = False

        def ensure_clean_state(self):
            self.called = True

    def test_run(self):
        with tempfile.TemporaryDirectory() as tempdir:
            env_template_path = Path(tempdir) / "example.env"
            env_output_path = Path(tempdir) / ".env"
            application_template_path = Path(tempdir) / "application.yml"
            application_output_path = (
                Path(tempdir) / "application.resolved.yml"
            )
            env_template_lines = [
                "CLIENT_URL=http://localhost:8080/wizard",
                "GENERAL_SECRET=<auto>",
                "POSTGRES_USER=postgres",
                "POSTGRES_HOST=postgres",
                "POSTGRES_PORT=5432",
                "POSTGRES_PASSWORD=<auto>",
                "POSTGRES_DB=engine-wizard",
                "DATABASE_CONNECTION_STRING=<derived>",
                "S3_URL=http://s3.example.invalid:9000",
                "S3_USERNAME=<auto>",
                "S3_PASSWORD=<auto>",
                "S3_BUCKET=engine-wizard",
                "S3_REGION=garage",
                "MAIL_ENABLED=false",
                "MAIL_NAME=",
                "MAIL_EMAIL=",
                "MAIL_PROVIDER=smtp",
                "MAIL_SMTP_HOST=",
                "MAIL_SMTP_PORT=25",
                "MAIL_SMTP_SECURITY=plain",
                "MAIL_SMTP_USERNAME=",
                "MAIL_SMTP_PASSWORD=",
                "GARAGE_RPC_SECRET=<auto>",
                "GARAGE_ADMIN_TOKEN=<auto>",
                "GARAGE_METRICS_TOKEN=<auto>",
                "",
            ]
            application_template = "general:\ndatabase:\ns3:\nmail:\n"
            auto_generated_builders = {
                "GENERAL_SECRET": lambda: "general-secret",
                "POSTGRES_PASSWORD": lambda: "postgres-password",
                "S3_USERNAME": lambda: "access-key",
                "S3_PASSWORD": lambda: "secret-key",
                "GARAGE_RPC_SECRET": lambda: "rpc-secret",
                "GARAGE_ADMIN_TOKEN": lambda: "admin-token",
                "GARAGE_METRICS_TOKEN": lambda: "metrics-token",
            }

            env_template_path.write_text(
                "\n".join(env_template_lines)
            )
            application_template_path.write_text(application_template)

            state_guard = self.DummyStateGuard()

            with mock.patch.dict(
                bootstrap_env.AUTO_GENERATED_VALUE_BUILDERS,
                auto_generated_builders,
                clear=False,
            ):
                with mock.patch.object(
                    bootstrap_env,
                    "generate_rsa_private_key",
                    return_value="RSA-LINE-1\nRSA-LINE-2",
                ):
                    generator = bootstrap_env.BootstrapEnvGenerator(
                        env_template_path=env_template_path,
                        env_output_path=env_output_path,
                        application_skeleton_path=application_template_path,
                        application_output_path=application_output_path,
                        force=False,
                        state_guard=state_guard,
                    )
                    generator.run()

            env_text = env_output_path.read_text()
            application_text = application_output_path.read_text()

            self.assertTrue(state_guard.called)
            self.assertNotIn(bootstrap_env.AUTO_VALUE_PLACEHOLDER, env_text)
            self.assertNotIn(bootstrap_env.DERIVED_VALUE_PLACEHOLDER, env_text)
            self.assertIn("GENERAL_SECRET=general-secret", env_text)
            self.assertIn("POSTGRES_PASSWORD=postgres-password", env_text)
            self.assertIn("S3_USERNAME=access-key", env_text)
            self.assertIn("S3_PASSWORD=secret-key", env_text)
            self.assertIn(
                (
                    "DATABASE_CONNECTION_STRING=postgresql://postgres:"
                    "postgres-password@postgres:5432/engine-wizard"
                ),
                env_text,
            )
            self.assertIn("GARAGE_ADMIN_TOKEN=admin-token", env_text)
            self.assertEqual(0o600, env_output_path.stat().st_mode & 0o777)

            self.assertIn('  secret: "general-secret"', application_text)
            self.assertIn(
                '  url: "http://s3.example.invalid:9000"',
                application_text,
            )
            self.assertIn('  username: "access-key"', application_text)
            self.assertIn('  password: "secret-key"', application_text)
            self.assertIn(
                "  rsaPrivateKey: |\n    RSA-LINE-1\n    RSA-LINE-2",
                application_text,
            )
            self.assertEqual(
                0o644,
                application_output_path.stat().st_mode & 0o777,
            )

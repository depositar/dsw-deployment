#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import string
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ClassVar, Mapping

AUTO_VALUE_PLACEHOLDER = "<auto>"
DERIVED_VALUE_PLACEHOLDER = "<derived>"


@dataclass(frozen=True)
class EnvTemplate:
    plain_value_pattern: ClassVar[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_./:@+-]+")

    lines: list[str]
    assignments: dict[str, str]

    @classmethod
    def load(cls, path: Path) -> "EnvTemplate":
        if not path.exists():
            raise RuntimeError(f"Template file not found: {path}")

        lines = path.read_text().splitlines()
        return cls(lines=lines, assignments=cls._parse_assignments(lines))

    @staticmethod
    def parse_literal(raw: str) -> str:
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            return bytes(value[1:-1], "utf-8").decode("unicode_escape")
        if len(value) >= 2 and value[0] == value[-1] == "'":
            return value[1:-1]
        return value

    @classmethod
    def format_value(cls, value: str) -> str:
        if value == "":
            return ""
        if cls.plain_value_pattern.fullmatch(value):
            return value
        escaped = value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
        return f'"{escaped}"'

    def render(self, resolved_values: Mapping[str, str]) -> str:
        rendered_lines: list[str] = []
        for line in self.lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in line:
                rendered_lines.append(line)
                continue

            key, _ = line.split("=", 1)
            rendered_lines.append(f"{key}={self.format_value(resolved_values[key])}")

        return "\n".join(rendered_lines) + "\n"

    @staticmethod
    def _parse_assignments(lines: list[str]) -> dict[str, str]:
        assignments: dict[str, str] = {}
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in line:
                continue
            key, raw_value = line.split("=", 1)
            assignments[key] = raw_value
        return assignments


def random_alnum(length: int) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_rsa_private_key() -> str:
    try:
        result = subprocess.run(
            ["openssl", "genrsa", "-traditional", "4096"],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise RuntimeError(
            "openssl is required to generate the RSA private key for application.resolved.yml."
        ) from error
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            f"openssl failed to generate the RSA private key for application.resolved.yml: {error.stderr}"
        ) from error

    return result.stdout.strip()


def discover_deployment_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_from_root(root: Path, raw_path: str) -> Path:
    return (root / raw_path).resolve()


def deployment_state_paths(root: Path) -> tuple[Path, ...]:
    return (
        root / "db-data/data",
        root / "garage-data/meta",
        root / "garage-data/data",
    )


def build_database_connection_string(resolved_values: Mapping[str, str]) -> str:
    return (
        f"postgresql://{resolved_values['POSTGRES_USER']}:"
        f"{resolved_values['POSTGRES_PASSWORD']}@{resolved_values['POSTGRES_HOST']}:"
        f"{resolved_values['POSTGRES_PORT']}/"
        f"{resolved_values['POSTGRES_DB']}"
    )


def parse_bool(name: str, raw: str) -> str:
    value = raw.strip().lower()
    if value in {"true", "1", "yes", "on"}:
        return "true"
    if value in {"false", "0", "no", "off"}:
        return "false"
    raise RuntimeError(f"{name} must be a boolean value like true or false.")


def parse_int(name: str, raw: str) -> int:
    try:
        return int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer value.") from error


def yaml_string(value: str) -> str:
    return json.dumps(value)


class BootstrapStateGuard:
    ignored_filenames: ClassVar[set[str]] = {".gitkeep"}

    def __init__(self, state_paths: tuple[Path, ...]):
        self.state_paths = state_paths

    def ensure_clean_state(self):
        found_paths = [path for path in self.state_paths if self._contains_state(path)]
        if not found_paths:
            return

        joined_paths = ", ".join(str(path) for path in found_paths)
        raise RuntimeError(
            "Existing persistent state detected in "
            f"{joined_paths}. bootstrap-env.py now generates a fresh environment each time, "
            "so rerunning it against an existing PostgreSQL or Garage data directory is unsafe. "
            "Restore the previous .env/application.resolved.yml, or remove the persistent data "
            "directories before bootstrapping a brand-new environment."
        )

    def _contains_state(self, path: Path) -> bool:
        if not path.exists():
            return False
        if path.is_file():
            return path.name not in self.ignored_filenames

        for child in path.rglob("*"):
            if child.is_file() and child.name not in self.ignored_filenames:
                return True
        return False


AUTO_GENERATED_VALUE_BUILDERS: dict[str, Callable[[], str]] = {
    "GENERAL_SECRET": lambda: random_alnum(32),
    "POSTGRES_PASSWORD": lambda: random_alnum(24),
    "S3_USERNAME": lambda: "GK" + secrets.token_hex(12),
    "S3_PASSWORD": lambda: secrets.token_hex(32),
    "GARAGE_RPC_SECRET": lambda: secrets.token_hex(32),
    "GARAGE_ADMIN_TOKEN": lambda: random_alnum(32),
    "GARAGE_METRICS_TOKEN": lambda: random_alnum(32),
}

DERIVED_VALUE_BUILDERS: dict[str, Callable[[Mapping[str, str]], str]] = {
    "DATABASE_CONNECTION_STRING": build_database_connection_string,
}


def resolve_base_env_value(key: str, parsed_value: str) -> str:
    if key in AUTO_GENERATED_VALUE_BUILDERS and parsed_value in {"", AUTO_VALUE_PLACEHOLDER}:
        return AUTO_GENERATED_VALUE_BUILDERS[key]()
    if key in DERIVED_VALUE_BUILDERS and parsed_value in {"", DERIVED_VALUE_PLACEHOLDER}:
        return ""
    return parsed_value


def resolve_base_env_values(template: EnvTemplate) -> dict[str, str]:
    resolved_values: dict[str, str] = {}

    for key, raw_value in template.assignments.items():
        parsed_value = EnvTemplate.parse_literal(raw_value)
        resolved_values[key] = resolve_base_env_value(key, parsed_value)

    return resolved_values


def populate_derived_env_values(template: EnvTemplate, resolved_values: dict[str, str]):
    for key, builder in DERIVED_VALUE_BUILDERS.items():
        if key in template.assignments and not resolved_values.get(key):
            resolved_values[key] = builder(resolved_values)


def resolve_env_values(template: EnvTemplate) -> dict[str, str]:
    resolved_values = resolve_base_env_values(template)
    populate_derived_env_values(template, resolved_values)
    return resolved_values


def validate_application_skeleton(path: Path):
    if not path.exists():
        raise RuntimeError(f"Application skeleton file not found: {path}")

    skeleton = path.read_text()
    required_sections = ("general:", "database:", "s3:", "mail:")
    missing_sections = [section for section in required_sections if section not in skeleton]
    if missing_sections:
        raise RuntimeError(
            f"Application skeleton {path} is missing required sections: {', '.join(missing_sections)}"
        )


def render_application_config(resolved_values: Mapping[str, str]) -> str:
    rsa_private_key = generate_rsa_private_key()
    mail_enabled = parse_bool("MAIL_ENABLED", resolved_values["MAIL_ENABLED"])
    smtp_port = parse_int("MAIL_SMTP_PORT", resolved_values["MAIL_SMTP_PORT"])

    lines = [
        "general:",
        f"  clientUrl: {yaml_string(resolved_values['CLIENT_URL'])}",
        f"  secret: {yaml_string(resolved_values['GENERAL_SECRET'])}",
        "  rsaPrivateKey: |",
    ]
    lines.extend(f"    {line}" for line in rsa_private_key.splitlines())
    lines.extend(
        [
            "",
            "database:",
            f"  connectionString: {yaml_string(resolved_values['DATABASE_CONNECTION_STRING'])}",
            "",
            "s3:",
            f"  url: {yaml_string(resolved_values['S3_URL'])}",
            f"  username: {yaml_string(resolved_values['S3_USERNAME'])}",
            f"  password: {yaml_string(resolved_values['S3_PASSWORD'])}",
            f"  bucket: {yaml_string(resolved_values['S3_BUCKET'])}",
            f"  region: {yaml_string(resolved_values['S3_REGION'])}",
            "",
            "mail:",
            f"  enabled: {mail_enabled}",
            f"  name: {yaml_string(resolved_values['MAIL_NAME'])}",
            f"  email: {yaml_string(resolved_values['MAIL_EMAIL'])}",
            f"  provider: {yaml_string(resolved_values['MAIL_PROVIDER'])}",
            "  smtp:",
            f"    host: {yaml_string(resolved_values['MAIL_SMTP_HOST'])}",
            f"    port: {smtp_port}",
            f"    security: {yaml_string(resolved_values['MAIL_SMTP_SECURITY'])}",
            f"    username: {yaml_string(resolved_values['MAIL_SMTP_USERNAME'])}",
            f"    password: {yaml_string(resolved_values['MAIL_SMTP_PASSWORD'])}",
        ]
    )
    return "\n".join(lines) + "\n"


class BootstrapEnvGenerator:
    def __init__(
        self,
        env_template_path: Path,
        env_output_path: Path,
        application_skeleton_path: Path,
        application_output_path: Path,
        force: bool,
        state_guard: BootstrapStateGuard,
    ):
        self.env_template_path = env_template_path
        self.env_output_path = env_output_path
        self.application_skeleton_path = application_skeleton_path
        self.application_output_path = application_output_path
        self.force = force
        self.state_guard = state_guard

    def run(self):
        self.state_guard.ensure_clean_state()

        env_template = EnvTemplate.load(self.env_template_path)
        validate_application_skeleton(self.application_skeleton_path)
        resolved_values = resolve_env_values(env_template)

        self._write_output(self.env_output_path, env_template.render(resolved_values), 0o600)
        self._write_output(
            self.application_output_path,
            render_application_config(resolved_values),
            0o644,
        )

        print(f"Wrote {self.env_output_path}")
        print(f"Wrote {self.application_output_path}")

    def _write_output(self, path: Path, content: str, mode: int):
        if path.exists() and not self.force:
            raise RuntimeError(f"{path} already exists. Use --force to overwrite it.")

        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        path.write_text(content)
        os.chmod(path, mode)


def parse_args(deployment_root: Path) -> argparse.Namespace:
    def resolve_path(raw_path: str) -> Path:
        return resolve_from_root(deployment_root, raw_path)

    parser = argparse.ArgumentParser(
        description="Generate .env and config/application.resolved.yml from template files."
    )
    parser.add_argument(
        "--env-template",
        type=resolve_path,
        default=resolve_path("example.env"),
        help="Path to the env template file.",
    )
    parser.add_argument(
        "--env-output",
        type=resolve_path,
        default=resolve_path(".env"),
        help="Path to the generated env file.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the output file if it already exists.",
    )
    parser.add_argument(
        "--application-template",
        type=resolve_path,
        default=resolve_path("config/application.yml"),
        help="Path to the application.yml skeleton file used for structural validation.",
    )
    parser.add_argument(
        "--application-output",
        type=resolve_path,
        default=resolve_path("config/application.resolved.yml"),
        help="Path to the generated runtime application.yml with resolved values.",
    )
    return parser.parse_args()


def main():
    deployment_root = discover_deployment_root()
    options = parse_args(deployment_root)
    state_guard = BootstrapStateGuard(deployment_state_paths(deployment_root))
    BootstrapEnvGenerator(
        env_template_path=options.env_template,
        env_output_path=options.env_output,
        application_skeleton_path=options.application_template,
        application_output_path=options.application_output,
        force=options.force,
        state_guard=state_guard,
    ).run()


if __name__ == "__main__":
    main()

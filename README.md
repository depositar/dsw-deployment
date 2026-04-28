# Production Deployment of depositar DSW

## Overview

This branch is based on [dsw-deployment-example](https://github.com/ds-wizard/dsw-deployment-example), modified for production use.

The following list shows the features that have (or have not) been modified:
   - [x] Delete port forwarding of garage and postgresql.
   - [x] Use named volumes for PostgreSQL and Garage persistent data.
   - [x] Add bootstrap scripts for generating keys and accounts.
   - [x] Add a one-off service in docker-compose.yml to create the bucket.
   - [x] Check lint and generate test coverage in CI runner.
   - [ ] A reverse proxy with automatic TLS.
   - [ ] An SMTP server with DMARC, DKIM, and SPF support.


## Quick Start

1. Install prerequisites:

   Requirements:

   - Python 3.7+
   - OpenSSL
   - Docker CE with Docker Compose V2

   Verify their availability:

   ```bash
   python3 --version
   openssl version
   docker compose version
   ```

   To run lint, tests, and pre-commit hooks locally,
   install the required packages in a virtual environment:

   ```bash
   python3 -m venv .venv
   ./.venv/bin/pip install flake8 pytest pytest-cov pre-commit
   ```

2. Generate the local environment files (`.env` and `config/application.resolved.yml`):

   ```bash
   ./scripts/bootstrap_env.py
   ```

3. Start the stack:

   ```bash
   docker compose --env-file .env up -d
   ```

4. Open DSW:

   [http://localhost:8080/wizard](http://localhost:8080/wizard/)

5. Log in with:

   - Email: `albert.einstein@example.com`
   - Password: `password`

## What The Bootstrap Does

This setup has two bootstrap stages:

- `./scripts/bootstrap_env.py` generates `.env` and `config/application.resolved.yml`
  from `example.env` and `config/application.yml`, respectively. It generates passwords and
  secret values that are left for automatic creation, builds values that depend
  on other settings such as `DATABASE_CONNECTION_STRING`, and generates the RSA
  private key for the DSW application.

- `create_bucket.py` runs automatically as the `create-bucket` Docker Compose
  service during startup. It is intended to be safe to rerun for this local
  setup. It assigns the single-node Garage layout if the node still has no
  role, creates the configured S3 bucket if it does not exist, imports the
  configured Garage access key if it does not exist, and grants read, write,
  and owner permissions on the bucket to that key.

## Verification Checklist

After setup, use this checklist to confirm the Garage-backed deployment works.

### Infrastructure checks

```bash
docker compose ps
docker compose logs garage --tail=100
docker compose logs server --tail=100
docker compose logs docworker --tail=100
```

Expected results:

- `garage` is running
- `server` creates the S3 client successfully
- `docworker` starts without S3 errors
- no authentication, signing, or region errors appear in the logs

### Application checks

In the DSW UI, verify:

1. the application opens and login works
2. a project file can be uploaded
3. the uploaded file can be downloaded
4. a document preview can be generated
5. a document template asset URL works, if applicable

If these checks pass, Garage is functioning as a drop-in S3-compatible backend for this deployment.

## Troubleshooting

If something does not work:

```bash
docker compose ps
docker compose logs garage --tail=200
docker compose logs server --tail=200
docker compose logs docworker --tail=200
```

## Development Checks

Run the following commands from the repository root after creating `.venv` and
installing the development tools:

### Unit Tests

```bash
./.venv/bin/pytest tests
```

### Lint

```bash
./.venv/bin/flake8 scripts tests
```

### Pre-commit Check

Run all configured hooks across the repository:

```bash
./.venv/bin/pre-commit run --all-files
```

Set up the Git hook once to run checks automatically before
each commit:

```bash
./.venv/bin/pre-commit install
```

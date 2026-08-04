"""Fail fast on configuration mistakes before a staging or production deploy.

Run: python scripts/release_preflight.py --env-file .env.production \
    --frontend-env-file frontend/.env.production

This intentionally never prints secret values — only which keys are
missing and which shape-checks failed, so it's safe to run from CI logs.
"""
import argparse
from pathlib import Path

REQUIRED = {
    "ENVIRONMENT", "SECRET_KEY", "DATABASE_URL", "REDIS_URL", "ANTHROPIC_API_KEY",
    "WHATSAPP_TOKEN", "WATI_API_ENDPOINT", "PORTAL_BASE_URL", "ALLOWED_ORIGINS",
    "RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET",
    "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB",
}


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def check(values: dict[str, str]) -> list[str]:
    failures = []
    missing = sorted(key for key in REQUIRED if not values.get(key))
    if missing:
        failures.append("missing: " + ", ".join(missing))
    if values.get("ENVIRONMENT", "").lower() != "production":
        failures.append("ENVIRONMENT must be production")
    secret_key = values.get("SECRET_KEY", "")
    if len(secret_key) < 32 or secret_key == "changeme":
        failures.append("SECRET_KEY must be unique and at least 32 characters")
    portal_url = values.get("PORTAL_BASE_URL", "")
    if "localhost" in portal_url or "127.0.0.1" in portal_url or not portal_url.startswith("https://") \
            or portal_url == "https://":
        failures.append("PORTAL_BASE_URL must be a public HTTPS URL")
    if "*" in values.get("ALLOWED_ORIGINS", ""):
        failures.append("ALLOWED_ORIGINS must not contain a wildcard *")
    database_url = values.get("DATABASE_URL", "")
    if "CHANGE_ME" in database_url or values.get("POSTGRES_PASSWORD", "") == "CHANGE_ME_SAME_PASSWORD_AS_DATABASE_URL_BELOW":
        failures.append("DATABASE_URL/POSTGRES_PASSWORD still contain the .env.production.example placeholder")
    postgres_user = values.get("POSTGRES_USER", "")
    postgres_db = values.get("POSTGRES_DB", "")
    if postgres_user and postgres_user not in database_url:
        failures.append("POSTGRES_USER does not appear in DATABASE_URL — they must reference the same database")
    if postgres_db and postgres_db not in database_url:
        failures.append("POSTGRES_DB does not appear in DATABASE_URL — they must reference the same database")
    return failures


def check_frontend(values: dict[str, str]) -> list[str]:
    failures = []
    api_base = values.get("VITE_API_BASE", "")
    if not api_base:
        failures.append("VITE_API_BASE is missing")
    elif "localhost" in api_base or "127.0.0.1" in api_base or not api_base.startswith("https://") \
            or api_base == "https://":
        failures.append("VITE_API_BASE must be a public HTTPS URL")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=".env.production")
    parser.add_argument("--frontend-env-file", default="frontend/.env.production")
    args = parser.parse_args()

    path = Path(args.env_file)
    if not path.exists():
        print(f"FAIL: {path} does not exist")
        return 1
    failures = check(parse_env(path))

    frontend_path = Path(args.frontend_env_file)
    if not frontend_path.exists():
        failures.append(f"{frontend_path} does not exist")
    else:
        failures.extend(check_frontend(parse_env(frontend_path)))

    if failures:
        print("FAIL: " + "; ".join(failures))
        return 1
    print("PASS: production configuration shape is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

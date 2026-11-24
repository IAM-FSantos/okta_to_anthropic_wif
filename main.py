"""
Okta -> Anthropic Workload Identity Federation (WIF), end to end, in one file.

A workload (script, container, CI runner) proves its identity to Okta with a
private key, receives a short-lived Okta access token, and exchanges that token
with Anthropic for an ephemeral Anthropic access token (sk-ant-oat01-...).
No static API key is stored anywhere.

    workload --(1) signed client assertion--> Okta
             <-------(2) Okta access token---
             --(3) Okta token + rule IDs----> Anthropic /v1/oauth/token
             <-------(4) Anthropic token-----
"""

import os
import sys
import time
import uuid
from dotenv import load_dotenv
import jwt  # PyJWT
import requests

# Load environment variables from local .env file
load_dotenv()

# Let emoji status lines print on Windows consoles that default to cp1252
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

REQUEST_TIMEOUT = 10  # seconds
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")


def require_env(name: str) -> str:
    """Return a required environment variable or exit with a clear message."""
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Missing required environment variable: {name}. Please check your .env file.")
    return value


def raise_for_oauth_error(resp: requests.Response, step: str) -> None:
    """Fail with the server's error body, detailing the exact cause of a 400/401."""
    if not resp.ok:
        sys.exit(f"{step} failed: HTTP {resp.status_code}\n{resp.text}")


def get_okta_access_token() -> str:
    """Steps 1-2: sign a client assertion and trade it for an Okta access token."""
    domain = require_env("OKTA_DOMAIN")
    auth_server_id = require_env("OKTA_AUTH_SERVER_ID")
    client_id = require_env("OKTA_CLIENT_ID")
    key_id = require_env("OKTA_KEY_ID")
    key_path = os.environ.get("OKTA_PRIVATE_KEY_PATH", "workload_private_key.pem")
    scope = os.environ.get("OKTA_SCOPE", "anthropic.access")

    token_url = f"https://{domain}/oauth2/{auth_server_id}/v1/token"

    if not os.path.exists(key_path):
        sys.exit(f"Private key file not found at: {key_path}")

    with open(key_path, "r") as f:
        private_key = f.read()

    # Client assertion (RFC 7523): App proves possession of the private key
    now = int(time.time())
    client_assertion = jwt.encode(
        {
            "iss": client_id,
            "sub": client_id,
            "aud": token_url,
            "iat": now,
            "exp": now + 300,  # Ephemeral: 5 minutes validity
            "jti": str(uuid.uuid4()),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": key_id},
    )

    # Okta expects a form-encoded payload
    resp = requests.post(
        token_url,
        data={
            "grant_type": "client_credentials",
            "scope": scope,
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": client_assertion,
        },
        timeout=REQUEST_TIMEOUT,
    )
    raise_for_oauth_error(resp, "Okta token request")
    return resp.json()["access_token"]


def exchange_for_anthropic_token(okta_access_token: str) -> dict:
    """Steps 3-4: exchange the Okta token for a short-lived Anthropic token."""
    body = {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": okta_access_token,
        "federation_rule_id": require_env("ANTHROPIC_FEDERATION_RULE_ID"),
        "organization_id": require_env("ANTHROPIC_ORGANIZATION_ID"),
        "service_account_id": require_env("ANTHROPIC_SERVICE_ACCOUNT_ID"),
    }

    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    if workspace_id:
        body["workspace_id"] = workspace_id

    # Anthropic expects JSON payload for this grant
    resp = requests.post(
        "https://api.anthropic.com/v1/oauth/token",
        json=body,
        timeout=REQUEST_TIMEOUT,
    )
    raise_for_oauth_error(resp, "Anthropic token exchange")
    return resp.json()


def test_claude_invocation(anthropic_token: str) -> None:
    """Step 5: call the Claude API with the newly minted ephemeral token."""
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "Authorization": f"Bearer {anthropic_token}",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": CLAUDE_MODEL,
            "max_tokens": 1024,
            "output_config": {"effort": "low"},
            "messages": [{"role": "user", "content": "Hello Claude, connection test via Okta WIF. Reply in one sentence."}],
        },
        timeout=60,
    )
    if not resp.ok:
        sys.exit(f"⚠️ Claude API error: HTTP {resp.status_code}\n{resp.text}")

    # The response may contain thinking blocks before the text block
    text = next((b["text"] for b in resp.json()["content"] if b["type"] == "text"), "")
    print("✅ Claude API invocation successful!")
    print(f"Response: {text}")


def main() -> None:
    print("[1/3] Requesting Okta OIDC token using private_key_jwt...")
    okta_access_token = get_okta_access_token()
    print("✅ Okta access token minted.")

    print("\n[2/3] Exchanging Okta token at Anthropic WIF endpoint...")
    token_data = exchange_for_anthropic_token(okta_access_token)
    anthropic_token = token_data.get("access_token")
    expires_in = token_data.get("expires_in", 3600)

    print("✅ Federation successful! Ephemeral Anthropic token acquired.")
    print(f"Token lifetime: {expires_in} seconds.")

    print("\n[3/3] Invoking Claude API with ephemeral token...")
    test_claude_invocation(anthropic_token)


if __name__ == "__main__":
    main()


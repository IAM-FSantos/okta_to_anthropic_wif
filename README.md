# Okta → Anthropic Workload Identity Federation (WIF)

A single-file Python example that calls the Claude API **without a static API key**.

A workload (a script, container or CI job) proves its identity to Okta with a private key. Okta returns a short-lived access token. Anthropic exchanges that token for an ephemeral Anthropic access token, which is then used to call Claude.

```
workload --(1) signed client assertion (private_key_jwt)--> Okta
         <------------(2) Okta access token----------------
         --(3) Okta token + federation rule IDs-----------> Anthropic /v1/oauth/token
         <------------(4) short-lived Anthropic token------
         --(5) Bearer token-------------------------------> Claude Messages API
```

## Why

- **No long-lived secrets.** Nothing like an `sk-ant-api...` key sits in a config file, CI variable or vault.
- **Short-lived credentials.** The Okta client assertion lasts 5 minutes, and the Anthropic token expires on its own.
- **Central control.** Access is granted and revoked in Okta and in the Anthropic federation rule, not by rotating keys.

## Prerequisites

- Python 3.9+
- An Okta org where you can create apps and custom authorization servers
- An Anthropic organization with permission to create service accounts and federation rules

## 1. Configure Okta

1. **Create an API Services app** (Applications → Create App Integration → *API Services*).
   - Client authentication: **Public key / Private key**.
   - Add a public key for the app. You can create the key pair in Okta or yourself; see [Step 2: create `workload_private_key.pem`](#3-run-it).
   - Note the **Client ID**.
2. **Use a custom authorization server** (Security → API → Authorization Servers). You can use `default` or create a new one, and note its **ID**.
   - Add a custom scope, for example `anthropic.access`.
   - Add an access policy and rule that allow the *Client Credentials* grant for your app and that scope.
   - Note the **Issuer URI**; you need it on the Anthropic side.

## 2. Configure Anthropic

In the Anthropic Console, under your organization's settings:

1. Create a **service account** for the workload.
2. Add Okta as a **federation issuer** using the authorization server's Issuer URI.
3. Create a **federation rule** that maps Okta tokens (for example, matching the `sub`/`cid` claim to your Okta client ID and the expected audience) to that service account and the workspace(s) it may use.
4. Note the **federation rule ID**, **organization ID**, **service account ID** and, if needed, the **workspace ID**.

## 3. Run it

**Step 1: clone the repo and install the dependencies**

```bash
git clone https://github.com/IAM-FSantos/okta_to_anthropic_wif.git
cd okta_to_anthropic_wif

python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

**Step 2: create `workload_private_key.pem`**

The script signs its Okta login with this RSA private key. Okta must hold the matching public key. Use either option below.

*Option A: let Okta generate the key pair*

1. In the Okta app, go to **General → Client Credentials → Public Keys → Add key → Generate new key**.
2. Select the **PEM** tab and copy the private key. Okta shows it only once.
3. Save it in the project folder as `workload_private_key.pem`, including the `-----BEGIN PRIVATE KEY-----` and `-----END PRIVATE KEY-----` lines.
4. Save the key in Okta, then copy its **Key ID (kid)** into `OKTA_KEY_ID`.

*Option B: generate the key pair yourself*

This script also needs `joserfc`, which isn't in `requirements.txt` because only key generation uses it:

```bash
pip install joserfc
```

Save this as `generate_key.py` in the project folder, change `KID` to a name of your choice, and run `python generate_key.py`:

```python
import json
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from joserfc.jwk import RSAKey

KID = "my-workload-key-01"  # Unique Key ID (kid); use the same value for OKTA_KEY_ID

# 1. Generate an RSA key pair
private_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)

# 2. Save the private key (PEM). Keep it secure on the workload; never commit it.
private_pem = private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=serialization.NoEncryption(),
).decode("utf-8")
with open("workload_private_key.pem", "w") as f:
    f.write(private_pem)

# 3. Print the public key (JWK) to paste into Okta
key = RSAKey.import_key(private_pem, parameters={"kid": KID, "use": "sig", "alg": "RS256"})
print("Saved private key to workload_private_key.pem")
print("\n--- PUBLIC JWK (paste into Okta) ---")
print(json.dumps(key.as_dict(private=False), indent=2))
```

It writes a 4096-bit private key to `workload_private_key.pem` and prints only the **public** key as a JWK. In the Okta app, go to **Public Keys → Add key**, paste the JWK and save. Then set `OKTA_KEY_ID` to the same `kid`.

Whichever option you use:

- Never share or commit the private key. `.gitignore` already excludes `*.pem` files.
- To keep the key elsewhere, set `OKTA_PRIVATE_KEY_PATH` to its path.

**Step 3: configure `.env`**

```bash
cp .env.example .env    # Windows (cmd): copy .env.example .env
```

Open `.env` and fill in the IDs from the Okta and Anthropic setup above.

**Step 4: run it**

```bash
python main.py
```

Expected output:

```
[1/3] Requesting Okta OIDC token using private_key_jwt...
✅ Okta access token minted.

[2/3] Exchanging Okta token at Anthropic WIF endpoint...
✅ Federation successful! Ephemeral Anthropic token acquired.
Token lifetime: 600 seconds.

[3/3] Invoking Claude API with ephemeral token...
✅ Claude API invocation successful!
Response: ...
```

## Configuration

| Variable | Required | Description |
|---|---|---|
| `OKTA_DOMAIN` | yes | Okta org domain, without `https://` (e.g. `dev-123456.okta.com`) |
| `OKTA_AUTH_SERVER_ID` | yes | Custom authorization server ID (or `default`) |
| `OKTA_CLIENT_ID` | yes | Client ID of the API Services app |
| `OKTA_KEY_ID` | yes | `kid` of the public key registered on the app |
| `OKTA_PRIVATE_KEY_PATH` | no | Path to the PEM private key (default `workload_private_key.pem`) |
| `OKTA_SCOPE` | no | Scope to request (default `anthropic.access`) |
| `ANTHROPIC_FEDERATION_RULE_ID` | yes | Federation rule ID |
| `ANTHROPIC_ORGANIZATION_ID` | yes | Anthropic organization ID |
| `ANTHROPIC_SERVICE_ACCOUNT_ID` | yes | Service account the rule maps to |
| `ANTHROPIC_WORKSPACE_ID` | no | Required when the rule covers more than one workspace |
| `CLAUDE_MODEL` | no | Model for the test call (default `claude-opus-5-5`) |

## Troubleshooting

| Error | Likely cause |
|---|---|
| `Algorithm 'RS256' could not be found` | `cryptography` isn't installed. Run `pip install -r requirements.txt`. |
| Okta `invalid_client` | Wrong `OKTA_KEY_ID`, the private key doesn't match the registered public key, or the app isn't set to private_key_jwt. |
| Okta `invalid_scope` / `access_denied` | The scope doesn't exist on the authorization server, or no access policy rule allows the client credentials grant. |
| Claude API `400` "credit balance is too low" | Federation worked; the organization needs API credits (Console → Plans & Billing). |
| Anthropic `401` on token exchange | The issuer or claims in the Okta token don't match the federation rule, the IDs are wrong, or `ANTHROPIC_WORKSPACE_ID` is missing for a multi-workspace rule. |

## Using the official SDK in production

This script calls the endpoints with `requests` so each step of the flow is visible. In production, the official `anthropic` SDK can do the exchange and token refresh for you. Set `ANTHROPIC_FEDERATION_RULE_ID`, `ANTHROPIC_ORGANIZATION_ID` and `ANTHROPIC_IDENTITY_TOKEN` (or `ANTHROPIC_IDENTITY_TOKEN_FILE`) to the Okta token, then create a plain `anthropic.Anthropic()` client. Make sure `ANTHROPIC_API_KEY` is unset, because it takes precedence.

## Security notes

- `.env` and `*.pem` files are in `.gitignore`. **Never commit your private key.** If one is ever pushed, revoke it in Okta immediately; deleting the commit isn't enough.
- In real deployments, keep the private key in a secrets manager or KMS/HSM, not on disk.
- Scope the federation rule tightly: one service account, the minimum set of workspaces, and exact claim matches.

## License

[MIT](LICENSE)

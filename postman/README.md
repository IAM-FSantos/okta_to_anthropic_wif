# Postman collection: Okta → Anthropic Workload Identity Federation (WIF)

> This folder holds the Postman version of the flow. The [repository README](../README.md) covers the Python version and the full Okta and Anthropic console walkthrough.

Call the Claude API **without an API key**. This collection shows the full machine-to-machine flow end to end: an Okta service app gets a short-lived OIDC token, Anthropic exchanges it for a short-lived Claude API token through Workload Identity Federation, and that token calls Claude.

It is a working reference you can run in Postman to:

- **Learn** how WIF works on the wire, one HTTP call at a time.
- **Validate** an Okta + Anthropic WIF setup before you wire it into an application.
- **Troubleshoot** a failing setup, with scripts that decode tokens and explain errors.

> In production you normally don't make these calls yourself. The Anthropic SDKs (Python, TypeScript, Go, Java, C#, Ruby, PHP) take an Okta token provider, perform the exchange, and refresh the token for you. See [Use WIF with Okta](https://platform.claude.com/docs/en/manage-claude/wif-providers/okta).

---

## Why Workload Identity Federation?

| Long-lived API key (`sk-ant-api...`) | Workload Identity Federation |
| --- | --- |
| A static secret that must be stored, distributed and rotated | No Anthropic secret at all: the workload proves who it is with its IdP |
| Valid until someone revokes it | Tokens live minutes (60 s to 24 h, set per rule) |
| Hard to tie to a specific workload | Each token maps to a federation rule and a service account, and every exchange is logged in the Claude Console |
| A leaked key works from anywhere | A leaked token expires quickly, and Okta access can be cut off centrally |

With `private_key_jwt`, even Okta holds no shared secret: the workload signs its requests with a private key that never leaves it.

---

## How it works

```
 Workload (Postman)              Okta custom auth server            Anthropic API
 ──────────────────              ───────────────────────            ─────────────
 1. Sign client assertion
    (RS256, private key)
        │
        │  POST /oauth2/{authServerId}/v1/token
        │  grant_type=client_credentials
        │  client_assertion=<signed JWT>
        ├──────────────────────────────────►│
        │                                   │ verify signature with the
        │                                   │ app's registered public key
        │◄──────────────────────────────────┤
        │  access_token (JWT)               
        │    iss = https://{domain}/oauth2/{authServerId}
        │    sub = {client_id}
        │    aud = https://api.anthropic.com
        │
        │  2. POST https://api.anthropic.com/v1/oauth/token
        │     grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer
        │     assertion=<Okta JWT>, federation_rule_id, organization_id, workspace_id
        ├──────────────────────────────────────────────────────────────────────►│
        │                                    verify JWT via the issuer's JWKS,  │
        │                                    match the federation rule          │
        │◄──────────────────────────────────────────────────────────────────────┤
        │  access_token = sk-ant-oat01-... (acts as the rule's service account)
        │
        │  3. POST https://api.anthropic.com/v1/messages
        │     Authorization: Bearer sk-ant-oat01-...
        ├──────────────────────────────────────────────────────────────────────►│
        │◄──────────────────────────────────────────────────────────────────────┤
        │  Claude's reply
```

| Step | Request | Standard | Saves |
| --- | --- | --- | --- |
| 1 | **Okta - Mint OIDC Token** | OAuth 2.0 client credentials ([RFC 6749 §4.4](https://www.rfc-editor.org/rfc/rfc6749#section-4.4)) with `private_key_jwt` client authentication ([RFC 7523 §2.2](https://www.rfc-editor.org/rfc/rfc7523#section-2.2)) | `okta_access_token` |
| 2 | **Anthropic WIF - Token Exchange** | JWT bearer grant ([RFC 7523 §2.1](https://www.rfc-editor.org/rfc/rfc7523#section-2.1)) | `anthropic_access_token` |
| 3 | **Claude API - Messages** | Bearer token ([RFC 6750](https://www.rfc-editor.org/rfc/rfc6750)) | nothing |

Each request's documentation explains its parameters, scripts and errors.

---

## Prerequisites

### Okta

1. **API Access Management** enabled, so you can use a **custom authorization server**. The built-in `default` server works. The Okta *org* authorization server (`/oauth2/v1/token`) does **not**: Okta doesn't publish its signing keys, so Anthropic can't verify its tokens.
2. An **API Services** app integration (OIDC, machine-to-machine). Note its **Client ID** (`0oa...`).
3. Client authentication set to **Public key / Private key** (`private_key_jwt`), with your public key registered. Note the key's **Key ID** (`kid`).
4. **DPoP turned off** on the app ("Require Demonstrating Proof of Possession"). This collection doesn't send DPoP proofs.
5. On the custom authorization server:
   - **Audience** set to `https://api.anthropic.com`.
   - A **scope** the app can request, for example `anthropic.access`.
   - An **access policy** with a rule that lets this app use the **Client Credentials** grant for that scope.
   - Optionally, **custom claims** you want the federation rule to match on.

### Anthropic (Claude Console)

Go to **Settings → Workload identity** (`platform.claude.com/settings/workload-identity-federation`), choose **Connect workload → Custom OIDC**, and create:

1. **Federation issuer.** Issuer URL `https://{okta_domain}/oauth2/{okta_auth_server_id}` with JWKS mode `discovery`. It must equal the token's `iss` claim exactly, with no trailing slash.
2. **Service account** (`svac_...`), a member of the target workspace.
3. **Federation rule** (`fdrl_...`) that:
   - matches `subject_prefix` = your Okta **Client ID** (exact, no `*`) and `audience` = `https://api.anthropic.com`;
   - targets the service account;
   - is scoped to one workspace (`wrkspc_...`);
   - uses `oauth_scope` = `workspace:developer` (the same access as a workspace API key);
   - sets `token_lifetime_seconds` (the wizard suggests 600).

Also copy your **Organization ID** (a UUID) from **Settings → Organization**.

### Generate the key pair

The simplest option needs no tools: in the Okta app's **Client Credentials → Public keys** section, choose **Add key → Generate new key**. Okta registers the public key and shows the private key **once**: copy it in PEM format. Its **Key ID** is shown next to it.

To generate your own key instead (any OS with OpenSSL, including Git Bash on Windows):

```bash
# RSA 2048 private key
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out okta-wif-private.pem

# Public key to register on the Okta app (Okta accepts PEM or JWK)
openssl pkey -in okta-wif-private.pem -pubout -out okta-wif-public.pem
```

Both private key formats work as is: **PKCS#8** (`-----BEGIN PRIVATE KEY-----`) and **PKCS#1** (`-----BEGIN RSA PRIVATE KEY-----`). The step 1 script reads both, so you don't need to convert anything or install openssl. Password-protected keys aren't supported.

---

## Setup in Postman

1. **Import the collection.** In Postman, choose **Import** and select [`okta-to-anthropic-wif.postman_collection.json`](okta-to-anthropic-wif.postman_collection.json) from this folder.
2. **Import the environment template.** Import [`okta-to-anthropic-wif.postman_environment.example.json`](okta-to-anthropic-wif.postman_environment.example.json) the same way, then fill in your real values as **current values**. Environment values take precedence over collection values, so the collection keeps its safe placeholders and your real IDs never end up in it.
3. `okta_private_key_pem` is already a **secret**-type variable in the template. Paste the key into its **current value** only. Initial values sync to Postman's cloud and are included when you share or export; current values stay on your machine.
4. Select the environment and **run the collection** (Collection Runner, or send requests 1 → 2 → 3 in order).
5. Open the **Postman Console** (View → Show Postman Console) to see what each script logged: the signed assertion's details, the decoded Okta claims, the token's lifetime and Claude's answer.

> **Don't commit a filled-in environment.** If you export your environment, keep the file outside this repo. `.gitignore` already ignores `*.postman_environment.json` (everything except the `.example` template).

### Run it from the command line (Newman)

The signing script is plain JavaScript with no npm dependency, so the collection also runs in [Newman](https://www.npmjs.com/package/newman), for example in CI:

```bash
npm install -g newman
newman run postman/okta-to-anthropic-wif.postman_collection.json \
  -e my-okta-wif.postman_environment.json
```

Keep `my-okta-wif.postman_environment.json` (your filled-in export) out of version control, or pass values with `--env-var "name=value"` from your CI secret store.

---

## Variables

### You set these

| Variable | Example / format | Required | Description |
| --- | --- | --- | --- |
| `okta_domain` | `your-org.okta.com` | Yes | Your Okta org domain, without `https://`. |
| `okta_auth_server_id` | `default` or `aus...` | Yes | ID of the custom authorization server. |
| `okta_client_id` | `0oa...` | Yes | Client ID of the API Services app. Also the token's `sub` claim. |
| `okta_key_id` | the key's `kid` | Recommended | Key ID of the public key registered on the app. Sent in the assertion's JWT header. |
| `okta_private_key_pem` | `-----BEGIN PRIVATE KEY-----…` | Yes | Unencrypted RSA private key in PEM format, either **PKCS#8** (`BEGIN PRIVATE KEY`) or **PKCS#1** (`BEGIN RSA PRIVATE KEY`). Treat as a secret. |
| `okta_scope` | `anthropic.access` | Yes | Scope granted to the app on the authorization server. |
| `anthropic_organization_id` | UUID | Yes | Your Anthropic organization ID (Console → Settings → Organization). |
| `anthropic_federation_rule_id` | `fdrl_...` | Yes | The federation rule to evaluate. |
| `anthropic_workspace_id` | `wrkspc_...` | Conditional | Workspace to scope the token to. Required when the rule covers more than one workspace; leave it empty to let a single-workspace rule pick its own. |
| `anthropic_service_account_id` | `svac_...` | No | The rule's target service account. The rule decides the account; if you set this, it must match. Leave the placeholder to omit it. |
| `anthropic_version` | `2026-10-21` | Yes | Value of the `anthropic-version` header on the Messages call. |
| `claude_model` | `claude-opus-5-5` | Yes | Model used for the test message. |

### The scripts set these (leave them empty)

| Variable | Set by | Description |
| --- | --- | --- |
| `okta_client_assertion` | Step 1 pre-request | Signed RS256 client assertion (valid 5 minutes, single use). |
| `okta_assertion_error` | Step 1 pre-request | Why signing failed, if it did. Step 1's post-response script prints it too. |
| `okta_access_token` | Step 1 post-response | Okta access token (JWT). Cleared after a successful exchange, because each one can be exchanged only once. |
| `okta_token_expires_at` | Step 1 post-response | Expiry of the Okta token (epoch ms). |
| `anthropic_access_token` | Step 2 post-response | Short-lived Claude API token (`sk-ant-oat01-...`). |
| `anthropic_token_expires_at` | Step 2 post-response | Expiry of the Anthropic token (epoch ms). |

Every run starts by clearing all six, so a failed step can never leave an old token behind.

---

### Variable scopes

The scripts read every value with `pm.variables.get`, so you can keep your values in any scope. When the same name exists in more than one, Postman uses the first one it finds, in this order: **local → data (Runner file) → environment → collection → global**. So:

- An **environment** value overrides the collection placeholder. This is the recommended setup.
- A **global** value is ignored while the collection still has a variable with the same name. Put the value in the collection or an environment instead, or delete the collection variable.
- Postman sends the **current value**. If you edit only the *initial* value of an existing variable, the old current value is still used. Use *Reset* or edit the current value too.

The scripts write their results (`okta_client_assertion`, `okta_assertion_error`, the tokens and expiries) as collection variables.

## Scripts at a glance

| Request | Pre-request | Post-response |
| --- | --- | --- |
| 1. Okta | Checks the variables and rejects placeholders, accepts PKCS#8 and PKCS#1 keys, rejects password-protected keys, and signs the RS256 assertion synchronously in plain JavaScript (no npm package). The flow is at the top of the script and the crypto helpers are at the bottom. It logs the exact reason and skips the request if anything is wrong. | Saves the token, decodes it, checks `iss`, `sub` and `aud` against what Anthropic will verify, and explains Okta errors (`invalid_client`, `invalid_scope`, DPoP...). |
| 2. Anthropic exchange | Checks that a fresh Okta token exists, checks the format of every Anthropic ID, and builds the JSON body, leaving out optional fields that are empty. | Saves the token and its expiry, checks the `sk-ant-oat01-` prefix, and explains 400 and 401 errors. |
| 3. Messages | Checks that the Anthropic token exists and hasn't expired. | Finds the text block (it may come after a thinking block), checks `stop_reason`, and logs the reply and token usage. |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Step 1 skipped: "Set these variables…" | Placeholders still in place | Set the variables in your environment. |
| Step 1 skipped: "…is password-protected" | The key is password-protected | Export it again without a password, or generate a new key in Okta. |
| Step 1 skipped: "Could not sign…" | The PEM is incomplete, isn't an RSA key, or is shorter than 2048 bits | Paste the whole key, including the `BEGIN` and `END` lines. The message gives the `BEGIN` line and length the script read. |
| Okta `400 invalid_client`: "A client_id must be provided" | The request went out without a client assertion | Open the Postman Console (View → Show Postman Console) and read the `[Step 1 · Okta client assertion]` lines. No `Pre-request script started` line means Postman has an old copy of the collection: re-import it. |
| Your value seems ignored | A variable with the same name in a higher-precedence scope wins | See *Variable scopes*. Check the **current** value, not only the initial value. |
| Okta `401 invalid_client` | Public key, `kid` or Client ID mismatch, or the app doesn't use `private_key_jwt` | Re-check the key pair registered on the app, and `okta_key_id`. |
| Okta `400 invalid_scope` | Scope missing or not allowed by the access policy | Add the scope and a policy rule for Client Credentials. |
| Okta `invalid_dpop_proof` / `use_dpop_nonce` | DPoP required on the app | Turn DPoP off for this app. |
| Step 1 test: "decoded JWT payload…" | You used the Okta org authorization server, whose tokens Anthropic can't verify | Use a custom authorization server (`default` or `aus...`). |
| Step 1 test: `aud` | Authorization server audience isn't `https://api.anthropic.com` | Change the audience on the authorization server. |
| Anthropic `401 Authentication failed` | Any assertion check failed (the message is deliberately generic) | Open the [authentication history](https://platform.claude.com/settings/workload-identity-federation?tab=history). It names the reason. |
| …reason `jti_reused` | The same Okta token was exchanged twice | Run step 1 again. The collection clears the Okta token after each exchange. |
| …reason `workspace_id_required` | The rule covers several workspaces | Set `anthropic_workspace_id`. |
| …reason `match_subject_prefix` | `sub` ≠ the rule's `subject_prefix` | Set `subject_prefix` to the exact Client ID (it's case-sensitive). |
| …issuer mismatch | `iss` ≠ the issuer URL | The issuer URL must be `https://{domain}/oauth2/{authServerId}`, byte for byte. |
| Anthropic `400 invalid_request_error` | Missing or malformed field | Check the `fdrl_`, UUID and `wrkspc_` formats. |
| Messages `403` | The rule's `oauth_scope` doesn't allow the endpoint | Use `workspace:developer`. |
| Messages `401` | The Anthropic token expired | Run steps 1 and 2 again. |
| Messages `404` | Unknown model | Set `claude_model` to a current model ID. |

---

## Security notes

- Keep the private key out of version control and out of *initial* values. Use a secret-type variable with a current value only.
- Pin the federation rule to one **exact** Client ID. Every service app on the same Okta authorization server shares one issuer, so a rule without `subject_prefix` would trust all of them.
- Keep token lifetimes short. Anthropic caps the minted token at twice the Okta token's remaining lifetime, and at the rule's `token_lifetime_seconds`.
- Give the service account only the workspace it needs, and review the authentication history regularly.

---

## References

- [Workload Identity Federation overview](https://platform.claude.com/docs/en/manage-claude/workload-identity-federation)
- [Use WIF with Okta](https://platform.claude.com/docs/en/manage-claude/wif-providers/okta)
- [WIF reference: exchange fields, validation rules, errors](https://platform.claude.com/docs/en/manage-claude/wif-reference)
- [Okta: implement OAuth for Okta with a service app](https://developer.okta.com/docs/guides/implement-oauth-for-okta-serviceapp/main/)
- [RFC 7523: JWT profile for OAuth 2.0 client authentication and authorization grants](https://www.rfc-editor.org/rfc/rfc7523)
- [Claude Messages API](https://platform.claude.com/docs/en/api/messages)

*Community reference collection. It isn't an official Anthropic or Okta artifact. Check every configuration against your organization's security standards before using it in production.*

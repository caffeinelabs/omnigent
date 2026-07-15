# GitHub App per-user sandbox authentication

> **Status: implemented.** Config surface: `github:` server-config
> section / `OMNIGENT_GITHUB_APP_*` env. Code:
> `omnigent/server/github_app.py` (config + secret handling, no
> network), `omnigent/server/github_app_client.py` (the httpx client;
> kept separate so secret material and the network sink never share a
> module), `omnigent/server/secretbox.py`,
> `omnigent/server/github_store.py`,
> `omnigent/server/routes/integrations_github.py`, the injection path
> in `omnigent/onboarding/sandboxes/base.py` +
> `omnigent/server/managed_hosts.py`.

## Problem

Managed sandboxes (`host_type="managed"` sessions) authenticate git
over HTTPS with a single **deployment-level** `GIT_TOKEN` baked into
the server environment (Modal secret / k8s secret / env). Every user's
sandbox pushes and clones as that one shared bot account. There is no
way for a sandbox to act as *the human who launched it*: `gh` is
unauthenticated, `git push` is attributed to the shared token, and the
user cannot SSH into their own sandbox.

We want:

1. **GitHub App** authentication (not a classic OAuth App), so the
   deployment installs one app and mints per-user tokens with the
   app's identity.
2. Users **connect their GitHub account from the web UI** (Settings →
   Integrations).
3. When a sandbox is spawned for a session, the **`gh` CLI and git are
   authenticated as the connecting user** inside that sandbox.
4. The user's **public SSH keys** (read from GitHub) are injected into
   the sandbox's `authorized_keys` so they can SSH in with their own
   key.

This is additive: it does not replace OIDC login (which may also use
GitHub as an IdP). When no GitHub App is configured, everything falls
back to the existing shared-`GIT_TOKEN` behaviour.

## GitHub App vs OAuth App

A GitHub App is registered once by the deployment operator. It has:

- an **App ID** and an **RSA private key** — used to sign a short-lived
  RS256 app JWT (`iss = app_id`). The app JWT authenticates *as the
  app* for app-level API calls.
- a **client id / client secret** — used for the **user authorization
  web flow** (user-to-server tokens). The endpoints are the same
  `https://github.com/login/oauth/{authorize,access_token}` used by
  OAuth Apps, but the credential pair belongs to the App.

We use the user authorization flow to obtain a **user access token**
(`ghu_…`) that acts *as the connecting user*. When the App is
configured with expiring user tokens, GitHub also returns a refresh
token (`ghr_…`); we refresh transparently at sandbox-launch time.

## Configuration

`GitHubAppConfig.from_env()` reads:

| Env var | Meaning |
|---|---|
| `OMNIGENT_GITHUB_APP_ID` | Numeric App ID (for the app JWT). Optional — only needed for app-level calls. |
| `OMNIGENT_GITHUB_APP_CLIENT_ID` | **Required.** App client id (`Iv1…`/`Iv23…`). |
| `OMNIGENT_GITHUB_APP_CLIENT_SECRET` | **Required.** App client secret. |
| `OMNIGENT_GITHUB_APP_PRIVATE_KEY` / `_PRIVATE_KEY_PATH` | RSA private key PEM (inline or path). Optional. |
| `OMNIGENT_GITHUB_APP_SLUG` | App slug, used to build the install URL surfaced in the UI. Optional. |
| `OMNIGENT_GITHUB_APP_REDIRECT_URI` | OAuth callback. Defaults to `https://<OMNIGENT_DOMAIN>/v1/integrations/github/callback`. |
| `OMNIGENT_GITHUB_APP_TOKEN_ENC_KEY` | Hex secret (≥32 bytes) used to encrypt stored tokens at rest. Falls back to a key derived from the client secret. |

The feature is **enabled** iff a client id + client secret + a resolvable
redirect URI are present.

## Token storage

`github_connections` table (one row per `(workspace_id, user_id)`):

- `github_login`, `github_user_id`
- `access_token_enc`, `refresh_token_enc` — Fernet-encrypted at rest
  (`omnigent/server/secretbox.py`), never returned over the wire
- `token_expires_at`, `refresh_token_expires_at`, `scopes`
- `created_at`, `updated_at`

`GithubConnectionStore` mirrors `SqlAlchemyAccountStore`: same DB,
server-only surface. The plaintext token is only surfaced through the
dedicated `resolve_sandbox_identity()` path at launch time.

## Connect flow (web UI)

Routes under `/v1/integrations/github` (auth-gated):

- `GET /status` → `{enabled, connected, login, scopes, install_url}`.
- `GET /connect?return_to=…` → 302 to the GitHub authorize URL. State
  is a signed JWT (HS256 over the encryption key) carrying the user id,
  a nonce, and the post-connect return URL, so the callback can't be
  replayed or cross-bound to another user.
- `GET /callback?code&state` → validate state against the session
  user, exchange the code, fetch the GitHub login, upsert the
  connection, redirect back to `return_to`.
- `POST /disconnect` → delete the row.

## Sandbox injection

At managed launch, `sessions.py` resolves the owner's
`SandboxGithubIdentity` (fresh access token — refreshed if expired —
plus the login and the public SSH keys read from GitHub) and threads it
through `launch_managed_host` → `_arm_and_start_host` →
`SandboxLauncher.start_host`.

`start_host` (the shared exec-model default) then, when an identity is
present:

1. Uses the user token as `GIT_TOKEN` for the clone and in the host
   process env (the host image's credential helper + forwarded runner
   env already consume `GIT_TOKEN`/`GIT_USERNAME`), so git acts as the
   user.
2. Writes `~/.config/gh/hosts.yml` with the user's `oauth_token` +
   `user`, so the `gh` CLI is authenticated as the user for every shell
   in the sandbox — independent of env-forwarding rules. The managed
   host image (`deploy/docker/Dockerfile` `--target host`) ships `gh`
   itself so this config is usable out of the box.
3. Appends the user's public keys to `~/.ssh/authorized_keys`
   (`0700` dir, `0600` file). The host image also preinstalls OpenSSH
   and a pinned VS Code Server so Remote-SSH into the sandbox works
   without a cold server download.

When no identity is resolvable (App not configured, user not
connected), `start_host` behaves exactly as before — the shared
`GIT_TOKEN` path is untouched.

## Security notes

- Tokens are encrypted at rest and only decrypted server-side at launch.
- The OAuth `state` is signed and bound to the authenticated user id.
- Injecting the token into the sandbox env is an explicit trade-off: it
  authenticates `gh`/git *as the user* inside the box, which is the
  requested behaviour. Deployments that need the token to never enter
  the sandbox should keep using the credential-proxy model
  (`designs/SANDBOX_CREDENTIAL_PROXY.md`) instead of connecting
  per-user GitHub accounts.
- Only **public** SSH keys are injected (into `authorized_keys`); no
  private key material ever leaves GitHub or the server.

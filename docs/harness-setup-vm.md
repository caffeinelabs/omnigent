# Harness setup on a VM (localhost / EC2 / hosted runner)

How to install Omnigent (caffeinelabs fork) plus the five supported harnesses —
Claude Code, Codex, Pi, Goose, jcode — on a bare VM, and configure everything so
sessions run through our Bifrost gateway where possible. Verified on EC2
(`cafetero`), **Ubuntu 24.04 LTS, x86_64** (any 24.04+ should do; older LTS
releases are untested). This doubles as the SRE-681 exit doc for the VM side;
k8s/hosted-runner specifics are at the end.

Audience: teammates reproducing this setup on their own VM. Nothing here is
secret, but the Bifrost API key goes through 1Password / the team vault, not
this doc.

---

## 0. Prerequisites (Ubuntu 24.04+)

```bash
# node via fnm — needed for the npm-installed harnesses (codex, pi).
# claude, goose, and jcode use native installers and do not need node.
curl -fsSL https://fnm.vercel.app/install | bash
fnm install --lts

# python 3.11+ and uv (Omnigent runs from a venv; 24.04 ships 3.12)
curl -LsSf https://astral.sh/uv/install.sh | sh

# tmux (Omnigent's native TUI wrappers run claude/codex/pi/goose inside tmux)
sudo apt-get install -y tmux git
```

## 1. Install Omnigent (fork, `develop`)

```bash
git clone git@github.com:caffeinelabs/omnigent.git ~/code/omnigent
cd ~/code/omnigent && git checkout develop
uv venv .venv && source .venv/bin/activate
uv pip install -e .
omnigent --help    # sanity
```

For k8s-adjacent work use `uv pip install -e '.[kubernetes]'`.

## 2. Bifrost gateway (shared provider)

Everything non-subscription routes through Bifrost
(`https://bifrost.dev.caffeine.ai/v1`, OpenAI-compatible). It serves both the
chat-completions and the responses wire APIs — Pi/jcode use chat, Codex (which
no longer speaks anything else) uses responses; both verified working through
it. Get the key from the vault, then put it in `~/.omnigent/config.yaml`:

```yaml
providers:
  bifrost:
    default: true          # default provider for harnesses without their own config
    kind: gateway
    openai:
      api_key: <BIFROST_KEY>          # consider env interpolation instead of plaintext
      base_url: https://bifrost.dev.caffeine.ai/v1
      models:
        default: x-ai/grok-4.6     # kimi-k3 remains in the catalog as an alternative
      wire_api: chat
```

The catalog exposed here is curated: only models enabled on the Bifrost side are
visible (see Linear SRE-682 for that change).

## 3. Register this VM as a host (`omnigent login` + `omnigent host`)

Two steps.
First, authenticate (one-time):

```bash
omnigent login https://omni.marko.caffeine.tech
```

Probes the server's auth mode and runs the matching flow (accounts →
user/password prompt, OIDC → browser, header → no-op). Stores the session JWT
in `~/.omnigent/auth_tokens.json` keyed by server URL, **and records the
server as the default** (`server:` key in `~/.omnigent/config.yaml`) — every
later command then targets it without arguments. `host` can run the login flow
itself, but doing `login` first keeps host startup non-interactive.

Then register this machine as a host (foreground; Ctrl-C stops it):

```bash
uv run --env-file .env omnigent host        # server URL comes from config
```

- URL optional after `login` wrote the config default; `omnigent host <url>`
  or `--server <url>` overrides it.
- **`--env-file` is the supported way to load env** — exporting works too, but
  `set -a; source .env; set +a` is the long way around.
- The `.env` content matters: the host forwards an allowlist of its own env
  into runners/harness processes — `ANTHROPIC_*`, `OPENAI_*`,
  `GEMINI_API_KEY`, `GIT_TOKEN`/`GIT_USERNAME`, plus any extra names in
  `OMNIGENT_RUNNER_ENV_PASSTHROUGH` (comma-separated; add
  `JCODE_PROVIDER_BIFROST_API_KEY` so jcode inside runners sees its key).
- `--background` spawns a detached daemon instead. Manage:
  `omnigent host status` / `omnigent host stop` / `omnigent host stop-session`.

## 4. Per-harness setup

Legend: **managed** = Omnigent injects provider config at launch;
**user-owned** = you configure the harness's own files.

### Claude Code — managed (subscription) or via Bifrost

```bash
curl -fsSL https://claude.ai/install.sh | bash   # native installer, preferred over npm
claude                                            # completes OAuth subscription login
```

- Subscription mode (**tested** on this host): `~/.omnigent/config.yaml` gets
  `providers.claude: {kind: subscription, cli: claude, default: true}` —
  Claude talks directly to Anthropic.
- Gateway mode (**tested** 2026-08-14, subscription login left in place):

  ```yaml
  providers:
    claude:
      default: true
      kind: key
      anthropic:
        api_key: bifrost-no-auth        # dev gateway accepts it; real key once auth is enforced
        base_url: https://bifrost.dev.caffeine.ai/anthropic
        models: {default: bedrock/claude-sonnet-4-6}
  ```

  Omnigent injects `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` +
  `ANTHROPIC_MODEL` into the claude terminal at session start (new sessions
  only; running ones keep their process env). The server re-reads
  `config.yaml` at session create — if a new session still shows old
  behavior, restart the Omnigent server first.
- **Model picker shows only what the gateway catalog exposes for the
  anthropic family.** On dev Bifrost today: `bedrock/claude-sonnet-4-6` and
  `bedrock/claude-haiku-4-5-20251001` (no opus). Seeing more models there is
  gateway-side catalog curation (SRE-682), not client config.
- Manual fallback without touching Omnigent config: an `env` block in
  `~/.claude/settings.json` with the same three vars + `ANTHROPIC_SMALL_FAST_MODEL`.
  Host-wide, applies to standalone `claude` too — use only for debugging.

### Codex — managed

```bash
npm install -g @openai/codex          # 0.147.x tested
codex login                           # optional: standalone use only
```

Under Omnigent, **do not** hand-edit `~/.codex/config.toml`: codex-native runs
with a per-session managed `CODEX_HOME` generated from the configured provider
(Bifrost section above).

Wire-API note (verified against `omnigent/inner/codex_executor.py`
`_provider_codex_config_overrides` and a live rollout on this host): codex
≥ 0.137 removed the chat wire from its config schema — a provider block with
`wire_api = "chat"` hard-fails config load — so Omnigent **coerces the
provider's `wire_api: chat` to `"responses"`** in the managed config it
generates. Managed codex sessions therefore always run the responses wire,
and Bifrost serves it: a turn through `bifrost.dev.caffeine.ai/v1` with
`kimi-k3` completed end to end on 2026-08-13 (codex 0.147.0). The mismatch
only remains for genuinely chat-only upstreams (e.g. OpenRouter), where the
coerced config loads but turns fail at request time. If you *do* run codex
standalone against Bifrost, set `wire_api = "responses"` in your own
`~/.codex/config.toml` — it is the only value codex accepts anyway.

### Pi — managed

```bash
npm install -g @earendil-works/pi-coding-agent   # 0.84.x tested
```

- Leave `~/.pi/agent/auth.json` empty. Omnigent writes a per-session managed
  config dir (`PI_CODING_AGENT_DIR`) with `models.json` derived from the
  configured provider, so native Pi sessions authenticate exactly like the rest.
- Known upstream caveat: Pi turn boundaries rely on `agent_settled` (upstream
  omnigent-ai/omnigent#3358); long gateway stalls can leave a Pi session
  looking busy. Restart the session if that happens.

### Goose — user-owned

```bash
curl -fsSL https://github.com/aaif-goose/goose/releases/download/stable/download_cli.sh | CONFIGURE=false bash   # 1.46.x tested
```

The installer drops `goose` into `~/.local/bin`; `CONFIGURE=false` skips its
interactive `goose configure` prompt (we write the config file ourselves
below). On macOS, `brew install --cask block-goose` is the alternative.

Omnigent does **not** manage Goose auth. Edit `~/.config/goose/config.yaml`:

```yaml
OPENAI_HOST: https://bifrost.dev.caffeine.ai
OPENAI_API_KEY: bifrost-no-auth        # accepted as-is by our dev gateway; if Goose ever 401s, put the real Bifrost key here
GOOSE_CONTEXT_LIMIT: 500000   # grok-4.6 window; use 1048576 if you default to kimi-k3
GOOSE_MODE: auto
```

`omnigent goose` launches the TUI in a runner-owned tmux pane.

### jcode — user-owned config + ACP registration

```bash
curl -fsSL https://raw.githubusercontent.com/1jehuang/jcode/main/scripts/install.sh | bash
jcode --version     # expect >= v0.75.5 — earlier releases break ACP MCP sessions (#887)
```

`~/.jcode/config.toml`:

```toml
[provider]
default_provider = "bifrost"
default_model = "x-ai/grok-4.6"

[providers.bifrost]
type = "openai-compatible"
base_url = "https://bifrost.dev.caffeine.ai/v1"
auth = "bearer"
api_key_env = "JCODE_PROVIDER_BIFROST_API_KEY"
default_model = "x-ai/grok-4.6"
requires_api_key = true

[[providers.bifrost.models]]
id = "x-ai/grok-4.6"
context_window = 500000

[[providers.bifrost.models]]
id = "kimi-k3"
context_window = 1048576

# Without this, jcode's ACP profile hides all MCP tools (jcode #829):
[tools]
enabled = ["*"]
```

Put the key where standalone jcode can see it (Omnigent injects it itself when
spawning ACP, but `jcode run`/TUI need it from you):

```bash
echo 'JCODE_PROVIDER_BIFROST_API_KEY=<BIFROST_KEY>' > ~/.jcode/provider-bifrost.env
# …and/or: echo 'export JCODE_PROVIDER_BIFROST_API_KEY=<BIFROST_KEY>' >> ~/.bashrc
```

Register jcode as an ACP agent in `~/.omnigent/config.yaml`:

```yaml
acp:
  agents:
    - name: Jcode
      command: jcode acp
      omnigent_mcp: false    # jcode owns its own MCP config (~/.jcode/mcp.json)
```

Known limitations: no model switching over ACP mid-session (jcode #813), and
re-read instruction files can flush the provider KV cache (jcode #905).

## 5. Resulting `~/.omnigent/config.yaml` (working example from EC2)

This is the tested state: Claude in **gateway mode** (§4), everything else
defaulting to Bifrost. It matches what runs on `cafetero` today.

```yaml
acp:
  agents:
  - command: jcode acp
    name: Jcode
    omnigent_mcp: false
host:
  host_id: <generated>
  name: <your-host-name>
providers:
  bifrost:
    default: true
    kind: gateway
    openai:
      api_key: <BIFROST_KEY>
      base_url: https://bifrost.dev.caffeine.ai/v1
      models: {default: x-ai/grok-4.6}
      wire_api: chat
  claude:
    default: true
    kind: key
    anthropic:
      api_key: bifrost-no-auth
      base_url: https://bifrost.dev.caffeine.ai/anthropic
      models: {default: bedrock/claude-sonnet-4-6}
server: https://omni.<you>.caffeine.tech   # optional: your own Omnigent web endpoint
tui:
  theme: dark
```

## 6. Upgrading

Dev VMs **float**: upgrade harnesses ad hoc to catch upstream breakage early,
and note the last-tested versions in §4 when you do. Hosted-runner (k8s) hosts
are the opposite: harness CLIs come from the host image and are **pinned**
there — bump them by rebuilding `ghcr.io/caffeinelabs/omnigent-host` and
rolling the image through a preview env, never by upgrading inside a live pod.

```bash
# Omnigent itself
cd ~/code/omnigent && git pull --ff-only && uv pip install -e .

# Claude Code (native installer self-update)
claude update

# Codex / Pi (npm)
npm install -g @openai/codex@latest
npm install -g @earendil-works/pi-coding-agent@latest

# Goose (re-run the installer; pin with GOOSE_VERSION=v1.46.0 if needed)
curl -fsSL https://github.com/aaif-goose/goose/releases/download/stable/download_cli.sh | CONFIGURE=false bash

# jcode (built-in self-update)
jcode update
```

After upgrading a harness, rerun its verification row from §8 before declaring
the host healthy — wire-API and auth behavior change upstream without notice
(the codex `wire_api` removal in §4 is the canonical example).

## 7. Hosted runner (k8s) notes

- Runner hosts come from the host image (`ghcr.io/caffeinelabs/omnigent-host`);
  harness CLIs must be baked in or installed on first attach. Preview-env images
  build from `develop` via `gh workflow run preview-env.yml --ref develop`.
- Sandbox pods reach the Bifrost they were configured with; provider keys live
  on the Bifrost side, so runners only need the Bifrost token.
- jcode in k8s currently runs a patched build (see Linear SRE-682 comment) until
  upstream lands the fix; the k8s image work is tracked there.

## 8. Verification matrix (per harness)

Run this after first setup and after any upgrade (§6). "Bottom bar" = the
composer status label in the web UI's session view. Expected labels:
**Claude Code**, **Codex**, **Pi**, **Goose**, **Jcode** — anything else
(e.g. another harness's name) is a bug, not a quirk.

| Check | What to do | Pass looks like |
|---|---|---|
| Simple prompt | New session, send `Reply with exactly: ok` | Turn completes; reply is `ok` |
| Multi-turn + tools | Ask it to write a file, then in a second message read it back | Tool calls execute (approve if prompted); second turn reads the first turn's file |
| Correct UI label | Look at the bottom bar in the session view | Shows this harness's label from the table above |
| Visible in Bifrost logs | Check the Bifrost request log for the turn | Requests appear, routed to the expected model (`x-ai/grok-4.6`, `kimi-k3`) |
| Zombie-session recovery | `omnigent host stop-session <id>` (or close the session), reopen it, send another message | Session relaunches/resumes; no stranded "busy" state (Pi caveat: §4) |

Harness-specific notes:

- **Claude**: the subscription OAuth login must exist even in gateway mode —
  the CLI refuses to start unauthenticated before Omnigent's env injection
  matters.
- **Codex**: if a turn fails instantly with a wire/protocol error, suspect a
  chat-only upstream — see the wire-API note in §4.
- **Pi**: a turn that never settles is the known `agent_settled` caveat (§4),
  not an auth failure.
- **Goose/jcode**: auth is user-owned — if the simple prompt 401s, check
  §4's key locations (`~/.config/goose/config.yaml`,
  `JCODE_PROVIDER_BIFROST_API_KEY`) before touching Omnigent config.

## 9. Keeping this honest

This file describes fork behavior. When harness setup changes, update this doc
and record the delta in `DIFF.md` (required by the fork section of `AGENTS.md`).

# Harness setup on a VM (localhost / EC2 / hosted runner)

How to install Omnigent (caffeinelabs fork) plus the five supported harnesses —
Claude Code, Codex, Pi, Goose, jcode — on a bare VM, and configure everything so
sessions run through our Bifrost gateway where possible. Verified on EC2
(`cafetero`), Ubuntu, x86_64. This doubles as the SRE-681 exit doc for the VM
side; k8s/hosted-runner specifics are at the end.

Audience: teammates reproducing this setup on their own VM. Nothing here is
secret, but the Bifrost API key goes through 1Password / the team vault, not
this doc.

---

## 0. Prerequisites

```bash
# node (via fnm or nvm — needed for claude? no, native installer. Needed for codex + pi)
curl -fsSL https://fnm.vercel.app/install | bash
fnm install --lts

# python 3.11+ and uv (Omnigent runs from a venv)
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
(`https://bifrost.dev.caffeine.ai/v1`, OpenAI-compatible, chat wire API).
Get the key from the vault, then put it in `~/.omnigent/config.yaml`:

```yaml
providers:
  bifrost:
    default: true          # default provider for harnesses without their own config
    kind: gateway
    openai:
      api_key: <BIFROST_KEY>          # consider env interpolation instead of plaintext
      base_url: https://bifrost.dev.caffeine.ai/v1
      models:
        default: kimi-k3
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

- Subscription mode (this host, **tested**): `~/.omnigent/config.yaml` gets
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
(Bifrost section above). If you *do* run codex standalone against Bifrost, note
the current codex only accepts `wire_api = "responses"` — our gateway speaks
chat, so standalone codex needs a translating proxy (LiteLLM) until Bifrost
exposes /responses.

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
brew install block-goose-cli          # or: curl installer from block/goose releases
```

Omnigent does **not** manage Goose auth. Edit `~/.config/goose/config.yaml`:

```yaml
OPENAI_HOST: https://bifrost.dev.caffeine.ai
OPENAI_API_KEY: bifrost-no-auth        # accepted as-is by our dev gateway; if Goose ever 401s, put the real Bifrost key here
GOOSE_CONTEXT_LIMIT: 1048576
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
default_model = "kimi-k3"

[providers.bifrost]
type = "openai-compatible"
base_url = "https://bifrost.dev.caffeine.ai/v1"
auth = "bearer"
api_key_env = "JCODE_PROVIDER_BIFROST_API_KEY"
default_model = "kimi-k3"
requires_api_key = true

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
      models: {default: kimi-k3}
      wire_api: chat
  claude:
    cli: claude
    default: true
    kind: subscription
server: https://omni.<you>.caffeine.tech   # optional: your own Omnigent web endpoint
tui:
  theme: dark
```

## 6. Hosted runner (k8s) notes

- Runner hosts come from the host image (`ghcr.io/caffeinelabs/omnigent-host`);
  harness CLIs must be baked in or installed on first attach. Preview-env images
  build from `develop` via `gh workflow run preview-env.yml --ref develop`.
- Sandbox pods reach the Bifrost they were configured with; provider keys live
  on the Bifrost side, so runners only need the Bifrost token.
- jcode in k8s currently runs a patched build (see Linear SRE-682 comment) until
  upstream lands the fix; the k8s image work is tracked there.

## 7. Smoke test (per harness)

For each harness: create a session in the Omnigent TUI/web, send "print
ok", confirm the turn completes and tool calls (if any) execute. If Goose or
jcode standalone fail auth, check §3 for the key location first.

## 8. Keeping this honest

This file describes fork behavior. When harness setup changes, update this doc
and record the delta in `DIFF.md` (required by the fork section of `AGENTS.md`).

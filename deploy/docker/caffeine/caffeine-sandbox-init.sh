# Sourced by BOTH login (/etc/profile.d) and interactive non-login
# (/etc/bash.bashrc) shells so the sandbox terminal "just works" for dev.
[ -z "${BASH_VERSION:-}" ] && return 2>/dev/null || true
if command -v mise >/dev/null 2>&1 && [ "$(type -t mise 2>/dev/null)" != function ]; then
  eval "$(mise activate bash)"
  # Put mise's shims dir on PATH (in addition to activate). `mise activate`
  # only refreshes PATH at the NEXT shell prompt (via its precmd hook), so a
  # tool installed by a repo's mise.toml `enter` hook (e.g. `mise i`) isn't on
  # PATH for the FIRST command after `cd` — `pnpm: command not found`, then it
  # "works on the 2nd try". The shims dir is STATIC on PATH; `mise i` reshims,
  # so the tool is resolvable immediately. (mise's documented fix for needing a
  # tool before the prompt.)
  case ":$PATH:" in
    *":$HOME/.local/share/mise/shims:"*) ;;
    *) export PATH="$HOME/.local/share/mise/shims:$PATH" ;;
  esac
fi
export COREPACK_ENABLE_DOWNLOAD_PROMPT=0
__ce_appenv="${CAFFEINE_APP_ENV_FILE:-/var/run/caffeine-app-env/app.env}"
if [ "$(id -u)" != 0 ] && [ -f "$__ce_appenv" ]; then
  __ce_tok="$(sed -n 's/^NPM_AUTH_TOKEN=//p' "$__ce_appenv" | head -1)"
  if [ -n "$__ce_tok" ] && ! grep -qs 'npm.pkg.github.com/:_authToken' "${HOME}/.npmrc" 2>/dev/null; then
    printf '//npm.pkg.github.com/:_authToken=%s\n' "$__ce_tok" >> "${HOME}/.npmrc"
  fi
  if [ -n "$__ce_tok" ] && command -v docker >/dev/null 2>&1 && ! grep -qs '"ghcr.io"' "${HOME}/.docker/config.json" 2>/dev/null; then
    printf '%s' "$__ce_tok" | docker login ghcr.io -u token --password-stdin >/dev/null 2>&1
  fi
  unset __ce_tok
fi
unset __ce_appenv

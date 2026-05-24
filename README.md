# codex-shim

Run **Codex Desktop** with any model declared in your
`~/.codex-shim/settings.json` (or any custom JSON file), plus an optional
passthrough to your **ChatGPT subscription's GPT‑5.5** — without recompiling
Codex.

The shim is a small local Python server that pretends to be an OpenAI Responses
API endpoint. Codex points at it; the shim routes each request to whatever
upstream the matching catalog entry uses (OpenAI / Anthropic /
generic-chat-completion-api / AWS Bedrock / ChatGPT subscription).

> Note: the internal Codex provider id is still `factory_byok_shim` for
> backwards compatibility — renaming it would break every existing user's
> `~/.codex/config.toml`. The settings file format is the same one Factory.ai
> uses (this project started as a Factory BYOK adapter), so a Factory
> `settings.json` works as-is if you copy it to `~/.codex-shim/settings.json`.

> Status: developed against Codex Desktop on macOS arm64 (current
> production release). The picker patch targets a specific minified
> filter expression in `app.asar`; if Codex ships a webpack/esbuild
> rebuild that mangles variable names differently the patch may need
> updating — `codex-shim patch-app` will tell you when it can no
> longer find the expected snippet. Linux/Windows users should be
> able to skip the ASAR patch section and use the shim itself
> unchanged.

---

## Why

Codex Desktop only shows the models its server-side Statsig config whitelists.
If you have OpenAI / Anthropic / AWS Bedrock / Z.ai / DeepSeek / Gemini /
OpenRouter keys you'd like to use **as first-class models in the picker**,
this gets you there. It also lets you keep your ChatGPT subscription's
GPT‑5.5 visible alongside everything else.

---

## Install

```bash
git clone https://github.com/<you>/codex-shim ~/Documents/codex-shim
cd ~/Documents/codex-shim
python3 -m pip install --user aiohttp pytest    # only runtime dep is aiohttp
ln -s "$PWD/bin/codex-shim" ~/.local/bin/codex-shim
ln -s "$PWD/bin/codex-app"  ~/.local/bin/codex-app
ln -s "$PWD/bin/codex-model" ~/.local/bin/codex-model
```

Requires Python 3.11+.

---

## Quick start

### 1. Generate the catalog and start the shim

```bash
codex-shim generate          # reads ~/.codex-shim/settings.json, writes catalog
codex-shim start             # background daemon on 127.0.0.1:8765
codex-shim list              # show generated slugs and upstream routes
codex-shim status            # health probe
```

### 2. Point Codex Desktop at it (no global config changes)

```bash
codex-shim app .             # launch Codex with the shim wired in
```

That command applies opt-in `-c` overrides only for this launch. Your
`~/.codex/config.toml` is left untouched. After this Codex Desktop sees every
entry from `~/.codex-shim/settings.json` plus an optional `OpenAI GPT-5.5
(ChatGPT)` slug as picker entries.

If your Codex Desktop's model picker only shows "default" and refuses to render
the catalog entries, you also need the **picker patch** below.

### 3. (Optional) Switch the active Desktop model

```bash
codex-model list
codex-model openai-gpt-5-5    # or any other slug from `list`
codex-app                     # relaunch Codex with new default
```

---

## Custom config file

The shim defaults to `~/.codex-shim/settings.json`. You can point it at any
file:

```bash
codex-shim --settings /path/to/my-models.json generate
codex-shim --settings /path/to/my-models.json start
```

Schema (matches Factory.ai's own `customModels` format, since this project
originated as a Factory BYOK adapter):

```json
{
  "customModels": [
    {
      "model": "gpt-5.5",
      "provider": "openai",
      "baseUrl": "https://api.openai.com/v1",
      "apiKey": "sk-…",
      "displayName": "OpenAI GPT-5.5",
      "maxContextLimit": 400000
    },
    {
      "model": "claude-opus-4-7-20251109",
      "provider": "anthropic",
      "baseUrl": "https://api.anthropic.com/v1",
      "apiKey": "sk-ant-…",
      "displayName": "Claude Opus 4.7"
    },
    {
      "model": "deepseek-v4-pro",
      "provider": "anthropic",
      "baseUrl": "https://api.deepseek.com/anthropic",
      "apiKey": "…",
      "displayName": "DeepSeek V4 Pro",
      "noImageSupport": true
    }
  ]
}
```

The shim **never copies your API keys** into the generated catalog. Keys stay
in your settings file and are read fresh on every request.

### Optional per-entry fields

| field | effect |
|---|---|
| `apiKey` | Bearer token / API key. Required for non-passthrough providers. |
| `displayName` | Picker label. Defaults to `model`. |
| `maxContextLimit` | Override the catalog `context_window`. Defaults vary by model family. |
| `maxOutputTokens` | Cap upstream `max_tokens` (Anthropic / Bedrock). |
| `noImageSupport` | Hide image input from the picker for this entry. |
| `contextBeta1M` | Anthropic 1M-context beta. Only valid for Sonnet 4/4.5 (direct Anthropic *and* Bedrock). Opus rejects it with 400. Pricing roughly doubles past 200K tokens. |
| `systemPromptFile` | Absolute or `~`-relative path to a markdown file holding a custom system prompt. When unset, BYOK entries fall back to a provider-tuned bundled prompt in `codex_shim/prompts/`: `codex_style_anthropic.md` for `anthropic` / `bedrock`, `codex_style_openai.md` for `openai`, `codex_style_generic.md` for `generic-chat-completion-api`. The bundled prompts give BYOK models the `apply_patch` protocol, parallel tool-call discipline, and "act like Codex" working style that Codex Desktop's runtime context does not inject for non-OpenAI models. Re-read every time `codex-shim generate` runs. |
| `extraHeaders` | Extra HTTP headers to send upstream. |

Supported `provider` values:

| provider | upstream API |
|---|---|
| `openai` | OpenAI/`/v1/chat/completions` |
| `generic-chat-completion-api` | OpenAI-shaped chat completions |
| `anthropic` | Anthropic `/v1/messages` |
| `bedrock` | AWS Bedrock `/model/<id>/invoke[-with-response-stream]` (Anthropic-family models only) |

### AWS Bedrock (Claude on Bedrock)

Bedrock entries route to `bedrock-runtime.<region>.amazonaws.com` and only
support Anthropic-family models (Claude). Authentication uses a Bedrock API
key (long-term Bearer token, GA late 2024) — no SigV4 signing, no IAM
credentials. Cross-region inference profile IDs (`us.anthropic.…`) are the
recommended way to pin region routing.

```json
{
  "model": "us.anthropic.claude-sonnet-4-20250514-v1:0",
  "provider": "bedrock",
  "baseUrl": "https://bedrock-runtime.us-east-1.amazonaws.com",
  "apiKey": "<AWS_BEARER_TOKEN_BEDROCK>",
  "displayName": "Claude Sonnet 4 (Bedrock)"
}
```

Both `apiKey` and `baseUrl` are required — the shim does not read AWS env
vars. Region is derived from the host portion of `baseUrl`.

---

## Picker patch for Codex Desktop on macOS

Codex Desktop has a Statsig server-side allowlist (`use_hidden_models: true`)
that hides any model whose slug isn't on a hardcoded list. Custom catalog
entries fall into the hidden bucket and never render in the picker.

**Use the bundled command:**

```bash
codex-shim patch-app
```

This handles the whole flow correctly: copies the bundle into a workdir
(macOS App Management blocks in-place modification of notarized bundles
under /Applications), patches the asar, recomputes the
`ElectronAsarIntegrity` SHA-256, ad-hoc re-signs, and atomically swaps
the patched bundle into /Applications. Original is preserved at
`/Applications/Codex.app.unpatched-<timestamp>` and `codex-shim
restore-app` reverses everything.

If you'd rather do it by hand (or `patch-app` failed because Codex
shipped a webpack rebuild that mangled the picker filter), the manual
flow follows. **Always back up `app.asar` and `Info.plist` before
patching.**

```bash
APP=/Applications/Codex.app
sudo cp -R "$APP" "$APP.unpatched-$(date +%Y%m%d-%H%M%S)"

# 1. Extract the ASAR
cd /tmp && rm -rf codex-asar-patch && mkdir codex-asar-patch && cd codex-asar-patch
npx --yes @electron/asar extract "$APP/Contents/Resources/app.asar" extracted

# 2. Patch the picker filter (this match is single-occurrence, unique to that file)
PATCH_FILE=$(grep -RIl 'useHiddenModels' extracted/webview/assets/model-queries-*.js | head -n1)
sed -i.bak -E 's/let u=c\.useHiddenModels&&o!==`amazonBedrock`,d;/let u=!1,d;/' "$PATCH_FILE"
diff "$PATCH_FILE.bak" "$PATCH_FILE" || true   # confirm exactly one change
rm "$PATCH_FILE.bak"

# 3. Repack
npx --yes @electron/asar pack extracted app.asar.new
sudo cp app.asar.new "$APP/Contents/Resources/app.asar"
```

That alone will crash Codex on next launch with `EXC_BREAKPOINT`. Electron's
`ElectronAsarIntegrity` field in `Info.plist` is a SHA-256 of the **JSON
header** of the asar archive (not the whole file). Recompute it and re-sign:

```bash
# 4. Compute new header hash
HEADER_HASH=$(python3 - "$APP/Contents/Resources/app.asar" <<'PY'
import struct, hashlib, sys
with open(sys.argv[1], 'rb') as f:
    data_size, header_size, _, json_size = struct.unpack('<4I', f.read(16))
    header_json = f.read(json_size)
print(hashlib.sha256(header_json).hexdigest())
PY
)
echo "new header hash: $HEADER_HASH"

# 5. Patch Info.plist (replaces the hash for Resources/app.asar)
sudo /usr/libexec/PlistBuddy -c \
  "Set :ElectronAsarIntegrity:Resources/app.asar:hash $HEADER_HASH" \
  "$APP/Contents/Info.plist"

# 6. Ad-hoc re-sign (drops Apple signature; Gatekeeper will warn once)
sudo codesign --force --deep --sign - "$APP"

# 7. Launch
open "$APP"
```

To roll back: `sudo rm -rf "$APP" && sudo mv "$APP.unpatched-…" "$APP"`.

---

## ChatGPT GPT‑5.5 passthrough (optional)

If you have a ChatGPT plan with Codex access (`~/.codex/auth.json` exists with
`auth_mode: chatgpt`), the shim exposes one synthetic slug `gpt-5.5` (display
name `GPT-5.5`) that proxies straight to
`https://chatgpt.com/backend-api/codex/responses` with your access token,
billing against your ChatGPT subscription quota.

It's added automatically by `codex-shim generate`, so just select it in the
picker. If you'd rather hide it, add an entry with `"model": "gpt-5.5"` to
your `~/.codex-shim/settings.json` — your BYOK entry shadows the synthetic
one and routing goes through whichever upstream you point it at.

---

## How the routing works

```
Codex Desktop ── /v1/responses ──▶ codex-shim (127.0.0.1:8765)
                                     │
                                     ├── slug "gpt-5.5" (and not shadowed by BYOK)
                                     │       └─▶ chatgpt.com/backend-api/codex/responses
                                     │           (Authorization: Bearer <auth.json access_token>)
                                     │
                                     ├── provider "openai" / "generic-…"
                                     │       └─▶ baseUrl/chat/completions
                                     │           (Authorization: Bearer apiKey)
                                     │
                                     ├── provider "anthropic"
                                     │       └─▶ baseUrl/messages
                                     │           (x-api-key: apiKey, anthropic-version: …)
                                     │
                                     └── provider "bedrock" (Anthropic models only)
                                             └─▶ bedrock-runtime.<region>.amazonaws.com
                                                 /model/<id>/invoke[-with-response-stream]
                                                 (Authorization: Bearer <bedrock-api-key>)
```

The shim translates Codex's Responses-API request into the upstream's shape
(chat completions or Anthropic Messages) and translates the streamed reply
back. Extended-thinking blocks from Anthropic-shaped upstreams (Claude,
DeepSeek, GLM) round-trip through `reasoning.encrypted_content` items.

---

## MCP

Codex Desktop forwards three generic MCP tools to every model:

- `list_mcp_resources`
- `list_mcp_resource_templates`
- `read_mcp_resource`

It does **not** flatten individual MCP server tools into the function list.
That's a Codex client behavior, not a shim limitation. Shim-routed models
receive the same MCP tools as built-in OpenAI models. The model is expected
to call `list_mcp_resources` to discover what's available.

---

## Commands

```
codex-shim generate         regenerate catalog/config without starting daemon
codex-shim start            start local shim daemon
codex-shim status           health check + model count
codex-shim stop             stop daemon
codex-shim restart          restart daemon
codex-shim enable           start daemon AND write managed config to ~/.codex
codex-shim disable          stop daemon AND remove managed config from ~/.codex
codex-shim list             list catalog slugs and their upstream routes
codex-shim model list       list slugs currently usable in the picker
codex-shim model use <slug> set the Desktop default model
codex-shim codex -- <args>  exec `codex` CLI through the shim
codex-shim app [path]       launch Codex Desktop through the shim
codex-shim patch-app        patch Codex Desktop's picker to allow custom slugs
codex-shim restore-app      undo patch-app (restore original app bundle)

codex-app [path]            shortcut for `codex-shim app`
codex-model [list|<slug>]   shortcut for `codex-shim model …`
```

All commands accept `--settings <path>` and `--port <port>`.

---

## File layout

```
codex_shim/             python source (server + cli + translation)
bin/codex-shim          main entrypoint
bin/codex-app           shortcut wrapping `codex-shim app`
bin/codex-model         shortcut wrapping `codex-shim model …`
.codex-shim/            generated catalog, config, logs, pid (gitignored)
tests/                  pytest suite
```

The shim never edits `~/.codex/config.toml`. All Codex overrides are passed
inline as `-c key=value` arguments per launch.

---

## License

MIT — see `LICENSE`.

Codex Desktop is a trademark of OpenAI. This project is unaffiliated.

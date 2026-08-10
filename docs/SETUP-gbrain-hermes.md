# Setting up the brain and the assistant

This guide adds the two optional parts on top of a working `cos` install:

- **The brain** (`gbrain`): a local search index of your mail and notes.
  It is what lets the assistant answer "what did we discuss with X in June".
- **The assistant** (`hermes`): the chat agent you talk to, on Telegram and
  in the dashboard.

Plan for a few hours the first time. Nothing here sends your mail anywhere:
the index and its embeddings are computed on your machine. Only the chat
model is hosted, and you pick it.

Every problem we hit during the original install is in
[Problems you may hit](#problems-you-may-hit) at the end, each with its
symptom, cause, and fix. If something behaves strangely, check there first.

## How the parts connect

```
your notes ──┐
             ├──> gbrain (Postgres + local embeddings) ──> hermes ──> you
your mail  ──┘                                              │
                                                       OpenRouter
                                                    (the chat model)
```

`cos` exports your mail as markdown pages. `gbrain` indexes those pages plus
your notes. `hermes` reads the index and talks to you.

## Part 1 — the brain

### 1. Install gbrain and PostgreSQL

```bash
curl -fsSL https://bun.sh/install | bash          # bun runs gbrain
bun install -g github:garrytan/gbrain             # NOT npm — that name is taken
gbrain apply-migrations --yes

brew install postgresql@17 pgvector
brew services start postgresql@17
createdb -p 5435 my_brain                         # check your port in postgresql.conf
```

Use real PostgreSQL, not gbrain's embedded default (PGlite). The embedded
one allows only one writer at a time, so a long first import and the
background refresh block each other.

### 2. Create the brain

```bash
mkdir -p ~/brain && cd ~/brain && git init
gbrain init --postgres "postgresql://$USER@127.0.0.1:5435/my_brain" \
  --embedding-model ollama:nomic-embed-text \
  --embedding-dimensions 768 \
  --expansion-model openrouter:deepseek/deepseek-v4-flash-0731

gbrain config set search.mode balanced
gbrain config set link_resolution.global_basename true
gbrain config set provider_base_urls.ollama http://127.0.0.1:11434/v1
```

Two choices worth knowing about:

- **Embeddings run locally** (Ollama + nomic-embed-text). gbrain's default
  would send your mail to a third-party embedding service. This setup never
  does.
- **The `/v1` suffix on the Ollama URL is required.** Without it, embedding
  fails silently — see problem 3 below.

### 3. Point every model setting at your provider

This step is easy to get wrong, so here it is spelled out: gbrain has many
background phases, and **each one has its own model setting**. Any you skip
falls back to Anthropic and fails if you have no Anthropic key — silently,
in some phases.

```bash
CHEAP=openrouter:deepseek/deepseek-v4-flash-0731   # high volume, mechanical work
STRONG=openrouter:openai/gpt-5.2                   # low volume, judgement

for k in models.tier.utility models.drift models.auto_think \
         models.dream.patterns models.dream.synthesize_verdict \
         models.dream.extract_atoms; do gbrain config set $k $CHEAP; done

for k in models.default models.think models.tier.reasoning \
         models.tier.deep models.dream.synthesize; do gbrain config set $k $STRONG; done

gbrain config set agent.use_gateway_loop true
```

Do not set `models.tier.subagent` — gbrain reverts it to its own default,
and leaving it unset is the supported path.

### 4. Add your notes

Your notes folder must be a git repository — gbrain reads files through git,
so untracked files are invisible to it.

```bash
cd /path/to/your/notes && git init   # if it is not already
# add a .gitignore that tracks markdown only, then: git add -A && git commit

gbrain sources add vault --path /path/to/your/notes
gbrain sources federate vault
gbrain sync --source vault
gbrain extract --stale
gbrain embed --stale
```

### 5. Add your mail

```bash
cos export-brain --since 2025-08-01 --out ~/brain/email
cd ~/brain && git add -A && git commit -qm "email import"
gbrain sync --source default --repo ~/brain
gbrain extract --stale && gbrain embed --stale
```

The export is selective on purpose. It skips newsletters, robot senders,
single messages you never replied to, and messages with no text left after
quotes are stripped. On the original mailbox, 5,836 threads became 2,551
pages.

## Part 2 — the assistant

### 6. Install hermes

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
hermes config set model.provider openrouter
hermes config set model.default deepseek/deepseek-v4-flash-0731
hermes config set model.base_url https://openrouter.ai/api/v1
```

### 7. Lock it down — do not skip this

The assistant reads email written by strangers. A stranger's email can
contain instructions aimed at the assistant. These settings are what keep
such an email from doing anything: a security review found a working path
from "someone emailed you in 2019" to code running on your machine, and
this configuration closes it.

Edit `~/.hermes/config.yaml` **by hand** (see problem 1 — the `hermes
config set` command corrupts list values):

```yaml
agent:
  disabled_toolsets:
    - terminal
    - code_execution
    - browser
    - cronjob
    - skills
    - memory
    - web
    - computer_use
    - delegation
    - image_gen
    - video_gen
    - bfl
    - tts
    - kanban
    - homeassistant
approvals:
  mode: manual          # not "smart" — the automatic approver can be fooled too
  cron_mode: deny
skills:
  write_approval: true
  guard_agent_created: true
  inline_shell: false
memory:
  write_approval: true
```

And in `~/.hermes/.env`:

```bash
HERMES_WRITE_SAFE_ROOT=/Users/YOU/.hermes/agent-workspace
HERMES_YOLO_MODE=false
```

After editing, **open the file and check that `disabled_toolsets` is a real
YAML list** (each item on its own line with a dash). If it is a quoted
string, the lockdown silently does nothing.

### 8. Connect the brain to the assistant

In `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  gbrain:
    command: /Users/YOU/.bun/bin/bun      # bun itself, not the gbrain script
    args:
      - /Users/YOU/.bun/bin/gbrain
      - serve
    env:
      OLLAMA_BASE_URL: http://127.0.0.1:11434/v1
      PATH: /Users/YOU/.bun/bin:/usr/local/bin:/usr/bin:/bin
      OPENROUTER_API_KEY: your-key-here
      GBRAIN_THINK_MAX_OUTPUT_TOKENS: "32000"
      GBRAIN_AI_CHAT_TIMEOUT_MS: "900000"
```

Verify:

```bash
hermes mcp test gbrain     # expect: Connected, ~106 tools
```

### 9. Two settings that prevent silent hangs

```bash
# In ~/.hermes/config.yaml: gateway_timeout: 420
gbrain config set search.token_budget 6000
gbrain config set search.limit_default 12
```

Without these, one oversized search result can grow the conversation until
a model call hangs, and the default timeout is 30 minutes of silence.
Nothing legitimate here takes seven minutes; failing visibly beats waiting.

## Problems you may hit

Every one of these happened during the original install.

**1. The security settings silently do nothing.**
Cause: `hermes config set` stores list values as quoted strings, and the
toolset filter then matches nothing. Fix: edit `~/.hermes/config.yaml` by
hand and confirm list keys are real YAML lists. This same bug breaks
`mcp_servers.*.args`.

**2. The brain connection reports only "Connection closed".**
Cause: the `gbrain` command is a script that needs `bun`, and the process
that launched it has no `bun` on PATH. Fix: invoke `bun` directly with the
gbrain script as its argument, as in step 8.

**3. Embedding reports "Embedded 0 chunks" instead of an error.**
Cause: `OLLAMA_BASE_URL` without the `/v1` suffix — every embed call 404s
and the failure is swallowed. Fix: always `http://127.0.0.1:11434/v1`.

**4. `gbrain sync` syncs the wrong source.**
With more than one source registered, plain `gbrain sync` does not sync the
mail repo. Fix: `gbrain sync --source default --repo ~/brain`.

**5. Deep questions fail wanting an Anthropic key.**
Cause: `think` resolves its model through `models.tier.*`, not `chat_model`.
Fix: step 3 sets every tier; make sure you ran all of it.

**6. `think` returns an empty answer, blaming malformed model output.**
Cause: gbrain caps `think` output at 4,000 tokens for non-Anthropic models,
and some OpenRouter providers spend that whole budget on hidden reasoning —
the answer never starts. Fix: run `scripts/patch-gbrain.sh` (and re-run it
after every `gbrain upgrade`), and keep the two `GBRAIN_*` variables from
step 8 in place. Note from the diagnosis: provider quality varies wildly and
is not predicted by their advertised precision — measure, don't infer.

**7. Background jobs fail with an empty ledger, looking like a quota issue.**
Cause: scheduled jobs read `~/.zshenv` only, so an API key exported in
`~/.zshrc` never reaches them. Fix: export the key from `~/.zshenv`. To
check what a running process actually has:

```bash
ps eww $(pgrep -f "gbrain autopilot") | tr ' ' '\n' | grep -c OPENROUTER_API_KEY
```

**8. Extraction halts with "all provider calls failed" and no reason.**
The per-item errors are computed and then discarded before logging. If you
hit this, add a print of `details.failures` in gbrain's
`extract-atoms-drain.ts` — in our case it immediately printed the real
cause (a missing API key) after hours of guessing. General rule: when a
phase fails opaquely, patch in the error print before theorising.

**9. The bot goes quiet mid-conversation and never answers.**
Symptom: an inbound message in `gateway.log` with no matching response, and
`agent.log` showing input tokens climbing call after call. Cause: an
oversized tool result inflating the conversation until a call hangs. Fix:
the limits in step 9 above.

## One warning to keep

Never run `hermes -z` interactively. That flag bypasses every approval
check by design. The dashboard's question pipeline uses it deliberately —
with the assistant's tools pinned to a read-only set — but as a human
command it removes the protections you just configured.

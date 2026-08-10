#!/usr/bin/env bash
# Re-apply the local gbrain patches. RUN THIS AFTER EVERY `gbrain upgrade`.
#
# Five patches:
#   1. think output cap    — make it overridable (details below)
#   2. drain failure log   — surface the reason extract_atoms failed
#   3. reasoning_effort    — let local thinking models be told not to think
#   4. retrieval width     — page count and per-page excerpt size
#   5. propose_takes model — its own key; THIS IS THE AUTOPILOT HANG FIX
#
# gbrain caps `think` output at 4000 tokens for every model except Anthropic
# Claude 5 (src/core/think/index.ts). The comment there explains exactly why
# that is wrong for reasoning models — they spend the budget on the reasoning
# trace before emitting any answer — but the allowlist is Anthropic-only.
#
# Several OpenRouter providers serve DeepSeek V4 Flash with reasoning enabled.
# Observed on Novita: finish_reason=length, completion_tokens=2500,
# content_len=0, reasoning_len=9081. `think` then reports LLM_OUTPUT_NOT_JSON
# with an empty answer, which reads like a model failure and is not one.
#
# This patch makes the cap overridable via GBRAIN_THINK_MAX_OUTPUT_TOKENS.
# Pair it with the env vars in ~/.zshrc. Output is billed on actual tokens, not
# the cap, so a generous ceiling costs nothing: 32k on deepseek-v4-flash is
# about $0.006 per call.
#
# Idempotent — safe to run when the patch is already applied.
set -euo pipefail

GBRAIN_SRC="$HOME/.bun/install/global/node_modules/gbrain/src"
TARGET="$GBRAIN_SRC/core/think/index.ts"

if [ ! -f "$TARGET" ]; then
  echo "gbrain source not found at $TARGET" >&2
  echo "Is gbrain installed?  bun install -g github:garrytan/gbrain" >&2
  exit 1
fi

# ── Patch 2: make extract_atoms say WHY it failed ──────────────────────────
#
# The drain reports only "all provider calls failed this batch (batches=1,
# remaining=21993)" and doctor reports a 100% halt rate. Neither names a cause,
# and --json / DEBUG=1 / GBRAIN_LOG_LEVEL=debug add nothing. The per-item
# reasons arrive in `details.failures` and are dropped right here. Printing them
# turned a multi-hour dead end into a one-line answer:
# "Anthropic chat requires ANTHROPIC_API_KEY."
DRAIN="$GBRAIN_SRC/core/cycle/extract-atoms-drain.ts"
if [ -f "$DRAIN" ] && ! grep -q "drain-debug" "$DRAIN"; then
  python3 - "$DRAIN" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
s = p.read_text()
old = "        const failures = Array.isArray(d.failures) ? d.failures : [];"
new = (old + "\n"
       "        // LOCAL PATCH — see kiran/scripts/patch-gbrain.sh\n"
       "        if (failures.length) console.error('[drain-debug] ' + JSON.stringify(failures).slice(0, 900));")
if old not in s:
    sys.exit("upstream changed the drain adapter — re-derive patch 2 by hand")
p.write_text(s.replace(old, new))
PY
  echo "✓ patched $DRAIN (drain failure reasons)"
elif [ -f "$DRAIN" ]; then
  echo "✓ drain failure log already patched"
fi

# ── Patch 3: send reasoning_effort to openai-compatible providers ──────────
#
# Extraction runs on Qwen3.5 9B via Ollama. It thinks by default — 529 chars of
# reasoning to answer "OK" at ~5 tok/s — which drops atom extraction from ~7/min
# to under 1/min. Ollama's OpenAI route honours `reasoning_effort` (29s -> 1s
# measured), but gbrain never sends the field and recipes expose no extra-params
# hook. The fetch wrapper in applyOpenAICompatConfig is the least invasive place:
# openai-compatible providers only, only when GBRAIN_OPENAI_REASONING_EFFORT is
# set, and never overriding a value the caller already chose.
GATEWAY="$GBRAIN_SRC/core/ai/gateway.ts"
if [ -f "$GATEWAY" ] && ! grep -q "_kiranReasoningFetch" "$GATEWAY"; then
  python3 - "$GATEWAY" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1]); s = p.read_text()
anchor = "export function applyOpenAICompatConfig("
helper = """// LOCAL PATCH — see kiran/scripts/patch-gbrain.sh
const _KIRAN_EFFORT = process.env.GBRAIN_OPENAI_REASONING_EFFORT;
function _kiranReasoningFetch(recipeId: string, inner?: typeof fetch): typeof fetch | undefined {
  // Scope to the LOCAL provider only. This flag exists to stop Qwen3.5 from
  // thinking during extraction; applying it to OpenRouter would suppress
  // reasoning on the cloud model that answers questions, which is where
  // reasoning is wanted.
  if (!_KIRAN_EFFORT || recipeId !== 'litellm') return inner;
  const base = inner ?? fetch;
  return (async (input: Parameters<typeof fetch>[0], init?: Parameters<typeof fetch>[1]) => {
    if (init && typeof (init as RequestInit).body === 'string') {
      try {
        const body = JSON.parse((init as RequestInit).body as string);
        if (body && Array.isArray(body.messages) && body.reasoning_effort === undefined) {
          body.reasoning_effort = _KIRAN_EFFORT;
          init = { ...(init as RequestInit), body: JSON.stringify(body) };
        }
      } catch {
        // Non-JSON body — pass through untouched.
      }
    }
    return base(input, init);
  }) as typeof fetch;
}

export function applyOpenAICompatConfig("""
old_ret = "  return { baseURL, fetch: recipe.compat?.fetch };"
new_ret = "  return { baseURL, fetch: _kiranReasoningFetch(recipe.id, recipe.compat?.fetch) };"
if anchor not in s or old_ret not in s:
    sys.exit("upstream changed applyOpenAICompatConfig — re-derive patch 3 by hand")
p.write_text(s.replace(anchor, helper, 1).replace(old_ret, new_ret, 1))
PY
  echo "✓ patched $GATEWAY (reasoning_effort injection)"
elif [ -f "$GATEWAY" ]; then
  echo "✓ reasoning_effort already patched"
fi

# ── Patch 4: widen retrieval for large-context models ─────────────────────
#
# gbrain hardcodes 40 pages per answer and 600 chars per page. Both were sized
# for small context windows; DeepSeek V4 Flash 0731 carries 1M tokens.
#
# The excerpt size is the one that matters. Excerpts truncate from the END, so
# at 600 chars the substance of a long page never reaches the model. Measured on
# one real question, 600 -> 3000 took citations from 10 to 18 and surfaced a
# $200k transfer with no signed SOW that the narrow answer missed entirely.
# Page count barely moved (20 -> 22): search only found ~22 relevant pages, so
# the page cap was never the binding constraint.
python3 - "$GBRAIN_SRC" <<'PY'
import pathlib, sys
G = pathlib.Path(sys.argv[1])

g = G/"core/think/gather.ts"; s = g.read_text()
old = "  const gatherLimit = opts.gatherLimit ?? 40;"
new = ("  // LOCAL PATCH — see kiran/scripts/patch-gbrain.sh\n"
       "  const _envGather = Number.parseInt(process.env.GBRAIN_THINK_GATHER_LIMIT || '', 10);\n"
       "  const gatherLimit = opts.gatherLimit ?? (Number.isFinite(_envGather) && _envGather > 0 ? _envGather : 40);")
if "GBRAIN_THINK_GATHER_LIMIT" in s:
    print("  gather limit already patched")
elif old not in s:
    sys.exit("upstream changed gatherLimit — re-derive patch 4 by hand")
else:
    g.write_text(s.replace(old, new, 1)); print("  patched gather.ts (page count)")

i = G/"core/think/index.ts"; s = i.read_text()
old = "  const pagesBlock = renderPagesBlock(gather.pages, 600, opts.question);"
new = ("  // LOCAL PATCH — excerpts truncate from the END; 600 chars hid the substance\n"
       "  // of long pages from the model entirely.\n"
       "  const _envChars = Number.parseInt(process.env.GBRAIN_THINK_EXCERPT_CHARS || '', 10);\n"
       "  const pagesBlock = renderPagesBlock(gather.pages, Number.isFinite(_envChars) && _envChars > 0 ? _envChars : 600, opts.question);")
if "GBRAIN_THINK_EXCERPT_CHARS" in s:
    print("  excerpt size already patched")
elif old not in s:
    sys.exit("upstream changed the renderPagesBlock call — re-derive patch 4 by hand")
else:
    i.write_text(s.replace(old, new, 1)); print("  patched index.ts (excerpt size)")
PY

# ── Patch 5: give propose_takes its own model key ─────────────────────────
#
# THE AUTOPILOT HANG. propose_takes is bulk per-page work — one model call per
# page, thousands of pages — but it resolved its model from `chat_model`, i.e.
# the cloud model that answers questions. Its phase deadline is only checked
# BETWEEN pages, so one slow page blocked the phase until the job's 30-minute
# wall clock killed the entire cycle. The cycle then never reported finishing,
# the dispatcher re-queued it every 150s, and new mail silently stopped reaching
# the brain. Symptom looked like "lint hangs" because lint is simply the first
# phase whose start line is printed.
#
# With its own key (like the other dream phases) it runs on the local model:
#   gbrain config set models.dream.propose_takes litellm:qwen3.5:9b
# Cycle time after this change: 180s, all phases complete.
python3 - "$GBRAIN_SRC" <<'PYEOF'
import pathlib, sys
G = pathlib.Path(sys.argv[1])
p = G/"core/cycle/propose-takes.ts"; s = p.read_text()
old = "    const modelId = opts.model ?? getChatModel();"
new = """    // LOCAL PATCH — see kiran/scripts/patch-gbrain.sh
    let modelId = opts.model ?? getChatModel();
    try {
      const configured = await engine.getConfig('models.dream.propose_takes');
      if (configured) modelId = configured;
    } catch { /* keep chat_model */ }"""
if "models.dream.propose_takes" in s:
    print("  propose_takes model key already patched")
elif old not in s:
    sys.exit("upstream changed the modelId line — re-derive patch 5 by hand")
else:
    p.write_text(s.replace(old, new, 1)); print("  patched propose-takes.ts")
PYEOF

if grep -q "GBRAIN_THINK_MAX_OUTPUT_TOKENS" "$TARGET"; then
  echo "✓ think cap already patched"
  exit 0
fi

cp "$TARGET" "$TARGET.orig"

python3 - "$TARGET" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
s = p.read_text()
old = """export function maxOutputTokensFor(modelStr: string): number {
  return THINKING_BY_DEFAULT_MODEL_RE.test(modelStr)
    ? THINKING_DEFAULT_MAX_OUTPUT_TOKENS
    : DEFAULT_MAX_OUTPUT_TOKENS;
}"""
new = """// LOCAL PATCH — see kiran/scripts/patch-gbrain.sh. The allowlist above is
// Anthropic-only, but the failure it describes is not Anthropic-specific:
// several OpenRouter providers serve DeepSeek V4 Flash with reasoning enabled
// and it spends the whole 4000-token budget on the reasoning trace, returning
// content_len=0 and finish_reason=length.
const ENV_MAX_OUTPUT_TOKENS = Number.parseInt(
  process.env.GBRAIN_THINK_MAX_OUTPUT_TOKENS || '', 10,
);
export function maxOutputTokensFor(modelStr: string): number {
  if (Number.isFinite(ENV_MAX_OUTPUT_TOKENS) && ENV_MAX_OUTPUT_TOKENS > 0) {
    return ENV_MAX_OUTPUT_TOKENS;
  }
  return THINKING_BY_DEFAULT_MODEL_RE.test(modelStr)
    ? THINKING_DEFAULT_MAX_OUTPUT_TOKENS
    : DEFAULT_MAX_OUTPUT_TOKENS;
}"""
if old not in s:
    sys.exit("upstream changed maxOutputTokensFor — re-derive the patch by hand")
p.write_text(s.replace(old, new))
PY

echo "✓ patched $TARGET (original at $TARGET.orig)"
echo "  Ensure these are set — see ~/.zshrc:"
echo "    GBRAIN_THINK_MAX_OUTPUT_TOKENS=32000"
echo "    GBRAIN_AI_CHAT_TIMEOUT_MS=900000"

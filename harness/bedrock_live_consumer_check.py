"""Drive the live Bedrock Converse consumer (_BedrockStream) with a 16 KiB whitespace runaway and
report how many events it read. Run from the checkout under test:
    PYTHONPATH="$REPO" HERMES_HOME="$(mktemp -d)" "$REPO/.venv/bin/python" bedrock_live_consumer_check.py
"""
import agent.chat_completion_helpers as cch
from run_agent import AIAgent

RUNAWAY = ['{"command": "echo hi"'] + [" \n \n"] * 4000
seen = []


def events():
    es = [{"messageStart": {"role": "assistant"}},
          {"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {"toolUseId": "t1", "name": "terminal"}}}}]
    es += [{"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": d}}}} for d in RUNAWAY]
    es += [{"contentBlockStop": {"contentBlockIndex": 0}}, {"messageStop": {"stopReason": "max_tokens"}}]
    for e in es:
        seen.append(e)
        yield e


agent = AIAgent(api_key="k", base_url="https://example.com/v1", model="m", quiet_mode=True,
                skip_context_files=True, skip_memory=True)
agent.api_mode = "bedrock_converse"
cch._bedrock_converse_call = lambda *a, **k: events()
try:
    r = agent._interruptible_streaming_api_call({"modelId": "amazon.nova-pro-v1:0"})
    print("NO RAISE; events consumed", len(seen), "of", len(RUNAWAY) + 4, "finish", r.choices[0].finish_reason,
          "args_len", len(r.choices[0].message.tool_calls[0].function.arguments))
except Exception as e:
    print("RAISED", type(e).__name__, e, "consumed", len(seen))

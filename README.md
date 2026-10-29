# Evidence: tool-argument whitespace runaway on the Anthropic Messages and Bedrock Converse wires

Discovered and measured: **2026-10-29**.

## Patch commits
On `simin-yuan/hermes-agent` branch `fix/tool-args-whitespace-anthropic-bedrock`:
- **`0d96e80c56958e9127a9fd67229f567e6c9cfa24`**: `fix(agent): a whitespace runaway in Anthropic or Bedrock Converse tool input aborts and retries`
- **`7fb8fcf12a2e8cf60dbd65c000d9400697fdf7f2`**: `refactor(agent): pick the Bedrock tool-args watch once per tool block`. No behaviour change.

Base for both: upstream `NousResearch/hermes-agent` branch `openclaw-port/degenerate-tool-args` @ `0a3807ec`. That base adds `ToolArgsWhitespaceWatch` / `DegenerateToolArgumentsError` and wires them into the Responses and chat-completions wires.

## What was measured
A tool call whose streamed arguments degenerate into endless whitespace (`" \n \n" …`).

**On the base:**
- Anthropic Messages (`input_json_delta`) and Bedrock Converse (`contentBlockDelta.toolUse.input`) read the runaway to the output cap.
- Every whitespace delta counts as stream liveness, so no watchdog fires.

**With the patch:**
- Both live consumers abort at a 4,096-char whitespace run, using the same JSON-string semantics.
- Anthropic retries inside the call.
- Bedrock Converse has no in-call stream retry; the turn loop classifies the error as retryable and re-issues the request.
- 5,000 spaces inside a JSON string are not cut on either wire.

Results are in [`RESULTS.md`](RESULTS.md).

## Layout
```
harness/stub.py                          loopback stub: Anthropic Messages SSE and Bedrock ConverseStream (AWS event-stream)
harness/run_ab.sh                        one A/B leg: starts the stub, runs a real `hermes chat`, records rc and total time
harness/verdict.py                       reads the session's state.db: messages, tool calls, tool output
harness/bedrock_live_consumer_check.py   drives the live Bedrock consumer directly with a 16 KiB runaway
runs/<leg>/                              stub.jsonl (stub request log), summary.txt, out.txt, err.txt,
                                         state_db_verdict.txt, agent_log_excerpt.txt
tests/                                   red/green test outputs, wide-run summary and file list
checks/                                  scripts/check outputs
```
Absolute local paths in logs are replaced with `$REPO`, `$WORK` and `$HERMES_HOME`.

## How to rerun
No API keys are needed: every model call goes to the loopback stub, and the credentials in `run_ab.sh` are dummies.

1. Prepare a hermes-agent checkout with a test environment that includes the Bedrock extra. From the checkout:
   ```bash
   python -m pm.build_env --source . --out .venv --group dev --group test --extra bedrock
   ```
2. Check out the build to measure: the base (`0a3807ec`) or the branch (`0d96e80` / `7fb8fcf`).
3. Run each leg **one at a time**. The runner's cleanup stops every idle-exit gateway on the host, so parallel legs kill each other.
   ```bash
   export REPO=/path/to/hermes-agent WORK=/path/to/scratch
   harness/run_ab.sh base_anth  anthropic 18441 60            # on the base checkout
   harness/run_ab.sh base_br    converse  18442 60
   harness/run_ab.sh patch_anth anthropic 18461 60            # on the branch checkout
   harness/run_ab.sh patch_br   converse  18472 60
   harness/run_ab.sh ctrl_anth  anthropic 18481 60 control    # branch, 5,000 spaces inside a string
   harness/run_ab.sh ctrl_br    converse  18482 60 control
   ```
4. Read the verdict for each leg:
   ```bash
   "$REPO/.venv/bin/python" harness/verdict.py "$WORK/runs/<leg>"
   ```
   The stub's request log is `$WORK/runs/<leg>/stub.jsonl`, and the agent log is `$WORK/runs/<leg>/home/logs/agent.log`.
5. Run the tests and checks from the checkout:
   ```bash
   scripts/run_tests.sh tests/agent/test_degenerate_tool_args_native_wires.py tests/agent/test_degenerate_tool_args.py tests/agent/test_bedrock_stream_completion.py -q
   python scripts/check --base 0a3807ec
   ```

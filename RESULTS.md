# Results

All runs from 2026-10-29, with a real `hermes chat -Q -t terminal --yolo -q "run echo hi"` against the loopback stub in `harness/`.
- **Base:** upstream `openclaw-port/degenerate-tool-args` @ `0a3807e`.
- **Branch:** `0d96e80`. The refactor `7fb8fcf` does not change behaviour; tests and checks were re-run at `7fb8fcf`.

## Runaway A/B and control

**Runaway runs:** the first tool request streams `{"command": "echo hi"`, then `" \n \n"` at about 400 chars/s for 60 s, then `stop_reason` max_tokens. Later requests answer normally.

**Control runs:** the first tool request streams `{"command": "echo 'a<5000 spaces>b' | wc -c"}`, 4 chars per delta. That is valid JSON with 5,000 spaces inside a string.

| run dir | wire | build | stub chars written before close | seconds | retry (agent log) | tool ran (state.db) | rc | total s |
|---|---|---|---|---|---|---|---|---|
| `runs/base_anth` | Anthropic Messages | base | 23,273 (no cut) | 60.0 | none | yes, `hi` | 0 | 68.98 |
| `runs/patch_anth` | Anthropic Messages | branch | 4,121 | 10.66 | `Stream drop on attempt 1/3 — retrying … error_type=DegenerateToolArgumentsError` | yes, `hi` | 0 | 21.68 |
| `runs/base_br` | Bedrock Converse | base | 23,237 (no cut) | 60.01 | none | yes, `hi` | 0 | 68.28 |
| `runs/patch_br` | Bedrock Converse | branch | 4,641 | 11.97 | `API call failed (attempt 1/3) error_type=DegenerateToolArgumentsError` → `Retrying API call in 2.56…s (attempt 1/3)` | yes, `hi` | 0 | 23.13 |
| `runs/ctrl_anth` | Anthropic Messages | branch | 5,032 (no cut) | 13.0 | none | yes, `5003` | 0 | 21.85 |
| `runs/ctrl_br` | Bedrock Converse | branch | 5,032 (no cut) | 13.05 | none | yes, `5003` | 0 | 21.37 |

**How to read the table:**
- On both wires the client cuts at a 4,096-char whitespace run.
- The stub-side count also includes the 20-char prefix and whatever the client SDK had read ahead before the socket closed. The read-ahead part is an inference; it was not measured.
- On base, the Anthropic and Bedrock runs both read the full 60 s; the max_tokens truncation path then re-asked and the second request ran the tool.
- Total time includes Hermes startup and shutdown.

**Discarded run:** the first branch run on Bedrock was run in parallel with the Anthropic one. The runner's cleanup stopped every idle-exit gateway on the host, which killed that run (SIGTERM during its retry backoff). It was re-run alone; `runs/patch_br` is the re-run. All runs here were valid, single-leg runs.

## Tests
`tests/agent/test_degenerate_tool_args_native_wires.py` holds 2 tests.

**At base `0a3807e`** (source reverted, tests kept; `tests/red_at_0a3807e.txt`): 2 failed.
- Test 1, Anthropic: the runaway message is returned instead of the retried call.
- Test 2: `TypeError … unexpected keyword argument 'abort_degenerate_args'`.

**Bedrock live consumer, driven directly** (`harness/bedrock_live_consumer_check.py`, output in `tests/bedrock_live_consumer_check.txt`):
- `0a3807e`: `NO RAISE; events consumed 4005 of 4005 finish length args_len 2`
- `7fb8fcf`: `RAISED DegenerateToolArgumentsError … consumed 1027`

**At `7fb8fcf`** (`tests/green_at_7fb8fcf.txt`): 3 files, 5 passed, 0 failed, 2 skipped. The 2 skips are boto3-only cases in `test_bedrock_stream_completion.py`; the runner's environment has no boto3. With boto3 installed, the same files pass with 0 skips.

**Wider run at `0d96e80`** (`tests/wide_run_0d96e80_summary.txt`): 134 files that reference the touched code, `2405 tests passed, 0 failed, 15 skipped`. The file list is `tests/wide_run_file_list.txt`.

## Checks
- `python scripts/check --base 0a3807ec` at `0d96e80`: `12 checks, ok` (`checks/scripts_check_0d96e80_vs_0a3807e.txt`).
- At `7fb8fcf`: `12 checks, ok` (`checks/scripts_check_7fb8fcf_vs_0a3807e.txt`).

Environment: Linux x86_64, Python 3.14.9, boto3 1.43.115, anthropic 0.125.0, httpx 0.28.1.

#!/bin/bash
# usage: run_ab.sh <label> <wire anthropic|converse> <port> <runaway_s>
set -u
LABEL=$1 WIRE=$2 PORT=$3 RS=$4
: "${REPO:?set REPO to the hermes-agent checkout}" "${WORK:?set WORK to a scratch dir}"
D=$WORK/runs/$LABEL; rm -rf $D; mkdir -p $D/home $D/cwd
HARNESS=$(cd "$(dirname "$0")" && pwd); cd "$REPO"
.venv/bin/python "$HARNESS/stub.py" $PORT $D/stub.jsonl $RS ${5:-runaway} & SP=$!
sleep 1
export HERMES_HOME=$D/home
unset ANTHROPIC_API_KEY AWS_PROFILE
if [ $WIRE = anthropic ]; then
  ENVS=(ANTHROPIC_API_KEY=sk-ant-stub-000 ANTHROPIC_BASE_URL=http://127.0.0.1:$PORT)
  ARGS=(--provider anthropic -m claude-sonnet-4-5)
else
  ENVS=(AWS_ACCESS_KEY_ID=AKIASTUB AWS_SECRET_ACCESS_KEY=stub AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1 AWS_ENDPOINT_URL_BEDROCK_RUNTIME=http://127.0.0.1:$PORT AWS_EC2_METADATA_DISABLED=true)
  ARGS=(--provider bedrock -m us.amazon.nova-pro-v1:0)
fi
T0=$(date +%s.%N)
( cd $D/cwd && env "${ENVS[@]}" timeout 400 "$REPO/.venv/bin/python" -m hermes_cli.main chat -Q -t terminal --yolo "${ARGS[@]}" -q "run echo hi" ) > $D/out.txt 2> $D/err.txt
RC=$?
T1=$(date +%s.%N)
kill $SP 2>/dev/null; wait $SP 2>/dev/null
# Stops every idle-exit gateway on the host: run one A/B leg at a time, never in parallel.
for p in $(pgrep -f "idle-exi[t]"); do kill $p; done
echo "rc=$RC total_s=$(echo "$T1 - $T0" | bc)" > $D/summary.txt
cat $D/summary.txt

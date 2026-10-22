#!/usr/bin/env bash
# RQ-Sec security-campaign re-run: fast qwen2.5-7b, attacks ENABLED.
set -u
LMS="$HOME/.lmstudio/bin/lms"
VPY="/Users/huanbui/miniconda3/envs/vesper/bin/python"
REPO="/Users/huanbui/Research/vesper"
cd "$REPO" || exit 1
M="qwen2.5-7b-instruct"
LOG="logs/iotj_seccampaign.log"
echo "[$(date)] SECCAMPAIGN start" | tee "$LOG"
"$LMS" unload --all  >>"$LOG" 2>&1
"$LMS" load "$M" --gpu max -c 4096 >>"$LOG" 2>&1 || { echo "LOAD FAILED" | tee -a "$LOG"; exit 1; }
sleep 5
rm -rf results/vesper_autonomous_eval results/iotj_seccampaign/qwen2.5-7b 2>/dev/null
# --with-smartthings starts the LIVE Docker firmware containers the attack suites
# target; without it the eval auto-disables --with-attacks (0 attacks execute).
"$VPY" -u scripts/run_autonomous_eval.py \
    --model "$M" --skip-model-check \
    --num-scenes 30 --num-days 3 --time-acceleration 60 \
    --headless --allow-fallback-tasks --with-smartthings --with-attacks >>"$LOG" 2>&1
rc=$?
[ -d results/vesper_autonomous_eval ] && mv results/vesper_autonomous_eval results/iotj_seccampaign/qwen2.5-7b
"$LMS" unload --all >>"$LOG" 2>&1
echo "[$(date)] SECCAMPAIGN done (rc=$rc)" | tee -a "$LOG"
touch results/iotj_seccampaign/SEC_DONE

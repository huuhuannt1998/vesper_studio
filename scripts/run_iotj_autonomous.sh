#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# IoT-J multi-model autonomous eval driver (unattended).
#   1. Waits for the VM WiFi jobs (RQ-N1/N2) to finish, to avoid Mac CPU/GPU
#      contention skewing the timing-sensitive 802.11 measurements.
#   2. For each model: unload all -> lms load <model> -> run a clean 30-scene
#      eval against it -> save to results/iotj_auto/<model>/.
#   3. Drops results/iotj_auto/AUTO_DONE when finished.
# Requires: LM Studio running (lms CLI at ~/.lmstudio/bin/lms), conda vesper env.
# ─────────────────────────────────────────────────────────────────────────────
set -u
LMS="$HOME/.lmstudio/bin/lms"
VPY="/Users/huanbui/miniconda3/envs/vesper/bin/python"
REPO="/Users/huanbui/Research/vesper"
cd "$REPO" || exit 1
mkdir -p results/iotj_auto logs
DRV="logs/iotj_auto_driver.log"

# Models to sweep (edit to add/remove). First two = paper models (clean re-run).
MODELS=( "qwen2.5-7b-instruct" "meta-llama-3.1-8b-instruct" "gemma-2-9b-it" "qwen/qwen3.5-9b" )

echo "[$(date)] driver start (PID $$)" | tee -a "$DRV"
echo "[$(date)] waiting for VM WiFi DONE marker to protect timing..." | tee -a "$DRV"
tries=0
while ! multipass exec vesper-vm -- test -f /home/ubuntu/iotj/DONE 2>/dev/null; do
  sleep 300; tries=$((tries+1))
  [ $((tries % 6)) -eq 0 ] && echo "[$(date)] still waiting on WiFi ($((tries*5)) min)" | tee -a "$DRV"
done
echo "[$(date)] WiFi jobs DONE -> starting model sweep" | tee -a "$DRV"

for M in "${MODELS[@]}"; do
  safe=$(echo "$M" | tr '/:' '__')
  echo "[$(date)] ==== MODEL $M ====" | tee -a "$DRV"
  "$LMS" unload --all >> "$DRV" 2>&1
  if ! "$LMS" load "$M" --gpu max -c 4096 >> "$DRV" 2>&1; then
    echo "[$(date)] LOAD FAILED for $M — skipping" | tee -a "$DRV"; continue
  fi
  sleep 5
  echo "[$(date)] running 30-scene eval for $M ..." | tee -a "$DRV"
  rm -rf results/vesper_autonomous_eval 2>/dev/null
  "$VPY" -u scripts/run_autonomous_eval.py \
      --model "$M" --skip-model-check \
      --num-scenes 30 --num-days 3 --time-acceleration 60 \
      --headless --allow-fallback-tasks > "logs/iotj_auto_$safe.log" 2>&1
  rc=$?
  [ -d results/vesper_autonomous_eval ] && mv results/vesper_autonomous_eval "results/iotj_auto/$safe"
  errs=$(grep -icE 'error|traceback|timed out' "logs/iotj_auto_$safe.log" 2>/dev/null)
  echo "[$(date)] DONE $M (rc=$rc, $errs err lines)" | tee -a "$DRV"
done

"$LMS" unload --all >> "$DRV" 2>&1
touch results/iotj_auto/AUTO_DONE
echo "[$(date)] ALL MODELS DONE" | tee -a "$DRV"

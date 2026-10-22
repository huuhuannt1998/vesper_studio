#!/usr/bin/env bash
set -u
LMS="$HOME/.lmstudio/bin/lms"; VPY="/Users/huanbui/miniconda3/envs/vesper/bin/python"
cd /Users/huanbui/Research/vesper || exit 1
LOG=logs/sec_smoke.log
echo "[$(date)] smoke start" > "$LOG"
"$LMS" load qwen2.5-7b-instruct --gpu max -c 4096 >>"$LOG" 2>&1 || echo "load failed" >>"$LOG"
sleep 5
rm -rf results/vesper_autonomous_eval 2>/dev/null
"$VPY" -u scripts/run_autonomous_eval.py \
    --model qwen2.5-7b-instruct --skip-model-check \
    --num-scenes 1 --num-days 1 --time-acceleration 60 \
    --headless --allow-fallback-tasks --with-smartthings --with-attacks >>"$LOG" 2>&1
echo "[$(date)] smoke done rc=$?" >>"$LOG"
touch results/SMOKE_DONE

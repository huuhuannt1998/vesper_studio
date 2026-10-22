#!/usr/bin/env bash
# Post-regen: export the regenerated raw episode dirs, verify non-zero network
# features, replace the biased episodes in the dataset, rebuild splits + baselines.
set -euo pipefail
PY=/Users/huanbui/miniconda3/envs/vesper/bin/python
cd /Users/huanbui/Research/vesper

echo "=== 1. export regen raw dirs -> /tmp/regen_out ==="
rm -rf /tmp/regen_out
$PY -c "import sys;sys.path.insert(0,'scripts');from dataset.build_dataset import build;build('results/vesper_sh_regen','/tmp/regen_out')"

echo "=== 2. verify non-zero net, then replace in dataset ==="
$PY - <<'PYEOF'
import pandas as pd, glob, os, shutil
ok=0; bad=[]
for d in sorted(glob.glob('/tmp/regen_out/episodes/*')):
    w=pd.read_parquet(f'{d}/windows.parquet')
    nc=[c for c in w.columns if c.startswith('net_')]
    if nc and w[nc].abs().sum().sum()>0:
        name=os.path.basename(d)
        shutil.rmtree(f'results/vesper_sh/episodes/{name}',ignore_errors=True)
        shutil.copytree(d,f'results/vesper_sh/episodes/{name}')
        ok+=1
    else:
        bad.append(os.path.basename(d))
print(f'  replaced {ok} episodes; zero-net (NOT replaced): {bad}')
PYEOF

echo "=== 3. rebuild splits + baselines ==="
$PY -c "import sys;sys.path.insert(0,'scripts');from dataset.run_baselines import main;import json;r=main('results/vesper_sh');print(json.dumps({'macro_f1':r['random_forest']['macro_f1'],'n_train':r['n_train'],'n_test':r['n_test'],'rf_per_class':{k:round(v['f1'],3) for k,v in r['random_forest']['per_class'].items()},'if_recall':r['isolation_forest']['per_attack_recall'],'if_benign_fpr':r['isolation_forest']['benign_fpr']},indent=2))"

echo "=== 4. confirm ZERO zero-net episodes remain (60/60 multimodal) ==="
$PY - <<'PYEOF'
import pandas as pd, glob, os
tot=0; z=[]
for d in sorted(glob.glob('results/vesper_sh/episodes/*')):
    w=pd.read_parquet(f'{d}/windows.parquet'); tot+=1
    nc=[c for c in w.columns if c.startswith('net_')]
    if not (nc and w[nc].abs().sum().sum()>0): z.append(os.path.basename(d))
print(f'  episodes={tot} with-network={tot-len(z)} zero-net={len(z)} {z[:6]}')
# by-resident network coverage check (the bias fix)
import collections
cov=collections.Counter()
for d in sorted(glob.glob('results/vesper_sh/episodes/*')):
    w=pd.read_parquet(f'{d}/windows.parquet'); nc=[c for c in w.columns if c.startswith('net_')]
    model=os.path.basename(d).split('__')[1]
    if nc and w[nc].abs().sum().sum()>0: cov[model]+=1
print('  per-resident with network:', dict(cov))
PYEOF
echo "REBUILD DONE"

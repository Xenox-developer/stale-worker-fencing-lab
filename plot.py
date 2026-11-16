"""Render factual metrics from the saved independent audit; no inference."""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
data = json.loads((ROOT / 'results/audit.json').read_text())
assert data['passed'] and data['audited_trials'] == 72
modes = ['lease_only', 'precheck', 'fenced']
labels = ['Lease acquisition only', 'Separate ownership check', 'Storage-side fencing']
metrics = [('stale_commit', 'Stale commits', 'handoffs'),
           ('replacement_success', 'Replacement commits', 'handoffs'),
           ('clean_success', 'Clean commits', 'clean')]
plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 15,
                     'svg.fonttype': 'none'})
fig, axes = plt.subplots(1, 3, figsize=(14, 5), layout='constrained')
fig.set_facecolor('white')
for ax, (metric, title, denominator) in zip(axes, metrics):
    values = [data['summary'][m][metric] for m in modes]
    denoms = [data['summary'][m][denominator] for m in modes]
    ax.barh(range(3), values, color=['#44546A', '#0072B2', '#8B4513'], height=0.58)
    ax.set_yticks(range(3), labels if metric == 'stale_commit' else [''] * 3)
    ax.invert_yaxis()
    max_n = max(denoms)
    ax.set_xlim(0, max_n * 1.23)
    ax.set_xticks([0, max_n / 2, max_n], [str(0), str(max_n // 2), str(max_n)])
    ax.set_title(title, fontweight='bold', pad=17)
    ax.set_xlabel('Trials')
    for i, (value, n) in enumerate(zip(values, denoms)):
        ax.text(value + max_n * .035, i, f'{value}/{n}', va='center', fontsize=16)
    ax.spines[['top', 'right']].set_visible(False)
    ax.grid(axis='x', alpha=.18)
    ax.set_axisbelow(True)
for suffix in ['png', 'svg']:
    fig.savefig(ROOT / f'results/fencing.{suffix}', dpi=170, facecolor='white')
plt.close(fig)

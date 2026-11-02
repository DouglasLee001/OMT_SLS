#!/usr/bin/env bash
# Submit with: sbatch script/run_strict_bound_server.sh
# Recheck all 156 design/portfolio B incumbents from the frozen source run.
#SBATCH --job-name=z3-bound156
#SBATCH --partition=all
#SBATCH --nodelist=node1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=180
#SBATCH --exclusive
#SBATCH --mem=0
#SBATCH --time=01:30:00
#SBATCH --output=/data/shared/lbh/OMT/result/strict_bound/slurm-bound156-%j.out

set -euo pipefail
TASK_ROOT=/home/lbh/OMT
exec bash "$TASK_ROOT/script/run_strict_bound.sh" \
  --prepared /data/shared/lbh/OMT/result/strict_bound/queries156_design_portfolio_NIA_20261031_160915_094021 \
  --expected-count 156 \
  --solver "$TASK_ROOT/competitor/z3-5.1.0/build/z3" \
  --jobs 180 --timeout 3600

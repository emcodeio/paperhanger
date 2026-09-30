#!/bin/zsh -u
# Event lines for a watcher (one line = one event) while a long run goes:
#   watch_run.sh <run-dir> [logname=run] [tmux-session=ph-run]
# Polls progress.py every 120 s and prints only what is worth acting on: every 10% of
# estimated time, the end of the photos that need no model, a new failure, a stall, disk
# under 20 GB, critical memory pressure, the session vanishing without EXIT, or EXIT itself
# (then exits). Milestones already reported are kept in <run-dir>/.watch-state-<logname>,
# so a restarted watcher does not repeat them. tmux is found on PATH.

here=${0:A:h}
run_dir=${1:A} log=${2:-run} sess=${3:-ph-run}
state=$run_dir/.watch-state-$log
touch $state
seen() { grep -qxF -- "$1" $state }
mark() { print -r -- "$1" >> $state }

while true; do
  j=$(uv run --quiet python $here/progress.py --log $run_dir/$log.log --input $run_dir/input \
        --originals $run_dir/processing/originals 2>&1) \
    || { print "WATCH progress.py error: ${j[1,200]}"; sleep 120; continue }
  line=$(print -r -- $j | python3 -c '
import json,sys
d=json.load(sys.stdin)
pct,eta,rate=d["pct_time"],d["eta_s"],d["rate"]
eta_s=f"{eta/3600:.1f} h left" if eta is not None else "eta n/a"
rate_s=f"x{rate:.2f} of estimate" if rate else ""
dp,tp,fg=d["done_photos"],d["total_photos"],d["free_gb"]
print("BASE\t"+f"{dp}/{tp} photos, {pct}% of est. time, {eta_s} {rate_s}, {fg} GB free")
print("PCT\t"+str(int(pct//10*10)))
print("FREEPHASE\t"+("1" if d["current_est_s"]>10 or d["exit"] is not None else "0"))
for f in d["failures"]: print("FAIL\t"+f)
if d["stalled"]: print("STALL\t"+str(d["current"])+" log idle "+str(d["log_age_s"])+"s")
if d["free_gb"]<20: print("DISK\t"+str(d["free_gb"]))
if d["memory_pressure"]=="critical": print("MEM\tcritical")
if d["exit"] is not None: print("EXIT\t"+str(d["exit"]))
')
  base=$(print -r -- $line | awk -F'\t' '$1=="BASE"{print $2}')
  print -r -- $line | while IFS=$'\t' read -r kind val; do
    case $kind in
      PCT)       (( val >= 10 )) && ! seen "PCT $val" && { mark "PCT $val"; print "MILESTONE ${val}%: $base" } ;;
      FREEPHASE) [[ $val == 1 ]] && ! seen FREEPHASE && { mark FREEPHASE; print "FREE OUTPUTS DONE: $base" } ;;
      FAIL)      ! seen "FAIL $val" && { mark "FAIL $val"; print "FAILURE: $val" } ;;
      STALL)     print "STALL: $val | $base" ;;
      DISK)      print "LOW DISK: $val GB free" ;;
      MEM)       print "MEMORY PRESSURE CRITICAL | $base" ;;
      EXIT)      print "EXIT=$val: $base"; exit 0 ;;
    esac
  done
  [[ -e $run_dir/$log.exit ]] && { print "EXIT file: $(cat $run_dir/$log.exit) | $base"; exit 0 }
  tmux has-session -t $sess 2>/dev/null || { sleep 5; [[ -e $run_dir/$log.exit ]] || { print "SESSION $sess GONE without EXIT | $base"; exit 1 } }
  sleep 120
done

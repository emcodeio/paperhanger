#!/bin/zsh -u
# Launch one long paperhanger run safely. Meant to run inside tmux so it outlives the terminal:
#
#   tmux new-session -d -s ph-run -- /path/to/library/run_paperhanger.sh <run-dir> <input-subdir> <logname>
#
# <run-dir>/<input-subdir> is the input; <run-dir>/processing the processing directory;
# the log goes to <run-dir>/<logname>.log and paperhanger's own exit code to <logname>.exit
# (0 ok, 1 never started, 2 some photos failed, 130 interrupted).
#
# What it guards against, each learned on the first whole-library run:
#   - two runs sharing one processing dir: a mkdir lock (<run-dir>/.lock);
#   - sleep: caffeinate -ims for exactly the life of this script;
#   - a log that lags by kilobytes: paperhanger prints without flushing, so PYTHONUNBUFFERED;
#   - the exit code tee would report instead of paperhanger's: captured inside the braces;
#   - Ctrl-C killing tee before paperhanger prints its summary: tee -i, and a no-op INT trap.
#
# Stop a run only with:  tmux send-keys -t ph-run C-c
# Killing the tmux session sends SIGHUP, which paperhanger does not handle, and leaves its
# .work/ scratch behind; after a hard kill, remove <run-dir>/processing/.work once .lock is gone.

(( $# == 3 )) || { print -u2 "usage: run_paperhanger.sh <run-dir> <input-subdir> <logname>"; exit 2 }
run_dir=${1:A} in=$2 log=$3
[[ -d $run_dir/$in ]] || { print -u2 "no such input dir: $run_dir/$in"; exit 2 }
ph=$(command -v paperhanger) || { print -u2 "paperhanger is not on PATH (uv tool install .)"; exit 2 }
repo=${0:A:h:h}
cd $run_dir || exit 1

mkdir $run_dir/.lock 2>/dev/null || { print -u2 "another run holds $run_dir/.lock"; exit 1 }
trap 'rmdir $run_dir/.lock 2>/dev/null' EXIT
trap ':' INT

export PYTHONUNBUFFERED=1
/usr/bin/caffeinate -ims -w $$ &

{
  print "START $(date +%FT%T) sha=$(git -C $repo rev-parse --short HEAD 2>/dev/null) input=$in"
  $ph -b --processing-dir $run_dir/processing $run_dir/$in
  rc=$?
  print "EXIT=$rc $(date +%FT%T)"
  print $rc > $run_dir/$log.exit
} 2>&1 | /usr/bin/tee -i -a $run_dir/$log.log

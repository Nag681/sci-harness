#!/bin/sh
# Replays the README's example, a Rust crate that gets p50/p99 latency, with this checkout's sci and prints every
# command with its real output. The agent's file edits are scripted. Needs git, python3 and cargo.
# Usage: sh .github/readme/demo.sh > demo.txt
set -eu
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 PYTHONDONTWRITEBYTECODE=1 CARGO_TERM_COLOR=never
LAST=$WORK/last.txt

x() {  # print a command as a terminal shows it, then run it (sci exits non-zero on FAIL, which is expected)
    printf '\n$ %s\n' "$1"
    sh -c "$1" >"$LAST" 2>&1 || true
    cat "$LAST"
}
edit() { printf '\n# the agent edits %s: %s\n' "$1" "$2"; cat >"$1"; }
edit_diff() {  # like edit, and prints the change as a diff
    cp "$1" "$WORK/before"
    edit "$1" "$2"
    diff -U1 "$WORK/before" "$1" | tail -n +3 || true
}
scene() { printf '\n\n===== %s =====\n' "$1"; }

stats() {  # src/stats.rs: $1 = the body of percentile() (none: no percentile yet), $2 = the tests to add
    cat <<'EOF'
//! Statistics over request latencies, in milliseconds.

/// The mean latency, or 0.0 when there are no samples.
pub fn mean(samples: &[u64]) -> f64 {
    if samples.is_empty() {
        return 0.0;
    }
    samples.iter().sum::<u64>() as f64 / samples.len() as f64
}
EOF
    [ -z "$1" ] || printf '%s\n' "$1"
    cat <<'EOF'

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mean_of_three() {
        assert_eq!(mean(&[10, 20, 30]), 20.0);
    }
EOF
    [ -z "$2" ] || printf '%s\n' "$2"
    printf '}\n'
}

pct() {  # percentile() with the 0-based index computed by $1
    cat <<EOF

/// The p-th percentile of the samples (nearest-rank method).
pub fn percentile(samples: &[u64], p: usize) -> u64 {
    if samples.is_empty() {
        return 0;
    }
    let mut sorted = samples.to_vec();
    sorted.sort_unstable();
    sorted[$1]
}
EOF
}

P99_TESTS='
    #[test]
    fn p99_of_100_samples() {
        let samples: Vec<u64> = (1..=100).rev().collect();
        assert_eq!(percentile(&samples, 99), 99);
    }

    #[test]
    fn p99_of_10_samples_is_the_slowest() {
        let samples: Vec<u64> = (1..=10).collect();
        assert_eq!(percentile(&samples, 99), 10);
    }'
P99_HIDDEN='
    #[test]
    fn p99_of_100_samples() {
        let samples: Vec<u64> = (1..=100).rev().collect();
        assert_eq!(percentile(&samples, 99), 99);
    }

    #[test]
    #[ignore = "flaky"]
    fn p99_of_10_samples_is_the_slowest() {
        let samples: Vec<u64> = (1..=10).collect();
        assert_eq!(percentile(&samples, 99), 10);
    }'

rank_fn() {  # T2's helper: the 1-based nearest rank, computed by $1
    cat <<EOF

/// The 1-based nearest rank of the p-th percentile among n samples.
fn rank(n: usize, p: usize) -> usize {
    ($1).max(1)
}

/// The p-th percentile of the samples (nearest-rank method).
pub fn percentile(samples: &[u64], p: usize) -> u64 {
    if samples.is_empty() {
        return 0;
    }
    let mut sorted = samples.to_vec();
    sorted.sort_unstable();
    sorted[rank(sorted.len(), p) - 1]
}

/// The median latency: the 50th percentile.
pub fn median(samples: &[u64]) -> u64 {
    percentile(samples, 50)
}
EOF
}

main() {
    mkdir "$WORK/latency" && cd "$WORK/latency"
    git init -q -b main
    git config user.name "Demo" && git config user.email "demo@example.com"
    printf '/target\n' >.gitignore
    printf '[package]\nname = "latency"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n' >Cargo.toml
    mkdir src
    printf '//! Latency statistics for the service dashboard.\npub mod stats;\n' >src/lib.rs
    stats "" "" >src/stats.rs
    cargo generate-lockfile -q
    git add -A && git commit -q -m "latency crate"

    scene "start"
    printf '\n$ python3 ~/.local/share/agent-skills/sci/scripts/sci.py init --objective "Show p50 and p99 latency on the dashboard"\n'
    python3 "$ROOT/scripts/sci.py" init --objective "Show p50 and p99 latency on the dashboard" 2>&1 || true
    x 'git switch -c sci/p99'

    scene "plan"
    x '.sci/sci note "stats.rs has mean() only; latencies are u64 milliseconds" --kind code'
    x '.sci/sci baseline'
    x '.sci/sci task add "Add percentile() to stats" --verify "cargo test -q stats::tests" --scope src/stats.rs --inv "a percentile is always one of the samples"'
    x '.sci/sci task add "Show p50 and p99 on the dashboard line" --verify "cargo test -q dashboard::tests" --scope src/dashboard.rs --scope src/lib.rs --scope src/stats.rs --dep T1'

    scene "T1: the test first"
    x '.sci/sci task start'
    stats "" "$P99_TESTS" | edit src/stats.rs "two tests for percentile(), before any code"
    x '.sci/sci check'
    x '.sci/sci lock'

    scene "T1: fix 1"
    stats "$(pct 'sorted.len() * p / 100')" "$P99_TESTS" | edit src/stats.rs "index = n * p / 100"
    x '.sci/sci check'

    scene "the agent's context is compacted"
    x '.sci/sci resume'

    scene "T1: fix 2"
    stats "$(pct 'sorted.len() * p / 100 - 1')" "$P99_TESTS" | edit src/stats.rs "index = n * p / 100 - 1"
    x '.sci/sci check'

    scene "T1: the agent hides the failing test"
    stats "$(pct 'sorted.len() * p / 100 - 1')" "$P99_HIDDEN" | edit_diff src/stats.rs "#[ignore] on the test that fails"
    x '.sci/sci check'
    x '.sci/sci restore --paths src/stats.rs'

    scene "T1: fix 3"
    stats "$(pct '(sorted.len() - 1) * p / 100')" "$P99_TESTS" | edit src/stats.rs "index = (n - 1) * p / 100"
    x '.sci/sci check'

    scene "T1: STOP, guesses and an experiment"
    x '.sci/sci hyp add "percentile() picks from the samples before they are sorted"'
    x '.sci/sci hyp add "the rank n * p / 100 is rounded down; nearest-rank rounds it up"'
    mkdir -p .sci/scratch
    edit .sci/scratch/probe.py "an experiment on sorted samples" <<'EOF'
import math

for n, want in ((100, 99), (10, 10)):
    s = list(range(1, n + 1))  # sorted
    rank = n * 99 / 100
    down, up = math.floor(rank), math.ceil(rank)
    print(f"n={n}: rank {rank} | rounded down: #{down} = {s[down - 1]} | rounded up: #{up} = {s[up - 1]} | want {want}")
EOF
    x ".sci/sci run 'python3 .sci/scratch/probe.py'"
    run=$(sed -n 's/^run \([0-9]*\):.*/\1/p' "$LAST" | head -n 1)
    x ".sci/sci hyp refute H1 'run $run: on sorted samples, rounding the rank down still picks 9 instead of 10'"
    x ".sci/sci hyp confirm H2 'run $run: rounding the rank up picks 99 and 10, both right'"

    scene "T1: the fix the evidence supports"
    stats "$(pct '(sorted.len() * p).div_ceil(100).max(1) - 1')" "$P99_TESTS" | edit src/stats.rs "round the rank up, then the 0-based index"
    x '.sci/sci check'
    x '.sci/sci verify'
    x '.sci/sci done -m "feat: percentile() with the nearest-rank method"'

    scene "T2"
    x '.sci/sci task start'
    edit src/dashboard.rs "the acceptance test" <<'EOF'
//! The latency line on the service dashboard.

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn summary_shows_p50_and_p99() {
        let samples: Vec<u64> = (1..=100).collect();
        assert_eq!(summary(&samples), "n=100 mean=50.5ms p50=50ms p99=99ms");
    }
}
EOF
    printf '//! Latency statistics for the service dashboard.\npub mod dashboard;\npub mod stats;\n' >src/lib.rs
    x '.sci/sci check'
    x '.sci/sci lock'
    cat >"$WORK/summary.rs" <<'EOF'
//! The latency line on the service dashboard.
use crate::stats::{mean, median, percentile};

/// The dashboard's latency line: count, mean, p50 and p99.
pub fn summary(samples: &[u64]) -> String {
    format!("n={} mean={:.1}ms p50={}ms p99={}ms", samples.len(), mean(samples), median(samples),
            percentile(samples, 99))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn summary_shows_p50_and_p99() {
        let samples: Vec<u64> = (1..=100).collect();
        assert_eq!(summary(&samples), "n=100 mean=50.5ms p50=50ms p99=99ms");
    }
}
EOF
    edit src/dashboard.rs "summary()" <"$WORK/summary.rs"
    stats "$(rank_fn 'n * p / 100')" "$P99_TESTS" | edit src/stats.rs "median(), and a rank() helper shared with percentile()"
    x '.sci/sci check'
    x '.sci/sci verify'
    stats "$(rank_fn '(n * p).div_ceil(100)')" "$P99_TESTS" | edit src/stats.rs "rank() rounds up again"
    x '.sci/sci check'
    x '.sci/sci verify'
    x '.sci/sci done -m "feat: p50 and p99 on the dashboard line"'

    scene "the end"
    x 'git log --oneline'
    x '.sci/sci report'
}

main 2>&1 | sed "s#$WORK#~#g"

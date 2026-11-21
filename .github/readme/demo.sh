#!/bin/sh
# Replays the README's bill-splitter example with this checkout's sci and prints every command with its real
# output. The agent's file edits are scripted. Usage: sh .github/readme/demo.sh > demo.txt
set -eu
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 PYTHONDONTWRITEBYTECODE=1
LAST=$WORK/last.txt

x() {  # print a command as a terminal shows it, then run it (sci exits non-zero on FAIL, which is expected)
    printf '\n$ %s\n' "$1"
    sh -c "$1" >"$LAST" 2>&1 || true
    cat "$LAST"
}
edit() { printf '\n# the agent edits %s: %s\n' "$1" "$2"; cat >"$1"; }
scene() { printf '\n\n===== %s =====\n' "$1"; }

main() {
    mkdir "$WORK/bills" && cd "$WORK/bills"
    git init -q -b main
    git config user.name "Demo" && git config user.email "demo@example.com"
    printf '__pycache__/\n' >.gitignore
    cat >bill.py <<'EOF'
"""Split a restaurant bill between friends."""


def split(total, people):
    """Return each person's share, in dollars."""
    share = round(total / people, 2)
    return [share] * people
EOF
    cat >test_bill.py <<'EOF'
import unittest

from bill import split


class TestSplit(unittest.TestCase):
    def test_even_split(self):
        self.assertEqual(split(90, 3), [30.0, 30.0, 30.0])

    def test_one_person_pays_everything(self):
        self.assertEqual(split(42.5, 1), [42.5])
EOF
    git add -A && git commit -q -m "bill splitter"

    scene "start"
    printf '\n$ python3 ~/.local/share/agent-skills/sci/scripts/sci.py init --objective "Add a tip option to the bill splitter"\n'
    python3 "$ROOT/scripts/sci.py" init --objective "Add a tip option to the bill splitter" 2>&1 || true
    x 'git switch -c sci/tip'

    scene "plan"
    x '.sci/sci note "split() rounds each share to cents: bill.py:6" --kind code'
    x ".sci/sci golden add shares --cmd \"python3 -c 'from bill import split; print(split(100, 3))'\""
    x '.sci/sci baseline'
    x '.sci/sci task add "Add a tip option to split()" --verify "python3 -m unittest test_tip -q" --scope bill.py --scope test_tip.py --inv "the shares add up to the bill plus tip"'
    x '.sci/sci task add "Show the tip on the receipt" --verify "python3 -m unittest test_receipt -q" --scope bill.py --scope test_receipt.py --dep T1'

    scene "T1: the test first"
    x '.sci/sci task start'
    edit test_tip.py "the acceptance test" <<'EOF'
import unittest

from bill import split


class TestTip(unittest.TestCase):
    def test_shares_add_up_to_bill_plus_tip(self):
        shares = split(100, 3, tip=0.15)
        self.assertAlmostEqual(sum(shares), 115.00, places=2)
EOF
    x '.sci/sci check'
    x '.sci/sci lock'

    scene "T1: fix 1, round each share"
    edit bill.py "round each share to cents" <<'EOF'
"""Split a restaurant bill between friends."""


def split(total, people, tip=0.0):
    """Return each person's share, in dollars."""
    share = round(total * (1 + tip) / people, 2)
    return [share] * people
EOF
    x '.sci/sci check'

    scene "the agent's memory is summarized"
    x '.sci/sci resume'

    scene "T1: fix 2, round up"
    edit bill.py "round each share up" <<'EOF'
"""Split a restaurant bill between friends."""
import math


def split(total, people, tip=0.0):
    """Return each person's share, in dollars."""
    share = math.ceil(total * (1 + tip) / people * 100) / 100
    return [share] * people
EOF
    x '.sci/sci check'

    scene "T1: the agent loosens the test"
    edit test_tip.py "places=2 becomes places=0" <<'EOF'
import unittest

from bill import split


class TestTip(unittest.TestCase):
    def test_shares_add_up_to_bill_plus_tip(self):
        shares = split(100, 3, tip=0.15)
        self.assertAlmostEqual(sum(shares), 115.00, places=0)
EOF
    x '.sci/sci check'
    x '.sci/sci restore --paths test_tip.py'

    scene "T1: fix 3, exact decimal math"
    edit bill.py "exact decimal math" <<'EOF'
"""Split a restaurant bill between friends."""
from decimal import ROUND_HALF_UP, Decimal


def split(total, people, tip=0.0):
    """Return each person's share, in dollars."""
    exact = Decimal(str(total)) * (1 + Decimal(str(tip)))
    share = (exact / people).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return [float(share)] * people
EOF
    x '.sci/sci check'

    scene "T1: STOP, guesses and an experiment"
    x '.sci/sci hyp add "computer math with decimals is inexact, so the sum comes out a cent short"'
    x '.sci/sci hyp add "rounding every share to cents loses the leftover cent, and nobody pays it"'
    mkdir -p .sci/scratch
    edit .sci/scratch/probe.py "an experiment" <<'EOF'
from decimal import Decimal

total = Decimal("100") * Decimal("1.15")
each = (total / 3).quantize(Decimal("0.01"))
print("exact total:", total, "| each:", each, "| 3 shares:", each * 3)
cents = int(total * 100)
print("in cents:", cents, "/ 3 =", divmod(cents, 3))
EOF
    x ".sci/sci run 'python3 .sci/scratch/probe.py'"
    run=$(sed -n 's/^run \([0-9]*\):.*/\1/p' "$LAST" | head -n 1)
    x ".sci/sci hyp refute H1 'run $run: with exact decimal math the 3 shares still add up to 114.99'"
    x ".sci/sci hyp confirm H2 'run $run: 11500 cents / 3 people = 3833 each with 1 cent left over'"

    scene "T1: the fix the evidence supports"
    edit bill.py "give the leftover cents to the first people" <<'EOF'
"""Split a restaurant bill between friends."""


def split(total, people, tip=0.0):
    """Return each person's share, in dollars. The leftover cents go to the first people."""
    cents = round(total * (1 + tip) * 100)
    base, extra = divmod(cents, people)
    return [(base + (1 if i < extra else 0)) / 100 for i in range(people)]
EOF
    x '.sci/sci check'
    x '.sci/sci verify'
    x '.sci/sci golden update shares --reason "the old shares lost a cent (3 x 33.33 = 99.99); now they add up to 100.00"'
    x '.sci/sci verify'
    x '.sci/sci done -m "feat: tip option for split()"'

    scene "T2"
    x '.sci/sci task start'
    edit test_receipt.py "the acceptance test" <<'EOF'
import unittest

from bill import receipt


class TestReceipt(unittest.TestCase):
    def test_receipt_shows_the_tip(self):
        text = receipt(100, 3, tip=0.15)
        self.assertIn("Tip:   $15.00 (15%)", text)
        self.assertIn("Total: $115.00", text)
EOF
    x '.sci/sci check'
    x '.sci/sci lock'
    edit bill.py "add receipt() and a to_cents() helper that cuts off the fraction of a cent" <<'EOF'
"""Split a restaurant bill between friends."""


def to_cents(amount):
    return int(amount * 100)


def split(total, people, tip=0.0):
    """Return each person's share, in dollars. The leftover cents go to the first people."""
    cents = to_cents(total * (1 + tip))
    base, extra = divmod(cents, people)
    return [(base + (1 if i < extra else 0)) / 100 for i in range(people)]


def receipt(total, people, tip=0.0):
    """The receipt as text: bill, tip, total and each share."""
    tip_cents = to_cents(total * tip)
    shares = split(total, people, tip)
    return "\n".join([
        "Bill:  $%.2f" % total,
        "Tip:   $%.2f (%d%%)" % (tip_cents / 100, round(tip * 100)),
        "Total: $%.2f" % ((to_cents(total) + tip_cents) / 100),
        "Each:  " + ", ".join("$%.2f" % s for s in shares),
    ])
EOF
    x '.sci/sci check'
    x '.sci/sci verify'
    edit bill.py "to_cents() rounds" <<'EOF'
"""Split a restaurant bill between friends."""


def to_cents(amount):
    return round(amount * 100)


def split(total, people, tip=0.0):
    """Return each person's share, in dollars. The leftover cents go to the first people."""
    cents = to_cents(total * (1 + tip))
    base, extra = divmod(cents, people)
    return [(base + (1 if i < extra else 0)) / 100 for i in range(people)]


def receipt(total, people, tip=0.0):
    """The receipt as text: bill, tip, total and each share."""
    tip_cents = to_cents(total * tip)
    shares = split(total, people, tip)
    return "\n".join([
        "Bill:  $%.2f" % total,
        "Tip:   $%.2f (%d%%)" % (tip_cents / 100, round(tip * 100)),
        "Total: $%.2f" % ((to_cents(total) + tip_cents) / 100),
        "Each:  " + ", ".join("$%.2f" % s for s in shares),
    ])
EOF
    x '.sci/sci check'
    x '.sci/sci verify'
    x '.sci/sci done -m "feat: show the tip on the receipt"'

    scene "the end"
    x 'git log --oneline'
    x '.sci/sci report'
    x "python3 -c 'from bill import receipt; print(receipt(100, 3, tip=0.15))'"
}

main 2>&1 | sed "s#$WORK#~#g"

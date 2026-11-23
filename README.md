<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/readme/hero-dark.svg">
  <img alt="Sci-Harness, for coding agents. Give your agent a big coding job. Get it done right." src=".github/readme/hero-light.svg" width="100%">
</picture>

<p align="center">
  <b>The free add-on that keeps your coding agent honest and on track, from the first step to the last.</b><br>
  Every step tested and saved · Tests it can't quietly weaken · Switch agents halfway and keep going
</p>

<p align="center">
  <a href="#get-started"><b>Get started in one line →</b></a> &nbsp;·&nbsp; <a href="docs/how-it-works.md">How it works</a>
  <br><sub>Works with Claude Code · Codex · Gemini CLI · Antigravity · OpenCode · pi · any agent that runs commands</sub>
</p>

## Agents slip on big jobs. Sci&#8209;Harness catches them.

It works like a climber's harness: every finished step is checked and saved, and every failed fix is kept, so a
slip costs one step, not the whole job.

| Without Sci&#8209;Harness | With Sci&#8209;Harness |
| --- | --- |
| ❌ **It forgets the plan.** Long chats get trimmed, and the details go with them. | ✅ **The plan is written down.** After any break, your agent, or a different one, picks up where it left off. |
| ❌ **It fakes the fix.** When a check fails, it quietly makes the check easier. | ✅ **The checks are locked.** Making one easier counts as a failure. Changing one takes a reason, and you see it. |
| ❌ **It goes in circles.** The same broken fix, again and again. | ✅ **Three misses, then it stops.** It must test what is really wrong before it tries again. If it skips that, you're told. |
| ❌ **It breaks what worked.** A new change quietly undoes an old one. | ✅ **Every step is re-checked.** Old features are tested again before anything is saved. |

## Out of usage? Switch agents and keep going.

Open another agent in the same project folder and type **`/sci`**. It picks up the same plan, the same locked
checks and everything already ruled out, even a test run that was still going. No copied chats. No
re-explaining. Claude Code to Codex, Gemini CLI to OpenCode, and back again.

## Everything else you get

- 🧾 **Every bent rule on the record.** The final report lists each one, so you know exactly where to look.
- 🙋 **Questions don't stall the job.** When something is unclear, it asks you and carries on with the rest.
- ⏳ **Slow tests are safe.** A long test run keeps going even if your agent times out or closes.
- 🔢 **Careful with numbers.** Key results are saved before a change, and any that move are caught.
- ↩️ **Easy undo.** It goes back to the last good point in one step, and every failed fix is kept.
- 🧹 **A clean history.** One commit per finished step, saying what was checked. Its own notes stay out of it.
- 📋 **Fine for messy projects.** Old problems are noted first, so only new ones count.
- ⚡ **Barely slower.** It runs only the current step's test, and skips checks that already passed on the same code.

| 💬 No coding needed | 🔒 Stays on your computer | 🆓 Free and open source |
| :---: | :---: | :---: |
| You describe the job. Your agent does the rest. | Sci-Harness never sends your code anywhere. | No account, no sign-up. MIT license. |

## Get started

**1. Install it.** Open Terminal on a Mac or Linux, or PowerShell on Windows, and paste one line.

Mac or Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/Nag681/sci-harness/main/install.sh | sh
```

Windows:

```powershell
irm https://raw.githubusercontent.com/Nag681/sci-harness/main/install.ps1 | iex
```

**2. Give your agent a big job.** Restart your agent, open your project and type **`/sci`**, then the job in one
sentence.

**3. Read the report.** When the job is done, your agent tells you what changed and what needs you.

<sub>No `/` commands in your agent? Write “Use the sci skill to …” instead. Sci-Harness needs Python and Git on
your computer.</sub>

<details>
<summary><b>Update or remove it</b></summary>

To update, paste the install line again. To remove it:

```bash
curl -fsSL https://raw.githubusercontent.com/Nag681/sci-harness/main/install.sh | sh -s -- --uninstall
```

```powershell
$env:SCI_ARGS = "--uninstall"; irm https://raw.githubusercontent.com/Nag681/sci-harness/main/install.ps1 | iex
```

</details>

<sub>Under the hood: the [developer guide](docs/how-it-works.md) · [Changelog](CHANGELOG.md) · [MIT license](LICENSE)</sub>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/readme/hero-dark.svg">
  <img alt="Sci-Harness, for coding agents. Give your agent a big coding job. Get it done right." src=".github/readme/hero-light.svg" width="100%">
</picture>

A free add-on for coding agents like Claude Code, Codex and Gemini. It keeps them honest and on track through
long jobs.

**[Get started](#get-started)** · [Good to know](#good-to-know) · [For developers](docs/how-it-works.md)

## Big jobs are where agents slip

Agents slip on long jobs in four ways. Sci-Harness works like a climber's harness: every finished
step is checked and saved, so a slip costs one step, not the whole job.

| Without Sci&#8209;Harness | With Sci&#8209;Harness |
| --- | --- |
| ❌ **It forgets the plan.** Long chats get trimmed, and the details go with them. | ✅ **The plan is written down.** After any break, your agent picks up where it left off. |
| ❌ **It fakes the fix.** When a check fails, it quietly makes the check easier. | ✅ **The checks are locked.** Making a check easier counts as a failure. |
| ❌ **It goes in circles.** The same broken fix, again and again. | ✅ **Three misses, then it stops.** It has to find the cause before it tries again. |
| ❌ **It breaks what worked.** A new change quietly undoes an old one. | ✅ **Every step is re-checked.** Old features are tested again before anything is saved. |

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

## Good to know

- **No coding needed.** You describe the job. Your agent does the rest.
- **Stays on your computer.** Your code is never sent anywhere.
- **Free and open source.** No account, no sign-up. MIT license.
- **Fine for messy projects.** Old problems are noted first, so only new ones count.
- **Barely slower.** It re-checks only what changed.
- **Works with your agent.** Claude Code, Codex, Gemini CLI, Antigravity, OpenCode and pi.

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

<sub>How it works under the hood: the [developer guide](docs/how-it-works.md). · [MIT license](LICENSE)</sub>

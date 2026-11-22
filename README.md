<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/readme/hero-dark.svg">
    <img alt="Sci-Harness, for coding agents. Give your agent a big coding job. Get it done right." src=".github/readme/hero-light.svg" width="100%">
  </picture>
</p>

<p align="center">
  A free add-on for coding agents like Claude Code, Codex and Gemini.<br>
  It keeps them honest and on track through long jobs.
</p>

<p align="center">
  <a href="#get-started"><b>Get started</b></a> &nbsp;·&nbsp; <a href="#good-to-know">Good to know</a> &nbsp;·&nbsp; <a href="docs/how-it-works.md">For developers</a>
</p>

<br>

## Big jobs are where agents slip

You ask your agent to build something big. It starts strong. A few hours in:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/readme/slips-dark.svg">
  <img alt="It forgets the plan: long chats get trimmed, and the details go with them. It fakes the fix: when a check fails, it quietly makes the check easier. It goes in circles: the same broken fix, again and again. It breaks what worked: a new change quietly undoes an old one." src=".github/readme/slips-light.svg" width="100%">
</picture>

## Sci-Harness catches every slip

Climbers clip into a harness so that a slip never sends them to the bottom. Sci-Harness does the same for your
agent. Every finished step is checked and saved, so a mistake costs one step, not the whole job.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/readme/catches-dark.svg">
  <img alt="The plan is written down: after any break, your agent picks up exactly where it left off. The checks are locked: making a check easier counts as a failure. Three misses, then it stops: it has to find the cause before it tries again. Every step is re-checked: old features are tested again before anything is saved." src=".github/readme/catches-light.svg" width="100%">
</picture>

## One evening, start to finish

At 6 PM you ask your agent for a shopping cart, then step away.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/readme/story-dark.svg">
  <img alt="6:00 PM: the plan is written, four steps, each with a check that proves it works. 6:40 PM: step 1 is saved; items go into the cart. 7:15 PM: a fake fix is caught; a check was made easier, and it is put back as it was. 8:05 PM: three misses, then a stop; it stops guessing and tests its ideas to find the cause. 8:30 PM: step 2 is saved; prices and totals add up. 9:10 PM: the chat is trimmed; it reads its notes and picks up exactly where it left off. 10:20 PM: step 3 breaks step 1; caught before it is saved, then fixed. 11:40 PM: done, with a report: 4 steps saved, 4 slips caught and 1 question for you." src=".github/readme/story-light.svg" width="100%">
</picture>

## How you use it

<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/readme/steps-dark.svg">
  <img alt="1. Describe the job: one sentence to your agent is all it takes. 2. Your agent builds: step by step, each one checked and saved. 3. You get the result: finished work and a short report to read." src=".github/readme/steps-light.svg" width="100%">
</picture>

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

**2. Give your agent a big job.** Restart your agent, open your project and type:

> **`/sci add a shopping cart to my store`**

<sub>No `/` commands in your agent? Write “Use the sci skill to …” instead. Sci-Harness needs Python and Git on
your computer.</sub>

## Good to know

<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/readme/facts-dark.svg">
  <img alt="No coding needed: you describe the job, your agent does the rest. Stays on your computer: your code is never sent anywhere. Free and open source: no account, no sign-up, MIT license. Fine for messy projects: old problems are noted first, so only new ones count. Barely slower: it re-checks only what changed. Works with your agent: Claude Code, Codex, Gemini CLI, Antigravity, OpenCode and pi." src=".github/readme/facts-light.svg" width="100%">
</picture>

<details>
<summary><b>Update or remove it</b></summary>
<br>

To update, paste the install line again. To remove it:

```bash
curl -fsSL https://raw.githubusercontent.com/Nag681/sci-harness/main/install.sh | sh -s -- --uninstall
```

```powershell
$env:SCI_ARGS = "--uninstall"; irm https://raw.githubusercontent.com/Nag681/sci-harness/main/install.ps1 | iex
```

</details>

<br>

<p align="center">
  <sub>Curious how it works under the hood? Read the <a href="docs/how-it-works.md">developer guide</a>. · <a href="LICENSE">MIT license</a></sub>
</p>

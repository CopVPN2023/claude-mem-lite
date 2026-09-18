---
name: timeline-report
description: Generate a narrative "Journey Into [Project]" development-history report from claude-mem-lite's full observation history for one project, saved to a markdown file.
---

# timeline-report

Use when asked for a timeline report, project history analysis,
"journey into" this project, or a full narrative summary of a project's
development — not for a specific question (`knowledge-agent`) or a
handful of recent hits (`mem-search`). This reads a project's *entire*
observation history, not a ranked top-N.

**First, check the size** before generating for real:

```bash
~/claude-mem-lite/.venv/bin/python3 ~/claude-mem-lite/timeline_report.py --project <path> --dry-run
```

Prints the observation count and a rough input-token estimate, no LLM
call. Tell the user the estimate and confirm before proceeding if it's
large — this is one `claude -p` call over the whole history, so a large
project means a large prompt.

Then generate for real:

```bash
~/claude-mem-lite/.venv/bin/python3 ~/claude-mem-lite/timeline_report.py --project <path> [--output <file>]
```

Saves a markdown narrative to `--output`, or `./journey-into-<project
basename>.md` by default, covering genesis, architectural evolution, key
breakthroughs, work patterns, technical debt, debugging sagas, timeline
statistics, and lessons. Report the saved path, observation count, and
date range covered back to the user.

Unlike `mem-search`/`timeline`/`get-tool-uses`, this fails loud on a
`claude -p` failure (stderr + non-zero exit, no file written) rather than
silently — there's a person waiting on the result.

No "Token Economics" section, unlike claude-mem's own `timeline-report`
skill — claude-mem-lite never tracked per-observation token costs (an
explicit non-goal), so there's no data to build that section from.

Cross-check anything safety-relevant against the actual code or git history
before relying on it — this log is a memory aid, not a source of truth.

# Skills for the Codex session

Install these on the machine where Codex runs. The starter does not vendor
third-party skills or require them at package runtime.

## Recommended two

1. [python-design-patterns](https://github.com/wshobson/agents/tree/main/plugins/python-development/skills/python-design-patterns)
   for KISS, focused functions, and avoiding premature abstractions.
2. [modern-python](https://github.com/trailofbits/skills/tree/main/plugins/modern-python/skills/modern-python)
   for Python development tooling. Apply selectively; keep Python 3.9, pip/venv
   support, and the existing runtime dependency limit.

Open Codex in the project directory:

```bash
codex
```

Enter each of the following as a separate Codex prompt, not as a shell command:

```text
$skill-installer install https://github.com/wshobson/agents/tree/main/plugins/python-development/skills/python-design-patterns
```

```text
$skill-installer install https://github.com/trailofbits/skills/tree/main/plugins/modern-python/skills/modern-python
```

Use `/skills` to confirm availability. If needed, exit and start a new Codex
session. Then run the project task using CODEX_PROMPT.md.

For a later testing-only pass, optionally install:

```text
$skill-installer install https://github.com/wshobson/agents/tree/main/plugins/python-development/skills/python-testing-patterns
```

## Air-gapped option

On a connected machine, obtain the complete skill directories, including
references and assets, from those exact upstream paths. Place them in the
project as:

- `.agents/skills/python-design-patterns/`
- `.agents/skills/modern-python/`

Each must contain SKILL.md at its root. Record the upstream revision if your
team keeps these in Git, preserve their license/attribution files, and transfer
them with the repository. This is a manual copy step; the initial ZIP contains
the project instructions and skill links, not downloaded upstream skills.

The package upgrade can proceed from AGENTS.md and docs/requirements.md even
if a skill cannot be downloaded.

## Official Codex references

- [Skills, installer, and repository discovery](https://learn.chatgpt.com/docs/build-skills)
- [Project instructions using AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)

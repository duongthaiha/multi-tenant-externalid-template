# Python hosted agent evaluation lineage (`.foundry`)

This directory mirrors `src/portfolio-agent/.foundry`'s role for the parallel Python
hosted agent (azd service `portfolio-agent-python`): it is where
`scripts/evaluate-portfolio-agent.py --agent python` persists evaluation run lineage,
under `results/<azd-environment>/<eval-id>/<run-id>.json`, exactly like the C# profile
persists under `src/portfolio-agent/.foundry/results/`.

No results ship in the public template. Once the agent is deployed (with `AGENT_PORTFOLIO_AGENT_PYTHON_NAME`,
`AGENT_PORTFOLIO_AGENT_PYTHON_VERSION`, and
`AGENT_PORTFOLIO_AGENT_PYTHON_RESPONSES_ENDPOINT` published for that environment),
running any suite against `--agent python` will populate this tree. See the
C# profile's `eval-invocation-design.json` for the shared
(agent-agnostic) design contract this profile also follows. This directory
intentionally does not duplicate that design document.

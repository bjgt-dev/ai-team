# Local handoff

## Summarize the conversation for pickup

- Capture the goal and the decisions already made and the open items and the next focus so a fresh agent can continue without rereading the whole conversation.

- Tailor the summary to the focus the user named when the user passed arguments for the next session.

- Include a suggested skills section that names which skills the next agent should search for with `muse.skill_search`.

- Reference specs and plans and ADRs and issues and commits and diffs by path or URL instead of copying their content into the summary.

- Redact API keys and passwords and personally identifiable information from the summary before it is saved or handed to another agent.

## Choose the delivery route

- Save the summary as a handoff document in the temporary directory of the user's operating system rather than in the current workspace when the next agent will pick it up later.

- Start a fresh background agent with the summary as its starting prompt and give that agent a descriptive name when the work should continue immediately.

- Keep the same summary content for either route and change only how it is delivered.

## Available connectors

- Use `muse.connector_search` to find other connectors and `muse.skill_search` to find related skills.

- If nothing here fits, look on the web for a maintained API or MCP server and set it up with [Custom Connectors and MCPs](/opt/muse/skills/custom-connector-setup/SKILL.md), or build a custom connector.

## Alternatives for better results

- Publish the work as a spec or as tickets when the next session needs tracked work rather than context alone.

# Implement the work

## Work from the spec and tickets

- Read the spec and its tickets before you write code.

- Treat the tickets as a task graph with blocking relationships between them.

- Work the frontier first, meaning the tickets whose blockers are all done.

- If the tracker was not provided, tell the user to run the setup skill for this repo before you start.

- If the work has no spec or ticket graph, implement it directly on the current branch instead of building an integration branch.

## Run implementers in parallel

- Create one integration branch for the whole spec when a ticket graph exists.

- Run one implementer per ticket in its own worktree on its own branch, based on the integration branch.

- Let each implementer build its ticket test first where possible, and merge the integration branch tip into its own branch before it reports done.

- Use an exploration step only when a ticket needs codebase or external exploration, and save its notes outside the repo where later implementers can read them.

- Merge each finished ticket to the integration branch, then start implementers for any tickets the merge unblocks.

## Review and close

- Run typechecking regularly, run single test files regularly, and run the full test suite once at the end.

- Review the integration branch once all tickets are complete, and fix every issue the review raises in one further implementer step.

- Open a draft pull request after the first merge only when the tracker closes work through pull requests or the user asks for one, and mark it ready when the work is complete.

- Close each ticket the way the tracker closes work, report the integration branch, and clean up all implementer worktrees.

- Commit direct work to the current branch when there is no ticket graph.

## Available connectors

- Use `muse.connector_search` to find other connectors and `muse.skill_search` to find related skills.

- If nothing here fits, look on the web for a maintained API or MCP server and set it up with [Custom Connectors and MCPs](/opt/muse/skills/custom-connector-setup/SKILL.md), or build a custom connector.

## Alternatives for better results

- Break a large spec into smaller tickets before implementing, so each implementer fits in one fresh context.

- Prefactor the code to make the change easy before you implement the change itself.

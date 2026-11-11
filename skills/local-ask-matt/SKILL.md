# Ask which skill fits

## Follow the idea to ship flow

- Start with local_grilling when the user wants to stress test a plan or decision.

- Ask for glossary and ADR docs alongside the grilling only when the work sits in a working directory and a paper trail will help later work.

- Run the same local_grilling interview without docs when no working directory or paper trail is needed.

- Detour through a prototype when a question needs a runnable answer and use local_handoff in both directions around that prototype.

- Turn a multi-session build into a spec with local_to_spec and then split that spec into tracer bullet tickets with local_to_tickets.

- Implement each ticket in a fresh context with local_implement when you want to drive tickets one at a time.

- Implement a whole spec in one run with local_implement when you want the ticket graph route with an integration branch and parallel implementers across the ready frontier.

- Drive each build slice test first with local_tdd and close the build with a two axis review from local_code_review.

- Shape a pull request body with local_pr and close the loop with local_retro after a build and especially after a build that went sideways.

- Keep grilling and spec and tickets in one unbroken context window until the tickets are done and start each implementation from its ticket.

## Take an on-ramp when work arrives differently

- Use local_triage for bug reports and incoming requests that you did not create and do not triage tickets that local_to_tickets already made agent ready.

- Use local_diagnosing_bugs for a hard bug that resists a first glance and build a tight feedback loop before you theorise about the cause.

- Use local_wayfinder for a huge and foggy effort that one session cannot hold and hand its cleared map to local_to_spec instead of jumping straight to implementation.

- Use local_improve_codebase_architecture in a spare moment to surface deepening opportunities and design a chosen opportunity with local_codebase_design.

## Choose at a phase boundary

- Read [Phase boundaries](PHASE-BOUNDARIES.md) at a phase boundary and work its questions in order.

- Continue in the same session when the next phase needs this phase as a primary source or when enough sharp context remains for the next phase to fit.

- Clear the context only when everything in the session is disposable for what comes next.

- Use local_handoff only when work must travel to a new harness or a new directory or a colleague or a forked side task.

- Send a tightly scoped task to a subagent when it can run without steering and use compacting as the default only after the cheaper options fail.

- Make the boundary decision at the boundary and mid-phase either continue or split the rest into subagents.

## Use a standalone skill when work sits outside the main flow

- Use local_research to delegate reading legwork to a background agent and carry its cited file into grilling or spec work.

- Use local_to_questionnaire when the blocker sits with someone else and aim its questions at the gap you need back.

- Use local_wizard for steps only a human can perform such as provisioning infrastructure or walking an unfamiliar dashboard.

- Use local_wait_what inside any other skill when a message did not land and re-pitch it in plain language.

- Use local_teach to learn a concept over multiple sessions in the current directory as a stateful workspace.

- Use local_prototype any time a design question stays hard to settle on paper.

- Use local_domain_modeling directly when the words are the problem and use local_codebase_design directly when the shape of a module is the problem.

- Use local_writing_for_agents when you write documents that agents will consume.

## Available connectors

- Use `muse.connector_search` to find other connectors and `muse.skill_search` to find related skills.

- If nothing here fits, look on the web for a maintained API or MCP server and set it up with [Custom Connectors and MCPs](/opt/muse/skills/custom-connector-setup/SKILL.md), or build a custom connector.

## Alternatives for better results

- Search with `muse.skill_search` and read the closest skill before you route so you do not send work to a skill that only sounds similar.

- Ask the user to choose a no interview synthesis route when they want a spec from discussion already held rather than a new interview.

- Break a large spec into smaller tickets before implementing so each implementer fits in one fresh context.

- Use local_domain_modeling when a session needs deeper terminology work than the docs created during grilling.

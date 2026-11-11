# Local grilling

## Map the design tree

- Start grilling when the user asks to stress-test thinking or uses a grill trigger phrase.

- Map the plan or decision as a design tree where every decision branches into the decisions that hang off it.

- Interview the user relentlessly until you reach a shared understanding of every branch.

## Ask the frontier in rounds

- Identify the frontier as every decision whose prerequisites are already settled.

- Ask the whole frontier in one round and number each question.

- Give a recommended answer under each question.

- Wait for the user answers before you ask the next round.

- Recompute the frontier after each round because settled decisions unblock new questions.

- Hold a question for a later round when its answer depends on a question still open in the current round.

## Find facts yourself

- Find facts from the environment yourself and never ask the user for a fact you could look up.

- Dispatch a subagent to find a needed fact when a frontier question depends on it.

- Do not block the whole round on one exploration and ask the rest of the frontier now.

- Keep decisions for the user and put each decision to the user and wait.

## Create docs as you go

- Create glossary and ADR docs as you go only when the user asked for docs alongside the grilling.

- Record a term in GLOSSARY.md right there when the term is resolved and do not batch terms for later.

- Create GLOSSARY.md lazily when the first term is resolved and create docs/adr only when the first ADR is needed.

- Keep GLOSSARY.md free of implementation details and do not use it as a spec or a scratch pad.

- Offer an ADR only when the decision is hard to reverse and surprising without context and the result of a real tradeoff.

- Challenge a term at once when it conflicts with the language already in GLOSSARY.md.

## Finish only on shared understanding

- Finish when the frontier is empty and every branch has been visited with nothing silently assumed.

- Do not act on the plan or decision until the user confirms you have reached a shared understanding.

## Available connectors

- Use `muse.connector_search` to find other connectors and `muse.skill_search` to find related skills.

- If nothing here fits, look on the web for a maintained API or MCP server and set it up with [Custom Connectors and MCPs](/opt/muse/skills/custom-connector-setup/SKILL.md), or build a custom connector.

## Alternatives for better results

- Use `muse.skill_search` to find local_domain_modeling when a session needs deeper terminology work than the docs created here.

- Ask the user to choose a no interview synthesis route when they want a spec from discussion already held rather than a new interview.

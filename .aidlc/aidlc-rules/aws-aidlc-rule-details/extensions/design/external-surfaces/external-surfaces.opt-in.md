# External Surface Validation - Opt-In

**Extension**: External Surface Validation

## Opt-In Prompt

The following question is automatically included in the Requirements
Analysis clarifying questions when this extension is loaded:

```markdown
## Question: External Surface Validation
Should external surface validation rules be enforced for this project?

A) Yes - enforce all EXTSRF rules as blocking constraints (recommended for
any project that depends on an SDK, external API, or auth flow)

B) No - skip EXTSRF rules (suitable only when the project touches no
external surface)

X) Other (please describe after [Answer]: tag below)

[Answer]:
```

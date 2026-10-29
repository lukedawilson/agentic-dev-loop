| Code | Meaning | Correct response |
| --- | --- | --- |
| AE-SBX-008 | The sandbox refused a git mutation command. Only the single commit step may change git state; agents never commit, branch, tag, or otherwise mutate git. | Do not run the command or any git mutation again. Continue without touching git and leave committing to the commit step. |
| AE-SBX-010 | The sandbox could not create the execution container for the command, so the command did not run. | The command is not the problem: retry once; if it persists, mention the code in your final response and stop. |
| AE-SBX-016 | The loop refused to sync an edit because the real repo changed underneath on a path this workspace also touches; the file in this workspace is stale and its edits will not be applied. | The command is not the problem: retry once, do not edit or work around the named path, and mention the code in your final response if it persists. |
| AE-SBX-018 | The network egress proxy for this step stopped, so the sandbox refused the command rather than run without enforcement. | The command is not the problem: do not retry it, mention the code in your final response, and stop. |
| AE-SBX-020 | The sandbox could not start the network egress proxy for this step, so the command did not run. | The command is not the problem: retry once; if it persists, mention the code in your final response and stop. |

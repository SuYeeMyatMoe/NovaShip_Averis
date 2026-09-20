# Operator FAQ

## Why does a case say HUMAN_REVIEW?

A required field is missing, unreadable, below the configured confidence threshold, or the workflow needs an authorised human decision. HUMAN_REVIEW is not a mismatch verdict. Check the Evidence and Errors panels before deciding what to do next.

## What does WAITING_DOCUMENTS mean?

The Shipping Instruction or Draft Bill of Lading is missing, unreadable, or the wrong document type was supplied. Upload or request the correct readable document, then retry the case. Do not ask Ask AI to infer a missing document value.

## What should I do when fields mismatch?

Open the Seven-Field Comparison and Evidence tabs. Verify the original SI and Draft BL snippets, review the recommended action, and inspect the correction draft. If external communication is needed, request approval from a Supervisor or Admin.

## Can Ask AI approve or send a draft?

No. Ask AI may explain the case, summarize evidence, and help prepare text. An authorised person must approve every external action in the case workflow.

## Can I contact the extracted Notify Party directly?

No. The extracted Notify Party is a comparison value, not contact authorisation. Use the Collaboration tab to select an approved recipient, choose the minimum necessary fields, inspect the preview, and confirm according to your role.

## What does THC mean?

THC means Terminal Handling Charge. It is a charge associated with handling a container at a terminal. Refer to the shipping glossary and the applicable commercial policy for case-specific interpretation.

## What if Ask AI cannot answer?

Do not ask it to guess. Review the source email, attachments, comparison evidence, policy, and audit history. Escalate missing or ambiguous information to a Supervisor.

## Why did Ask AI refuse my request?

The assistant refuses requests that would bypass approval, act on behalf of a user, invent values, send immediately, or access another case. Follow the authorised action shown in the refusal message.

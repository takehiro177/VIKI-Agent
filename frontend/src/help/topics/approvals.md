# Approvals and permissions

VIKI Agent asks before Claude Code takes an action its rules leave open to you.

## Why VIKI asks

Claude Code already runs with your account's rights, under its own instructions, skills, hooks and permission rules. Those rules settle most actions on their own. VIKI Agent only asks when a call is still left open. So some tool calls show no card at all.

## Answer a request

A **Waiting for approval** card names the tool, its target and the reason it is asking. Choose **Allow once** to let it try that one action, or **Deny** to refuse it. The card's note says **Permission applies to this request only**, so check the conversation afterward to see what happened.

The card shows a command on the lines it was written on. A character that hides or reorders text is shown as its code, such as `<U+202E>`, and the card says so; a card whose text runs past its box says that part of it is out of view. For a tool VIKI Agent has no summary for, such as an MCP tool, the card lists every argument, and says so first when they were shortened to fit.

## Plans and questions

A plan card asks **Proceed with this plan?** Choose **Approve** to exit plan mode and continue, or **Keep planning** to keep going. When a plan's text has a **Details** heading, the card folds that heading and what follows it behind a closed disclosure you open yourself; a plan with none shows in full, as before. A question card lists its choices as buttons. Pick one (or several, where the question allows it), change your picks as often as you like, then choose **Submit**; or use **Other** to answer in your own words.

## Everything waiting in one place

In **Overview** and **Code**, **Needs you** stays at the top of the right panel and shows how many items are waiting. Select **Open Approvals** to open the **Approvals** dialog and review every request and proposed change in one place.

A tool permission from a chat that's off screen can be answered in the list with **Allow once** or **Deny**. The row shows the whole command beside the buttons, on the lines it was written on. A long command scrolls, and a line under it says when part of it is out of view. For half a second after the buttons appear or move, a mouse click on **Allow once** or **Deny** is ignored and the buttons look dimmed; the keyboard still works. An agent run opened from the VIKI panel lists its waiting tool requests the same way, with the same buttons. Other tool permissions, plans and questions open their chat; a plan or question opens on its exact card.

When new requests arrive together, VIKI Agent announces them once for the batch: one request gets a short description, and several requests get a count.

When the window is too narrow to dock the right panel, the **VIKI panel** toggle shows a dot when something needs you. Its accessible description and tooltip give the waiting count.

## Proposed changes

In a protected vault, a note edit offers **Propose** instead of a direct save. A reply can also offer **Propose this skill**, or, for one of Decision's own questions, **Propose this wording**. A note edit or a skill proposal, once approved, creates a chat draft for you to review and send; nothing changes until you send that draft. Approving a question proposal is different: it applies the new wording right away, with no chat draft.

Decision can also raise a vault-change proposal on its own, before any reply offers one — to link two notes, mark an older one as replaced, or archive a project's ticked tasks. **Approve** makes a chat draft of that one edit, and nothing in the vault changes until you send it; once a draft has been made, the same row instead offers **Draft again**, opening another copy of it. **Reject**, with an optional reason, withdraws the proposal, and the vault is not changed.

## Permission modes

A new chat starts in the mode set by **Default mode** in **Settings** › **Chat**, or by a profile's own **Permission mode** setting. To switch an open chat, use the mode chip in its header; a profile's chat keeps its profile's mode. **Manual** asks before edits and commands that Claude Code's settings do not allow. Reading files in the chat's folders does not need a card. **Accept edits** lets Claude edit files and run `rm`, `mv`, `cp` and `sed` in those folders without asking. **Auto** lets Claude Code's own classifier review actions first; a request it still makes arrives as a card. **Plan** has VIKI plan first, for you to approve before it acts. See [Claude Code's permission guide](https://code.claude.com/docs/en/permissions) for the rest.

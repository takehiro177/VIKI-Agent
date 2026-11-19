# Vault

Browse, search, and edit the notes in your current workspace.

## Layout
The tabs under the search box switch between the **Front page**, **Knowledge**, **Sources**, **Maps**, **Everything else** and **All notes**; the number beside a tab counts the listed files in that folder, or in the whole list for **All notes**. In a window 800 pixels wide or more, the list sits on the left and the note you open sits on the right, and each side scrolls on its own. Until you open a note, the right side of **Knowledge**, **Sources** and **Maps** shows the section at a glance: its notes by status, its most linked maps, and its topics. Drag the divider between the two sides to resize them; **Reset** on the divider restores the default split. In a narrower window the note replaces the list, and **Back to notes** returns to it.

## Front page
When the vault has notes in `30-knowledge/`, `40-sources/` or `50-maps/`, the **Front page** brings together **Featured**, **Latest**, **Topics**, **This week**, **Rediscover** and your **Bookmarks**. **Latest** groups recently added or updated notes by day. **Rediscover** lists Knowledge files whose modification time is at least 30 days old. A newer note in another folder does not appear in **Latest**.

**Featured**, **Latest**, **Topics** and **This week** cover notes read from Knowledge, Sources and Maps; when that read is partial, their counts are lower bounds. The rail's Knowledge, Sources and Inbox tiles count listed files, so their counts can differ from the front page's note counts. **Topics** lists maps and their linked-note counts, and tags found in the covered notes; tag counts do not cover the whole vault. **Rediscover** uses Knowledge file modification times. **Bookmarks** use saved exact paths and can point to notes outside those folders. **Inbox** shows captures in `00-inbox/` and their age; **Triage inbox** opens inbox triage.

A card shows the note's exact relative path, except when the note sits directly in its folder under its own file name, where the path would only repeat the title; the reader always shows the path. Titles do not change file names or link targets. Dates, status and summaries come from the note's opening text when available; otherwise a card can show its file-change date. An unsupported or truncated header shows **Some details in this note's header could not be read.** If a note has no overview row, its card says **Details not read**; this does not say why the row is missing. The front page may show a global notice that some note details could not be read. **Show latest** restores the latest ordering after an updated overview arrives while you are focused on a card. **Bookmarks** shows the five newest saved paths.

**Bookmarks** point to exact paths. If a note is moved or renamed, its bookmark can appear as missing; it does not follow the note to another path. **Related** shows resolved or ambiguous links and backlinks with their exact paths. Missing links remain text and are not matched to another note.

## Sections, all notes and search
**Knowledge**, **Sources** and **Maps** browse the notes in those folders. **Everything else** appears when the list has another folder to show. These sections can show a file-name title with **Details not read** when overview details are unavailable. A partial list may not include every folder. **All notes** lists readable notes; filter by folder, change the sort order, or show only your bookmarks. **Search note contents** searches inside notes, not just their titles or paths. Rows show the exact relative path, including in search results, so notes with the same title remain distinct. A row with only file-name details says **Titles from file names; details not read.** A header that could not be read says **Some details in this note's header could not be read.**

## Reading a note
A note opens with its title and available details, followed by its text without the header block. A link written as `[[Note name]]` opens a matching note. **Related** includes links and backlinks, with exact paths; missing links remain text. **Bookmark** saves the current path, and a moved note stays missing at that bookmark. **History** opens the note's edit history.

## Creating and changing notes
With an ordinary vault open, you can also edit notes from this view.
Select **New note**, enter a path and its Markdown content, then select **Create note**.
Nothing is written until you select **Create note** — review the path first, since it becomes the note's saved location.
Open a note and select **Edit note** to change it.
Or open its **Note actions** menu to attach an excerpt to your message, or for **Rename**, **Move**, **Archive** or **Delete**.
Archived notes move under `archive/` and can be moved back.
Deleting a note creates a recovery copy when there's room, restorable for 30 days while that copy lasts.
Select **Save note** to write your change.
If the note changed elsewhere first, saving stops and your draft stays so you don't lose it.
The `bot-memory/` folder is read-only here; Claude Code writes Bot memory. **Recovery** and **Memory** sit at the end of the page; Recovery opens by itself when a save or note operation did not finish.

## A protected vault
A vault with the **Protected** policy is never written by the app directly.
Here the note editor offers **Propose** instead of **Save note**.
Proposing records the change for review in Approvals.
Approving it creates a chat draft asking VIKI to apply the change.

## macOS and Linux
Saving and the note actions work on APFS (macOS) and on ext4, XFS, Btrfs or tmpfs (Linux). On another drive, such as a network drive, the vault is read-only.

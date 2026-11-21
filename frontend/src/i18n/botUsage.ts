import { defineCatalog } from "./defineCatalog";

export const catalog = defineCatalog({
  en: {
    // Format helpers (usageFormat.ts): KIND_LABELS, routeLabel, botLabel, percentText's fallback,
    // and the shared "N token(s)"/"N call(s)" pairs other packages compose their own templates
    // from, the same way a pre-formatted number is passed into another catalog string's `tf`.
    "botUsage.kind.mainChat": "Main Bot chat",
    "botUsage.kind.taskThreads": "Task threads",
    "botUsage.kind.threadTurns": "Bot thread turns",
    "botUsage.kind.routineRuns": "Routine runs",
    "botUsage.kind.subagents": "Subagents",

    "botUsage.route.claudeCode": "Claude Code",
    "botUsage.route.removed": "Removed connection",

    "botUsage.bot.unknown": "Unknown Bot",
    "botUsage.bot.removedSuffix": "(removed)",

    "botUsage.format.percentEmpty": "-",

    "botUsage.count.tokens.one": "{count} token",
    "botUsage.count.tokens.many": "{count} tokens",
    "botUsage.count.calls.one": "{count} call",
    "botUsage.count.calls.many": "{count} calls",

    // Column, tile and field names shared across the tables (UsageTables), the trace
    // (UsageTrace) and the dashboard tiles (BotUsage): one row per word, reused wherever that
    // same column or tile appears.
    "botUsage.field.bot": "Bot",
    "botUsage.field.tokens": "Tokens",
    "botUsage.field.share": "Share",
    "botUsage.field.input": "Input",
    "botUsage.field.output": "Output",
    "botUsage.field.cacheReads": "Cache reads",
    "botUsage.field.cacheWrites": "Cache writes",
    "botUsage.field.calls": "Calls",
    "botUsage.field.conversations": "Conversations",
    "botUsage.field.route": "Route",
    "botUsage.field.model": "Model",
    "botUsage.field.kind": "Kind",
    "botUsage.field.lastCall": "Last call",
    "botUsage.field.time": "Time",
    "botUsage.field.date": "Date",
    "botUsage.field.thread": "Thread",
    "botUsage.field.context": "Context",

    // UsageColumns (the daily column chart).
    "botUsage.columns.empty": "No token use in this period.",
    "botUsage.columns.allBots": "All Bots",
    "botUsage.columns.tooltip": "{day} · {tokens} · {calls}",

    // UsageTables (the four summary tables).
    "botUsage.tables.bots": "Bots",
    "botUsage.tables.moreBots.one": "1 more Bot is counted under Other Bots.",
    "botUsage.tables.moreBots.many": "{count} more Bots are counted under Other Bots.",
    "botUsage.tables.modelsAndRoutes": "Models and routes",
    "botUsage.tables.moreRoutes.one": "1 more route is not shown.",
    "botUsage.tables.moreRoutes.many": "{count} more routes are not shown.",
    "botUsage.tables.conversationKinds": "Conversation kinds",
    "botUsage.tables.total": "Total",
    "botUsage.tables.largestConversations": "Largest conversations",
    "botUsage.tables.traceButton": "Trace",
    "botUsage.tables.openButton": "Open",
    "botUsage.tables.transcriptRemoved": "Transcript removed",
    "botUsage.tables.empty": "No usage in this period.",

    // UsageNotes (measured facts and coverage notes).
    "botUsage.notes.measuredFacts": "Measured facts",
    "botUsage.notes.cacheReadShare": "Cache reads were {share} of input-side tokens.",
    "botUsage.notes.avgContext": "An average call carried {count} tokens of context.",
    "botUsage.notes.routineShare": "Routine runs used {share} of all tokens.",
    "botUsage.notes.subagentShare": "Subagents used {share} of all tokens.",
    "botUsage.notes.largestShare": "The largest conversation used {share} of all tokens.",
    "botUsage.notes.aboutNumbers": "About these numbers",
    "botUsage.notes.coverage":
      "Counted from saved Claude Code conversations of this vault's Bots and kept in VIKI Agent's log for up to {days} days or {rows} calls.",
    "botUsage.notes.removedTranscripts.one":
      "1 conversation ({calls} calls) is only in the log: its saved conversation was removed.",
    "botUsage.notes.removedTranscripts.many":
      "{count} conversations ({calls} calls) are only in the log: their saved conversation was removed.",
    "botUsage.notes.noTranscript.one":
      "1 started conversation has no saved conversation and no log rows.",
    "botUsage.notes.noTranscript.many":
      "{count} started conversations have no saved conversation and no log rows.",
    "botUsage.notes.stillReading.one": "1 conversation is still being read. Refresh to continue.",
    "botUsage.notes.stillReading.many":
      "{count} conversations are still being read. Refresh to continue.",
    "botUsage.notes.logHolds": "The log holds {rows} calls since {date}.",
    "botUsage.notes.cleanupPeriod":
      "Claude Code removes saved conversations after 30 days unless cleanupPeriodDays is set.",
    "botUsage.notes.gatewayLimited":
      "Model connections that use Chat Completions or Responses report input and output tokens only.",

    // UsageTrace (the conversation trace).
    "botUsage.trace.heading": "Conversation trace",
    "botUsage.trace.line": "{bot} · {kind} · {route} · {calls}",
    "botUsage.trace.omitted": "Older calls not shown: {count}.",
    "botUsage.trace.turnLabel": "Turn {n}",
    "botUsage.trace.subagentRun": "Subagent run",
    "botUsage.trace.noTurnId": "Calls without a turn id",
    "botUsage.trace.turnSummary": "{label} · {calls} · {tokens}",
    "botUsage.trace.threadMain": "Main",
    "botUsage.trace.threadSubagent": "Subagent",
    "botUsage.trace.close": "Close",

    // BotUsage (the dashboard container).
    "botUsage.dashboard.title": "Bot usage",
    "botUsage.dashboard.period": "Period",
    "botUsage.dashboard.last7": "Last 7 days",
    "botUsage.dashboard.last30": "Last 30 days",
    "botUsage.dashboard.last90": "Last 90 days",
    "botUsage.dashboard.refresh": "Refresh",
    "botUsage.dashboard.loading": "Loading usage…",
    "botUsage.dashboard.error": "Usage could not be loaded. Refresh to try again.",
    "botUsage.dashboard.totalTokens": "Total tokens",
    "botUsage.dashboard.dailyTokens": "Daily tokens",
    "botUsage.dashboard.byBot": "By Bot",
    "botUsage.dashboard.otherBots": "Other Bots",
    "botUsage.dashboard.showTable": "Show table",

    // The Bots page nav (App.tsx).
    "botUsage.nav.usage": "Usage",
  },
});

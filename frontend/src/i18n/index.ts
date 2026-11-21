/**
 * Merges every i18n module into one English catalog, the only one VIKI Agent has. This file,
 * together with `t.ts`, is the only place it is assembled; every other module owns its own slice
 * and is merged here by nothing more than object spread.
 *
 * `defineCatalog` (what keeps each module's literal keys) lives in
 * its own dependency-free `defineCatalog.ts`, not here: every catalog module imports it from
 * there, and this file only imports catalog *values* from those modules. That makes the module
 * graph a plain DAG. Defined in this file, it would make every catalog module import back from
 * the module that imports them — safe only for an entry point that happened to load `index.ts`
 * or `t.ts` first; a view importing its own catalog module directly would hit a TDZ
 * `ReferenceError`. `i18n.unit.test.ts` guards against that.
 */

import { catalog as appearance } from "./appearance";
import { catalog as botHues } from "./botHues";
import { catalog as bots } from "./bots";
import { catalog as botUsage } from "./botUsage";
import { catalog as commands } from "./commands";
import { catalog as common } from "./common";
import { catalog as data } from "./data";
import { catalog as decisionPage } from "./decisionPage";
import { catalog as decisions } from "./decisions";
import { catalog as errorsCommon } from "./errors.common";
import { catalog as errorsData } from "./errors.data";
import { catalog as errorsDiagnostics } from "./errors.diagnostics";
import { catalog as errorsSettings } from "./errors.settings";
import { catalog as errorsWorkspaces } from "./errors.workspaces";
import { catalog as handsFree } from "./handsFree";
import { catalog as kanban } from "./kanban";
import { catalog as mcp } from "./mcp";
import { catalog as modelConnections } from "./modelConnections";
import { catalog as onboarding } from "./onboarding";
import { catalog as profiles } from "./profiles";
import { catalog as readAloud } from "./readAloud";
import { catalog as routines } from "./routines";
import { catalog as settings } from "./settings";
import { catalog as shell } from "./shell";
import { catalog as vault } from "./vault";
import { catalog as workspaces } from "./workspaces";
import { catalog as worktrees } from "./worktrees";

export { defineCatalog } from "./defineCatalog";

export const en = {
  ...bots.en,
  ...botUsage.en,
  ...botHues.en,
  ...common.en,
  ...commands.en,
  ...errorsCommon.en,
  ...errorsSettings.en,
  ...errorsWorkspaces.en,
  ...errorsDiagnostics.en,
  ...errorsData.en,
  ...shell.en,
  ...onboarding.en,
  ...settings.en,
  ...appearance.en,
  ...workspaces.en,
  ...worktrees.en,
  ...data.en,
  ...decisions.en,
  ...routines.en,
  ...readAloud.en,
  ...handsFree.en,
  ...mcp.en,
  ...modelConnections.en,
  ...kanban.en,
  ...decisionPage.en,
  ...vault.en,
  ...profiles.en,
};

/** Every catalog key, merged from every module. */
export type Key = keyof typeof en;

/** The modules `en` was merged from, exposed only so `i18n.unit.test.ts` can prove no key
 * was silently overwritten by a later spread (a duplicate across modules is otherwise invisible:
 * `{...a, ...b}` with the same key in both just keeps `b`'s value). Not for component use. */
export const modules = {
  bots,
  botUsage,
  botHues,
  common,
  commands,
  errorsCommon,
  errorsSettings,
  errorsWorkspaces,
  errorsDiagnostics,
  errorsData,
  shell,
  onboarding,
  settings,
  appearance,
  workspaces,
  worktrees,
  data,
  decisions,
  routines,
  readAloud,
  handsFree,
  mcp,
  modelConnections,
  kanban,
  decisionPage,
  vault,
  profiles,
};

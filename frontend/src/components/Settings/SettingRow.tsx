/**
 * One row of a settings page built from the document: the key's label and
 * description, one control per `kind`, the source and applies chips, the Reset button
 * and, when the last save said something, the row's own status or error line.
 *
 * Rules the row owns (everything else is `DocumentPage`'s):
 * - The control is chosen by `spec.kind`, never by the key, so a key the backend adds renders
 *   with a real control rather than nothing.
 * - A key whose `default` is `null` (the three `chat.*`) can hold "no value of its own", so its
 *   `<select>` carries a first option that means inherit; every other key resets through the
 *   button. `null` is what both send — the backend reads a `null` patch as "forget this key".
 * - The three non-empty string keys (`src/viki_agent/app/settings.py`'s `_NONEMPTY_STRING_KEYS`) send `null` when the
 *   field is cleared, never `""`: clearing means inherit, and `""` would come back as `too_long`
 *   on an empty field. `identity.display_name` may be `""` — the backend allows 0 there.
 * - `chat.default_model` has no client-side length check. Its bound is 1,024 UTF-8 bytes, which
 *   a `maxLength` in UTF-16 units cannot reproduce, so the server decides and the row shows the
 *   server's `too_long`.
 * - A read-only document does not disable the controls: they stay focusable with
 *   `aria-disabled="true"` and a no-op handler, and name the notice through `aria-describedby`
 *   — an unfocusable control is never announced, so the user would never learn
 *   why it does nothing. The text field is additionally `readOnly`, which is the honest native
 *   way to say "you may read and select this, but it will not be saved".
 * - `spec.kind === "number" && spec.bounds` renders a plain number input instead of the
 *   `<select>` the unbounded case uses: a string draft seeded like the text field's, saved on
 *   blur and Enter, refused inline (and never sent) when it is not a whole number in range.
 */

import { type ReactElement, useId, useState } from "react";
import type { Applies, KeySpec, SettingSource } from "../../clients/app/shellApi";
import { t, tf } from "../../i18n/t";
import { Check, Spinner } from "../Loading/Loading";
import { keyDesc, keyLabel, valueLabel } from "./pages";

/** The character bounds the client can reproduce exactly (`src/viki_agent/app/settings.py`'s `max_len` for the two
 * identity keys is counted in characters). `chat.default_model`'s is in bytes: see the module doc. */
const MAX_LENGTH: Readonly<Record<string, number>> = {
  "identity.display_name": 64,
  "identity.assistant_name": 32,
  "voice.voicebox_url": 32,
};

/** `src/viki_agent/app/settings.py`'s `_NONEMPTY_STRING_KEYS`. */
const NONEMPTY_KEYS: ReadonlySet<string> = new Set([
  "identity.assistant_name",
  "chat.default_model",
  "voice.voicebox_url",
]);

/** The row's meta line — the source label when it is not "default", the applies
 * label when it is not "now", joined " · " — or "" when neither is worth naming. */
function rowMeta(source: SettingSource, applies: Applies): string {
  const parts: string[] = [];
  if (source !== "default") parts.push(t(`common.source.${source}`));
  if (applies !== "now") parts.push(t(`common.applies.${applies}`));
  return parts.join(" · ");
}

/** The text-like controls' seed: the string kind's own value, or — for a bounded number — its
 * value turned into digits, so the same draft/seed mechanism below can edit either one. */
function committedText(spec: KeySpec, value: unknown): string {
  if (spec.kind === "string") return typeof value === "string" ? value : "";
  if (spec.kind === "number" && spec.bounds !== undefined)
    return typeof value === "number" ? String(value) : "";
  return "";
}

export type RowStatus = "saving" | "saved" | null;

export interface SettingRowProps {
  settingKey: string;
  spec: KeySpec;
  /** The effective value, whatever its source. */
  value: unknown;
  source: SettingSource;
  /** The id of the read-only notice when the document is read-only, else null. */
  readOnlyNoticeId: string | null;
  status: RowStatus;
  /** An already-resolved sentence (a `field.<code>` row, or `errorText`), never a server string. */
  error: string | null;
  /** The new value, or `null` to reset the key to what it inherits. */
  onChange: (key: string, value: unknown) => void;
  /** The values the select lists instead of `spec.allowed` (what is installed, say). */
  options?: readonly unknown[];
}

export function SettingRow({
  settingKey,
  spec,
  value,
  source,
  readOnlyNoticeId,
  status,
  error,
  onChange,
  options,
}: SettingRowProps): ReactElement {
  const base = useId();
  const controlId = `${base}-control`;
  const descId = `${base}-desc`;
  const errorId = `${base}-error`;
  const readOnly = readOnlyNoticeId !== null;
  const desc = keyDesc(settingKey);
  const resetUnsetId = `${base}-reset-reason`;
  // The read-only notice speaks for the whole page; otherwise the reset button's own sentence does.
  const resetReasonId = readOnlyNoticeId ?? (source === "setting" ? null : resetUnsetId);
  const meta = rowMeta(source, spec.applies);

  // The text field is the one control the user edits before the app hears about it, so it keeps
  // its own state. When the document moves under it — a reset, or the review panel adopting the
  // current document after a 409 — the field follows the document only if the user had typed
  // nothing of their own; otherwise their text stays in the control so they can save it again
  // against the new revision. Adjusted during render rather than in an effect: React
  // re-runs this component before painting, so no intermediate value is ever shown.
  const committed = committedText(spec, value);
  const [field, setField] = useState({ seed: committed, draft: committed });
  if (field.seed !== committed)
    setField({ seed: committed, draft: field.draft === field.seed ? committed : field.draft });
  const draft = field.draft;
  const setDraft = (next: string): void => setField((current) => ({ ...current, draft: next }));
  // The bounded number field's own client-side refusal: typing clears it, and the `error` prop
  // (a server-driven refusal) shows whenever this is null.
  const [numberError, setNumberError] = useState<string | null>(null);
  const shownError = numberError ?? error;

  const describedBy = [
    desc === "" ? null : descId,
    shownError === null ? null : errorId,
    readOnlyNoticeId,
  ]
    .filter((id): id is string => id !== null)
    .join(" ");
  const controlProps = {
    id: controlId,
    "aria-disabled": readOnly || undefined,
    "aria-describedby": describedBy === "" ? undefined : describedBy,
  } as const;

  function send(next: unknown): void {
    if (readOnly) return;
    onChange(settingKey, next);
  }

  function saveText(): void {
    if (readOnly || draft === committed) return;
    // Clearing a key that refuses "" means "inherit", which is a null patch, not an empty string.
    onChange(settingKey, draft === "" && NONEMPTY_KEYS.has(settingKey) ? null : draft);
  }

  function saveNumber(min: number, max: number): void {
    if (readOnly) return;
    const trimmed = draft.trim();
    if (trimmed === committed) return;
    if (!/^\d+$/.test(trimmed) || Number(trimmed) < min || Number(trimmed) > max) {
      setNumberError(tf("settings.number.range", { min, max }));
      return;
    }
    setNumberError(null);
    onChange(settingKey, Number(draft));
  }

  function control(): ReactElement {
    if (spec.kind === "bool")
      return (
        <input
          {...controlProps}
          type="checkbox"
          role="switch"
          className="settings-switch"
          checked={value === true}
          // `role="switch"` overrides the input's own implicit checked state, so the ARIA
          // attribute has to be written out for it; a switch without one announces nothing.
          aria-checked={value === true}
          readOnly={readOnly}
          onChange={(event) => send(event.target.checked)}
        />
      );
    if (spec.kind === "string")
      return (
        <input
          {...controlProps}
          type="text"
          className="settings-text"
          spellCheck={false}
          autoComplete="off"
          readOnly={readOnly}
          {...(MAX_LENGTH[settingKey] === undefined ? {} : { maxLength: MAX_LENGTH[settingKey] })}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={saveText}
          onKeyDown={(event) => {
            // The IME's Enter commits a conversion and is never a save (the composer's rule).
            if (event.key !== "Enter" || event.nativeEvent.isComposing || event.keyCode === 229)
              return;
            event.preventDefault();
            saveText();
          }}
        />
      );
    if (spec.kind === "number" && spec.bounds) {
      const [min, max] = spec.bounds;
      return (
        <input
          {...controlProps}
          type="number"
          inputMode="numeric"
          min={min}
          max={max}
          step={1}
          className="settings-text settings-number"
          readOnly={readOnly}
          value={draft}
          aria-invalid={shownError === null ? undefined : "true"}
          onChange={(event) => {
            setDraft(event.target.value);
            setNumberError(null);
          }}
          onBlur={() => saveNumber(min, max)}
          onKeyDown={(event) => {
            // The IME's Enter commits a conversion and is never a save (the composer's rule).
            if (event.key !== "Enter" || event.nativeEvent.isComposing || event.keyCode === 229)
              return;
            event.preventDefault();
            saveNumber(min, max);
          }}
        />
      );
    }
    // enum and an unbounded number are both a closed list, so both are a <select>; the number
    // key's values come back through `allowed`, which is what keeps `1` a number rather than "1".
    const allowed = options ?? spec.allowed ?? [];
    // A stored value the list no longer offers stays visible, but cannot be chosen again.
    const outsider =
      value !== null &&
      value !== undefined &&
      !allowed.some((candidate) => String(candidate) === String(value));
    const inheritable = spec.default === null;
    return (
      <select
        {...controlProps}
        className="settings-select"
        value={value === null || value === undefined ? "" : String(value)}
        onChange={(event) => {
          const index = allowed.findIndex((candidate) => String(candidate) === event.target.value);
          send(index < 0 ? null : allowed[index]);
        }}
      >
        {inheritable && (
          <option value="">
            {source === "setting"
              ? t("settings.row.reset_to_inherited")
              : tf("settings.value.inherit", { source: t(`common.source.${source}`) })}
          </option>
        )}
        {outsider && (
          <option value={String(value)} disabled>
            {tf("settings.value.unavailable", {
              value: valueLabel(settingKey, value) ?? String(value),
            })}
          </option>
        )}
        {allowed.map((candidate) => {
          const label = valueLabel(settingKey, candidate);
          // A value with no catalog row is shown as the raw token in mono, so it reads
          // as a wire value rather than as copy somebody wrote.
          return (
            <option
              key={String(candidate)}
              value={String(candidate)}
              {...(label === null ? { className: "mono" } : {})}
            >
              {label ?? String(candidate)}
            </option>
          );
        })}
      </select>
    );
  }

  return (
    <div className="settings-row" data-setting-key={settingKey}>
      <div className="settings-row-label">
        <label htmlFor={controlId}>{keyLabel(settingKey)}</label>
        {desc !== "" && (
          <p className="settings-row-desc" id={descId}>
            {desc}
          </p>
        )}
        {spec.personal && (
          <p className="settings-row-desc">{t("settings.row.personal_excluded")}</p>
        )}
      </div>
      <div className="settings-row-control">
        {control()}
        <div className="settings-row-status">
          {meta !== "" && <p className="settings-row-meta">{meta}</p>}
          <button
            type="button"
            className="btn txt sm settings-reset"
            aria-disabled={source !== "setting" || readOnly || undefined}
            {...(resetReasonId === null ? {} : { "aria-describedby": resetReasonId })}
            onClick={() => {
              if (source !== "setting") return;
              send(null);
            }}
          >
            {t("settings.row.reset_to_inherited")}
          </button>
          {/* A refused control names its reason. The read-only notice already explains the
              whole page; the ordinary refusal — nothing was set here, so nothing can be reset —
              has nowhere else to live, so it gets its own screen-reader-only sentence. */}
          {source !== "setting" && !readOnly && (
            <span className="sr-only" id={resetUnsetId}>
              {t("settings.row.reset_unavailable")}
            </span>
          )}
          {status !== null && (
            <span className="settings-row-saved" role="status">
              {status === "saving" ? <Spinner className="spinner-late" /> : <Check />}
              {t(status === "saving" ? "common.state.saving" : "common.state.saved")}
            </span>
          )}
        </div>
        {shownError !== null && (
          <p className="settings-error" role="alert" id={errorId}>
            {shownError}
          </p>
        )}
      </div>
    </div>
  );
}

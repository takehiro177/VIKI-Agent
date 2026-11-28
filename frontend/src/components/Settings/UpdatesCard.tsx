/**
 * Settings > Diagnostics and About: the automatic-update checkbox and the "Check now" button.
 * `auto`/`onAutoChange` write the `updates.check` setting the same way `BotLearningCard`'s own
 * auto-run switches write `bots.auto_reflect`/`bots.auto_dream` — a checkbox plus an `act()`
 * wrapper that reports a patch failure through `errorText`. `onCheckNow` is synchronous (it is
 * `viki.actions.checkForUpdates`, a local socket send, not a settings patch), so its result shows
 * at once rather than through a promise.
 */
import { useState } from "react";
import { ShellApiError } from "../../clients/app/shellApi";
import { errorText } from "../../i18n/t";
import "./Settings.css";

export interface UpdatesCardProps {
  auto: boolean;
  onAutoChange(value: boolean): Promise<void>;
  onCheckNow(): boolean;
}

export function UpdatesCard({ auto, onAutoChange, onCheckNow }: UpdatesCardProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState<boolean | null>(null);

  async function act(action: () => Promise<void>): Promise<void> {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (failure) {
      setError(
        errorText(failure instanceof ShellApiError ? failure : new ShellApiError(0, "unavailable")),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="updates-title" className="settings-card">
      <div className="settings-card-head">
        <h3 id="updates-title" className="settings-card-title">
          Updates
        </h3>
      </div>
      <label>
        <input
          type="checkbox"
          checked={auto}
          disabled={busy}
          onChange={(event) => void act(() => onAutoChange(event.target.checked))}
        />
        Check for updates automatically
      </label>
      <p className="settings-note">
        VIKI Agent asks GitHub for a newer release a minute after it starts and every 6 hours.
      </p>
      {error !== null ? (
        <p className="settings-error" role="alert">
          {error}
        </p>
      ) : null}
      <div className="settings-actions">
        <button type="button" className="btn alt sm" onClick={() => setSent(onCheckNow())}>
          Check now
        </button>
      </div>
      {sent !== null ? (
        <p role="status">
          {sent
            ? "Checking. VIKI Agent shows the result in a dialog."
            : "Couldn't ask VIKI Agent to check. Reopen this window and try again."}
        </p>
      ) : null}
    </section>
  );
}

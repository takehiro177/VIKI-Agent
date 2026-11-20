/**
 * App-level wiring for the missing-source rail: a pinned label this app still holds for a
 * session the current listing no longer has. sessionOrganization.unit.test.ts pins
 * `missingRecords` itself and MissingSessions.unit.test.tsx pins the component's own rendering;
 * this file pins only what App is responsible for supplying (the placement, the two actions,
 * telling a prepared-but-never-started profile session apart from an actually-gone transcript,
 * and quietly deleting an archive-only label whose transcript is gone).
 */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { App } from "../../App";
import { emptyRails, fakeRest, fakeViki, GENERATION, sessionRow } from "../../accept/m3b/fakes";
import type { Rest } from "../../api";
import {
  type SessionMetadata,
  type SessionMetadataApi,
  SessionMetadataError,
  type SessionMetadataPatch,
} from "../../clients/conversations/sessionMetadataApi";
import {
  type PreparedProfilePreview,
  type ProfileLaunchApi,
  ProfileLaunchApiError,
} from "../../clients/profiles/profileLaunchApi";
import type { SessionsResult } from "../../protocol";
import { fakeDurable } from "../../testDurable";

afterEach(() => {
  cleanup();
  sessionStorage.clear();
});

async function flush(): Promise<void> {
  await act(async () => {
    for (let i = 0; i < 5; i++) await Promise.resolve();
  });
}

function preparedPreview(sessionId: string, started = false): PreparedProfilePreview {
  return {
    started,
    launchable: true,
    fields: [],
    blockers: [],
    warnings: [],
    prefs: { mode: "default", effort: "high", model: "sonnet" },
    session_id: sessionId,
    profile_id: "profile",
    profile_revision: 1,
    generation: null,
    workspace_id: "workspace",
    workspace_revision: 1,
    policy: "ordinary",
    policy_revision: 1,
    settings_revision: 1,
    runtime: "claude",
    capability_key: "a".repeat(64),
    draft: { key: sessionId, revision: 1, text: "", saved_at: null, attachments: [] },
  };
}

/** Answers `preparedPreview` for `sessionId`, 404 `not_found` for any other id. */
function fakeProfileLaunchApiFor(sessionId: string): ProfileLaunchApi {
  return {
    capabilities: vi.fn(),
    models: vi.fn(),
    prepare: vi.fn(),
    inspect: vi.fn(async (id) =>
      id === sessionId
        ? preparedPreview(sessionId)
        : Promise.reject(new ProfileLaunchApiError(404, "not_found")),
    ),
  };
}

/** Holds every `inspect` open until `settle` answers the oldest one: a profile session from
 * `started` (true or false by id), 404 `not_found` for any other id. */
function heldProfileLaunchApi(started: ReadonlyMap<string, boolean>): ProfileLaunchApi & {
  calls: string[];
  settle: () => void;
} {
  const calls: string[] = [];
  const answers: (() => void)[] = [];
  return {
    calls,
    capabilities: vi.fn(),
    models: vi.fn(),
    prepare: vi.fn(),
    inspect: (id) => {
      calls.push(id);
      return new Promise<PreparedProfilePreview>((resolve, reject) => {
        answers.push(() => {
          const value = started.get(id);
          if (value === undefined) reject(new ProfileLaunchApiError(404, "not_found"));
          else resolve(preparedPreview(id, value));
        });
      });
    },
    settle: () => answers.shift()?.(),
  };
}

/** How `profileLaunchApiBy` answers an id: draft (started false) and started (true) resolve;
 * busy (429 overloaded), down (503 unavailable), offline (0 unavailable) and no_route (a plain
 * 404 with no error code, which the client reads as `unavailable`) reject. */
type InspectOutcome = "draft" | "started" | "busy" | "down" | "offline" | "no_route";

/** Answers every `inspect` at once from `outcomes`, 404 `not_found` for any other id; `calls`
 * gets each id asked, in order. */
function profileLaunchApiBy(
  outcomes: ReadonlyMap<string, InspectOutcome> = new Map(),
): ProfileLaunchApi & { calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    capabilities: vi.fn(),
    models: vi.fn(),
    prepare: vi.fn(),
    inspect: async (id) => {
      calls.push(id);
      const outcome = outcomes.get(id);
      if (outcome === "draft" || outcome === "started")
        return preparedPreview(id, outcome === "started");
      if (outcome === "busy") throw new ProfileLaunchApiError(429, "overloaded");
      if (outcome === "down") throw new ProfileLaunchApiError(503, "unavailable");
      if (outcome === "offline") throw new ProfileLaunchApiError(0, "unavailable");
      if (outcome === "no_route") throw new ProfileLaunchApiError(404, "unavailable");
      throw new ProfileLaunchApiError(404, "not_found");
    },
  };
}

function metadataRow(
  overrides: Partial<SessionMetadata> & { session_id: string },
): SessionMetadata {
  return { revision: 1, title: "", pinned: false, archived: false, ...overrides };
}

/** What `remove` does: deleted (the default) drops the row and resolves true; gone drops it and
 * resolves false, as the server does for a row another window already deleted; conflict and
 * offline throw and keep the row. */
type RemoveOutcome = "deleted" | "gone" | "conflict" | "offline";

/** Resolves `list` immediately; tracks every `patch`/`remove` call for assertions. `order`, when
 * given, also gets each call, so a caller can compare its position against another fake's. */
function instantMetadataApi(
  rows: SessionMetadata[],
  order: string[] = [],
  removeOutcome: RemoveOutcome = "deleted",
): SessionMetadataApi & {
  patchCalls: { sessionId: string; patch: SessionMetadataPatch }[];
  removeCalls: string[];
} {
  let current = rows;
  const patchCalls: { sessionId: string; patch: SessionMetadataPatch }[] = [];
  const removeCalls: string[] = [];
  return {
    patchCalls,
    removeCalls,
    async list() {
      return current;
    },
    async patch(sessionId, _expectedRevision, patch) {
      patchCalls.push({ sessionId, patch });
      const row = current.find((item) => item.session_id === sessionId);
      if (!row) throw new Error("missing row");
      const updated = { ...row, ...patch };
      current = current.map((item) => (item.session_id === sessionId ? updated : item));
      return updated;
    },
    async remove(sessionId) {
      order.push(`metadata.remove:${sessionId}`);
      removeCalls.push(sessionId);
      if (removeOutcome === "conflict") throw new SessionMetadataError(409, "conflict", 2);
      if (removeOutcome === "offline") throw new Error("offline");
      current = current.filter((item) => item.session_id !== sessionId);
      return removeOutcome === "deleted";
    },
  };
}

/** `/api/sessions` answers `partial` (one transcript omitted) until `turnReady()` is called,
 * then `ready`. */
function partialThenReadyRest(): Rest & { turnReady: () => void } {
  let ready = false;
  return {
    turnReady: () => {
      ready = true;
    },
    sessions: async () => ({
      sessions: [],
      state: ready ? "ready" : "partial",
      reason: ready ? "" : "time_budget",
      omitted: ready ? 0 : 1,
    }),
    search: async () => ({
      hits: [],
      state: "ready",
      reason: "",
      scanned: 0,
      total: 0,
      request_id: "",
    }),
    rails: async () => emptyRails(),
  };
}

test("a partial listing shows no record; the same listing turned ready shows it", async () => {
  vi.useFakeTimers();
  try {
    const metadata = instantMetadataApi([
      metadataRow({ session_id: "missing-2", title: "Partial title", pinned: true }),
    ]);
    const rest = partialThenReadyRest();
    const { viki, socket } = fakeViki();
    render(<App viki={viki} rest={rest} durable={fakeDurable()} organization={metadata} />);
    await act(async () => {
      socket.open();
    });
    await flush();
    // The label is already known; only the listing is partial.
    expect(screen.queryByText("Partial title")).toBeNull();

    rest.turnReady();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000); // the store's own poll interval
    });
    await flush();
    expect(screen.getByText("Partial title")).not.toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

/** Holds every `sessions()` call open until `resolveCall`/`rejectCall` settles that call number
 * (1-based); each call gets its own resolver, so an earlier one can still be answered after a
 * later one has started. */
function holdingRest(): Rest & {
  resolveCall: (n: number, result: SessionsResult) => void;
  rejectCall: (n: number, error: unknown) => void;
} {
  let call = 0;
  const resolvers = new Map<number, (result: SessionsResult) => void>();
  const rejectors = new Map<number, (error: unknown) => void>();
  return {
    resolveCall: (n, result) => resolvers.get(n)?.(result),
    rejectCall: (n, error) => rejectors.get(n)?.(error),
    sessions: () => {
      call += 1;
      const n = call;
      return new Promise<SessionsResult>((resolve, reject) => {
        resolvers.set(n, resolve);
        rejectors.set(n, reject);
      });
    },
    search: async () => ({
      hits: [],
      state: "ready",
      reason: "",
      scanned: 0,
      total: 0,
      request_id: "",
    }),
    rails: async () => emptyRails(),
  };
}

test("a held first listing read shows no section, until it settles ready", async () => {
  const metadata = instantMetadataApi([
    metadataRow({ session_id: "missing-held", title: "Held title", pinned: true }),
  ]);
  const rest = holdingRest();
  const { viki, socket } = fakeViki();
  const { container } = render(
    <App viki={viki} rest={rest} durable={fakeDurable()} organization={metadata} />,
  );
  await act(async () => {
    socket.open();
  });
  await flush();
  // The label is already known; only the first listing read is still in flight.
  expect(container.querySelector(".missing-sessions")).toBeNull();

  await act(async () => {
    rest.resolveCall(1, { sessions: [], state: "ready", reason: "", omitted: 0 });
    await flush();
  });
  expect(screen.getByText("Held title")).not.toBeNull();
});

test("a shown section hides on a partial poll read, and stays hidden while the next read is in flight", async () => {
  vi.useFakeTimers();
  try {
    const metadata = instantMetadataApi([
      metadataRow({ session_id: "missing-partial", title: "Partial poll title", pinned: true }),
    ]);
    const rest = holdingRest();
    const { viki, socket } = fakeViki();
    const { container } = render(
      <App viki={viki} rest={rest} durable={fakeDurable()} organization={metadata} />,
    );
    await act(async () => {
      socket.open();
    });
    await flush();
    await act(async () => {
      rest.resolveCall(1, { sessions: [], state: "ready", reason: "", omitted: 0 });
      await flush();
    });
    expect(screen.getByText("Partial poll title")).not.toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000); // the store's own poll interval, starts call 2
    });
    await act(async () => {
      rest.resolveCall(2, { sessions: [], state: "partial", reason: "time_budget", omitted: 1 });
      await flush();
    });
    expect(container.querySelector(".missing-sessions")).toBeNull();

    // The next poll's read (call 3) is in flight; the section stays hidden through it too.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(container.querySelector(".missing-sessions")).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

test("a shown section hides on a rejected poll read, and stays hidden while the next read is in flight", async () => {
  vi.useFakeTimers();
  try {
    const metadata = instantMetadataApi([
      metadataRow({ session_id: "missing-rejected", title: "Rejected poll title", pinned: true }),
    ]);
    const rest = holdingRest();
    const { viki, socket } = fakeViki();
    const { container } = render(
      <App viki={viki} rest={rest} durable={fakeDurable()} organization={metadata} />,
    );
    await act(async () => {
      socket.open();
    });
    await flush();
    await act(async () => {
      rest.resolveCall(1, { sessions: [], state: "ready", reason: "", omitted: 0 });
      await flush();
    });
    expect(screen.getByText("Rejected poll title")).not.toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000); // the store's own poll interval, starts call 2
    });
    await act(async () => {
      rest.rejectCall(2, new Error("network"));
      await flush();
    });
    expect(container.querySelector(".missing-sessions")).toBeNull();

    // The next poll's read (call 3) is in flight; the section stays hidden through it too.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(container.querySelector(".missing-sessions")).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

test("Unpin patches the label", async () => {
  const metadata = instantMetadataApi([
    metadataRow({ session_id: "missing-1", title: "Pinned title", pinned: true }),
  ]);
  const { viki, socket } = fakeViki();
  render(<App viki={viki} rest={fakeRest()} durable={fakeDurable()} organization={metadata} />);
  await act(async () => {
    socket.open();
  });
  await flush();
  await act(async () => {
    fireEvent.click(await screen.findByRole("button", { name: "Unpin" }));
  });
  expect(metadata.patchCalls).toEqual([{ sessionId: "missing-1", patch: { pinned: false } }]);
});

test("a held session never shows", async () => {
  const metadata = instantMetadataApi([
    metadataRow({ session_id: "held-1", title: "Held convo", pinned: true }),
  ]);
  const { viki, socket } = fakeViki();
  render(<App viki={viki} rest={fakeRest()} durable={fakeDurable()} organization={metadata} />);
  await act(async () => {
    socket.open();
  });
  act(() => {
    viki.actions.openSession("held-1");
  });
  await flush();
  expect(screen.queryByText("Held convo")).toBeNull();
});

// Restores the coverage a deleted queue-only test once gave: a session this window never opened
// (so `heldIds` alone misses it) still never shows, because the vault's own `vault.sessions`
// report still names it live (App.tsx's `missingHeld`). A pinned label makes it a candidate in
// the label's place, since a queue can no longer do that on its own.
test("a session the vault still reports live never shows, even with a qualifying label", async () => {
  const metadata = instantMetadataApi([
    metadataRow({ session_id: "live-elsewhere-1", title: "Live elsewhere", pinned: true }),
  ]);
  const { viki, socket } = fakeViki();
  render(<App viki={viki} rest={fakeRest()} durable={fakeDurable()} organization={metadata} />);
  await act(async () => {
    socket.open();
  });
  await flush();
  act(() => {
    socket.receive({
      v: 2,
      generation: GENERATION,
      request_id: "",
      type: "vault.sessions",
      session_id: "",
      seq: 1,
      data: { others: [], live: ["live-elsewhere-1"] },
    });
  });
  await flush();
  expect(screen.queryByText("Live elsewhere")).toBeNull();
});

test("nothing in the section opens a session", async () => {
  const metadata = instantMetadataApi([
    metadataRow({ session_id: "missing-1", title: "Pinned title", pinned: true }),
  ]);
  const { viki, socket } = fakeViki();
  render(
    <App
      viki={viki}
      rest={fakeRest({ sessions: [sessionRow({ session_id: "other", title: "Other" })] })}
      durable={fakeDurable()}
      organization={metadata}
    />,
  );
  await act(async () => {
    socket.open();
  });
  await flush();
  const openSpy = vi.spyOn(viki.actions, "openSession");
  fireEvent.click(await screen.findByText("Pinned title"));
  expect(openSpy).not.toHaveBeenCalled();
});

test("a prepared, never-started profile session shows as a profile draft with Open and no Remove, and Open selects it", async () => {
  const metadata = instantMetadataApi([
    metadataRow({ session_id: "profile-draft-1", title: "Draft convo", pinned: true, revision: 1 }),
  ]);
  const { viki, socket } = fakeViki();
  const openSpy = vi.spyOn(viki.actions, "openSession");
  render(
    <App
      viki={viki}
      rest={fakeRest()}
      durable={fakeDurable()}
      organization={metadata}
      profileLaunchApi={fakeProfileLaunchApiFor("profile-draft-1")}
    />,
  );
  await act(async () => {
    socket.open();
  });
  await flush();
  await waitFor(() => expect(screen.getByText("Profile draft, not started")).not.toBeNull());
  expect(screen.queryByText("Source missing")).toBeNull();
  expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();

  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
  });
  expect(openSpy).toHaveBeenCalledWith("profile-draft-1");
});

test("missing records are inspected one at a time, at most 8, and only a never-started profile draft opens", async () => {
  // Sorted by id: the two profile sessions first, then the gone records; the ninth is never read.
  const rows: [string, string][] = [
    ["a-draft", "Draft convo"],
    ["a-started", "Started convo"],
    ...Array.from({ length: 7 }, (_, i): [string, string] => [`m-${i}`, `Gone convo ${i}`]),
  ];
  const metadata = instantMetadataApi(
    rows.map(([session_id, title]) => metadataRow({ session_id, title })),
  );
  const launch = heldProfileLaunchApi(
    new Map([
      ["a-draft", false],
      ["a-started", true],
    ]),
  );
  const { viki, socket } = fakeViki();
  const { container } = render(
    <App
      viki={viki}
      rest={fakeRest()}
      durable={fakeDurable()}
      organization={metadata}
      profileLaunchApi={launch}
    />,
  );
  await act(async () => {
    socket.open();
  });
  await waitFor(() => expect(launch.calls).toHaveLength(1));
  for (let settled = 1; settled <= 8; settled++) {
    // The next inspection starts only once the previous one has settled.
    expect(launch.calls).toHaveLength(settled);
    await act(async () => {
      launch.settle();
    });
    await flush();
  }
  expect(launch.calls).toEqual(rows.slice(0, 8).map(([id]) => id));

  const row = (title: string) => within(screen.getByText(title).closest("li") as HTMLElement);
  expect(row("Draft convo").getByText("Profile draft, not started")).not.toBeNull();
  for (const title of ["Started convo", "Gone convo 0", "Gone convo 6"]) {
    expect(row(title).getByText("Source missing")).not.toBeNull();
    expect(row(title).queryByRole("button", { name: "Open" })).toBeNull();
  }

  await act(async () => {
    fireEvent.click(row("Draft convo").getByRole("button", { name: "Open" }));
  });
  await flush();
  expect(container.querySelector(".conversation-title")?.textContent).toBe("Draft convo");
});

const NOTICE = "Session settings could not be loaded or saved.";

/** Renders App with the label store and the profile client, then opens its socket. */
async function renderRail(
  metadata: SessionMetadataApi,
  launch: ProfileLaunchApi,
  rest: Rest = fakeRest(),
): Promise<ReturnType<typeof fakeViki>> {
  const fake = fakeViki();
  render(
    <App
      viki={fake.viki}
      rest={rest}
      durable={fakeDurable()}
      organization={metadata}
      profileLaunchApi={launch}
    />,
  );
  await act(async () => {
    fake.socket.open();
  });
  return fake;
}

/** The rail row that shows `label`. */
function missingRow(label: string) {
  return within(screen.getByText(label).closest("li") as HTMLElement);
}

test("an archive-only label whose transcript is gone is deleted quietly and never listed", async () => {
  // Each labelled row differs from the archive-only one by one mark of its own.
  const metadata = instantMetadataApi([
    metadataRow({ session_id: "archived-only", archived: true }),
    metadataRow({ session_id: "titled-1", archived: true, title: "Titled convo" }),
    metadataRow({ session_id: "pinned-1", archived: true, pinned: true }),
    metadataRow({ session_id: "colored-1", archived: true, color: "blue" }),
    metadataRow({ session_id: "unread-1", archived: true, unread: true }),
  ]);
  const launch = heldProfileLaunchApi(new Map());
  await renderRail(metadata, launch);
  await waitFor(() => expect(launch.calls).toHaveLength(1));
  for (let settled = 1; settled <= 5; settled++) {
    expect(launch.calls).toHaveLength(settled);
    // Still waiting for its answer, the archive-only record never shows.
    expect(screen.queryByText("Untitled session (archived)")).toBeNull();
    await act(async () => {
      launch.settle(); // 404 not_found
    });
    await flush();
  }
  // The labelled records come first (pinned, then by id), the archive-only one after them.
  expect(launch.calls).toEqual(["pinned-1", "colored-1", "titled-1", "unread-1", "archived-only"]);
  await waitFor(() => expect(metadata.removeCalls).toEqual(["archived-only"]));
  expect(screen.queryByText("Untitled session (archived)")).toBeNull();
  for (const label of [
    "Titled convo",
    "Untitled session (pinned-1)",
    "Untitled session (colored-)",
    "Untitled session (unread-1)",
  ]) {
    expect(missingRow(label).getByText("Source missing")).not.toBeNull();
    expect(missingRow(label).getByRole("button", { name: "Remove" })).not.toBeNull();
  }
  const section = within(
    screen.getByRole("region", { name: "Conversations whose record is missing" }),
  );
  expect(section.getAllByRole("button", { name: "Unpin" })).toHaveLength(1);
  expect(
    missingRow("Untitled session (pinned-1)").getByRole("button", { name: "Unpin" }),
  ).not.toBeNull();
  expect(screen.queryByText(NOTICE)).toBeNull();
});

test("an archive-only profile draft shows with Open and is kept", async () => {
  const metadata = instantMetadataApi([
    metadataRow({ session_id: "archived-draft", archived: true }),
  ]);
  await renderRail(metadata, profileLaunchApiBy(new Map([["archived-draft", "draft"]])));
  await waitFor(() => expect(screen.getByText("Profile draft, not started")).not.toBeNull());
  const row = missingRow("Untitled session (archived)");
  expect(row.getByRole("button", { name: "Open" })).not.toBeNull();
  expect(row.queryByRole("button", { name: "Remove" })).toBeNull();
  expect(metadata.removeCalls).toEqual([]);
});

test("an archive-only label is kept and listed unless inspect answers 404 not_found", async () => {
  const outcomes = new Map<string, InspectOutcome>([
    ["started1", "started"],
    ["busy0001", "busy"],
    ["down0001", "down"],
    ["offline1", "offline"],
    ["noroute1", "no_route"],
  ]);
  const metadata = instantMetadataApi(
    [...outcomes.keys(), "gone0001"].map((session_id) =>
      metadataRow({ session_id, archived: true }),
    ),
  );
  const launch = profileLaunchApiBy(outcomes);
  await renderRail(metadata, launch);
  await waitFor(() => expect(screen.getAllByText("Source missing")).toHaveLength(5));
  expect(launch.calls).toHaveLength(6);
  expect(metadata.removeCalls).toEqual(["gone0001"]);
  for (const id of outcomes.keys()) {
    const row = missingRow(`Untitled session (${id})`);
    expect(row.getByText("Source missing")).not.toBeNull();
    expect(row.getByRole("button", { name: "Remove" })).not.toBeNull();
  }
  expect(screen.queryByText("Untitled session (gone0001)")).toBeNull();
  expect(screen.queryByText(NOTICE)).toBeNull();
});

test("an archive-only label left by an Unpin stays listed and is not read again", async () => {
  const metadata = instantMetadataApi([
    metadataRow({ session_id: "pinned-archived", archived: true, pinned: true }),
  ]);
  const launch = profileLaunchApiBy();
  await renderRail(metadata, launch);
  await waitFor(() => expect(launch.calls).toEqual(["pinned-archived"]));
  await act(async () => {
    fireEvent.click(
      missingRow("Untitled session (pinned-a)").getByRole("button", { name: "Unpin" }),
    );
  });
  await flush();
  expect(metadata.patchCalls).toEqual([{ sessionId: "pinned-archived", patch: { pinned: false } }]);
  const row = missingRow("Untitled session (pinned-a)");
  expect(row.queryByRole("button", { name: "Unpin" })).toBeNull();
  expect(row.getByText("Source missing")).not.toBeNull();
  expect(row.getByRole("button", { name: "Remove" })).not.toBeNull();
  expect(launch.calls).toEqual(["pinned-archived"]);
  expect(metadata.removeCalls).toEqual([]);
});

test("a failed clear stays quiet and is not retried; a lost race drops the row", async () => {
  vi.useFakeTimers();
  try {
    for (const outcome of ["conflict", "offline"] as const) {
      const metadata = instantMetadataApi(
        [metadataRow({ session_id: "archived-1", archived: true })],
        [],
        outcome,
      );
      await renderRail(metadata, profileLaunchApiBy());
      await flush();
      const expectListedQuietly = () => {
        const row = missingRow("Untitled session (archived)");
        expect(row.getByText("Source missing")).not.toBeNull();
        expect(row.getByRole("button", { name: "Remove" })).not.toBeNull();
        expect(screen.queryByText(NOTICE)).toBeNull();
      };
      expect(metadata.removeCalls).toEqual(["archived-1"]);
      expectListedQuietly();
      for (let poll = 1; poll <= 2; poll++) {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(60_000); // the store's own poll interval
        });
        await flush();
      }
      expect(metadata.removeCalls).toEqual(["archived-1"]);
      expectListedQuietly();
      cleanup();
      sessionStorage.clear();
    }

    // Another window deleted the label first: the DELETE answers 404, and the row just drops.
    const metadata = instantMetadataApi(
      [metadataRow({ session_id: "archived-2", archived: true })],
      [],
      "gone",
    );
    await renderRail(metadata, profileLaunchApiBy());
    await flush();
    expect(metadata.removeCalls).toEqual(["archived-2"]);
    expect(screen.queryByText("Untitled session (archived)")).toBeNull();
    expect(screen.queryByText(NOTICE)).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

test("no clear while the listing is partial, or for a held or live session", async () => {
  vi.useFakeTimers();
  try {
    const metadata = instantMetadataApi(
      ["archived-gone", "archived-held", "archived-live"].map((session_id) =>
        metadataRow({ session_id, archived: true }),
      ),
    );
    const launch = profileLaunchApiBy();
    const rest = partialThenReadyRest();
    const { viki, socket } = await renderRail(metadata, launch, rest);
    act(() => {
      viki.actions.openSession("archived-held");
    });
    act(() => {
      socket.receive({
        v: 2,
        generation: GENERATION,
        request_id: "",
        type: "vault.sessions",
        session_id: "",
        seq: 1,
        data: { others: [], live: ["archived-live"] },
      });
    });
    await flush();
    expect(launch.calls).toEqual([]);
    expect(metadata.removeCalls).toEqual([]);

    rest.turnReady();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000); // the store's own poll interval
    });
    await flush();
    // Only the record neither held nor live is read, and cleared.
    expect(launch.calls).toEqual(["archived-gone"]);
    expect(metadata.removeCalls).toEqual(["archived-gone"]);
  } finally {
    vi.useRealTimers();
  }
});

test("a listing that turns partial before the answer clears nothing", async () => {
  vi.useFakeTimers();
  try {
    const metadata = instantMetadataApi([
      metadataRow({ session_id: "archived-1", archived: true }),
    ]);
    const launch = heldProfileLaunchApi(new Map());
    const rest = holdingRest();
    await renderRail(metadata, launch, rest);
    await flush();
    await act(async () => {
      rest.resolveCall(1, { sessions: [], state: "ready", reason: "", omitted: 0 });
      await flush();
    });
    expect(launch.calls).toEqual(["archived-1"]);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000); // the store's own poll interval, starts call 2
    });
    await act(async () => {
      rest.resolveCall(2, { sessions: [], state: "partial", reason: "time_budget", omitted: 1 });
      await flush();
    });
    await act(async () => {
      launch.settle(); // 404 not_found, after the listing turned partial
    });
    await flush();
    expect(metadata.removeCalls).toEqual([]);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000); // starts call 3
    });
    await act(async () => {
      rest.resolveCall(3, { sessions: [], state: "ready", reason: "", omitted: 0 });
      await flush();
    });
    expect(missingRow("Untitled session (archived)").getByText("Source missing")).not.toBeNull();
    expect(launch.calls).toEqual(["archived-1"]);
    expect(metadata.removeCalls).toEqual([]);
  } finally {
    vi.useRealTimers();
  }
});

test("archive-only records have their own budget of 64", async () => {
  // Eight labelled records use up their own 8 inspects; the archive-only one, sorted last, is
  // still read and deleted.
  const titled = Array.from({ length: 8 }, (_, i) => `m-${i}`);
  const first = instantMetadataApi([
    ...titled.map((session_id, i) => metadataRow({ session_id, title: `Gone convo ${i}` })),
    metadataRow({ session_id: "z-archived", archived: true }),
  ]);
  const firstLaunch = profileLaunchApiBy();
  await renderRail(first, firstLaunch);
  await waitFor(() => expect(first.removeCalls).toEqual(["z-archived"]));
  expect(firstLaunch.calls).toEqual([...titled, "z-archived"]);
  for (let i = 0; i < 8; i++)
    expect(missingRow(`Gone convo ${i}`).getByText("Source missing")).not.toBeNull();
  cleanup();
  sessionStorage.clear();

  // 65 archive-only records: 64 are read and deleted, the one past the budget stays listed.
  const ids = Array.from({ length: 65 }, (_, i) => `a-${String(i).padStart(2, "0")}`);
  const second = instantMetadataApi(
    ids.map((session_id) => metadataRow({ session_id, archived: true })),
  );
  const secondLaunch = profileLaunchApiBy();
  await renderRail(second, secondLaunch);
  await waitFor(() => expect(second.removeCalls).toHaveLength(64));
  await waitFor(() => expect(screen.getByText("Untitled session (a-64)")).not.toBeNull());
  expect(secondLaunch.calls).toEqual(ids.slice(0, 64));
  expect(second.removeCalls).toEqual(ids.slice(0, 64));
  expect(screen.getAllByText("Source missing")).toHaveLength(1);
});

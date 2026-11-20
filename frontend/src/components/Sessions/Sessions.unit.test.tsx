import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import {
  type SessionMetadata,
  SessionMetadataError,
} from "../../clients/conversations/sessionMetadataApi";
import { readCss, ruleIn } from "../../testing/css";
import { SessionOrganizer, SessionTabs, useSessionMetadata } from "./Sessions";

afterEach(cleanup);
const session: SessionMetadata = {
  session_id: "123e4567-e89b-12d3-a456-426614174000",
  revision: 1,
  title: "Chat",
  pinned: false,
  archived: false,
};

test("tab close is a UI callback and does not select the session", () => {
  const select = vi.fn();
  const close = vi.fn();
  render(
    <SessionTabs
      sessions={[session]}
      selectedId={session.session_id}
      onSelect={select}
      onClose={close}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Close: Chat" }));
  expect(close).toHaveBeenCalledWith(session.session_id);
  expect(select).not.toHaveBeenCalled();
});

test("keyboard arrows move selection and Delete only invokes close", () => {
  const second = {
    ...session,
    session_id: "223e4567-e89b-12d3-a456-426614174000",
    title: "Second",
  };
  const select = vi.fn();
  const close = vi.fn();
  render(
    <SessionTabs
      sessions={[session, second]}
      selectedId={session.session_id}
      onSelect={select}
      onClose={close}
    />,
  );
  const firstTab = screen.getByRole("tab", { name: /Chat/ });
  fireEvent.keyDown(firstTab, { key: "ArrowRight" });
  expect(select).toHaveBeenCalledWith(second.session_id);
  fireEvent.keyDown(firstTab, { key: "Delete" });
  expect(close).toHaveBeenCalledWith(session.session_id);
  expect(select).toHaveBeenCalledTimes(1);
});

test("organizer exposes pin/archive/rename and offers reload after a failed CAS", async () => {
  const patch = vi.fn(async () => null);
  const reload = vi.fn();
  render(<SessionOrganizer session={session} onPatch={patch} onReload={reload} />);
  fireEvent.change(screen.getByRole("textbox", { name: "Rename" }), {
    target: { value: "Renamed" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByRole("alert")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Reload" }));
  expect(reload).toHaveBeenCalled();
  expect(patch).toHaveBeenCalledWith({ title: "Renamed" });
});

test("a 409 from the metadata hook is reported as a conflict, not a generic failure", async () => {
  const api = {
    list: vi.fn(async () => [session]),
    patch: vi.fn(async () => {
      throw new SessionMetadataError(409, "conflict", 2);
    }),
    remove: vi.fn(),
  };
  function Harness() {
    const store = useSessionMetadata(api);
    return (
      <SessionOrganizer
        session={session}
        onPatch={(patch) => store.update(session.session_id, patch)}
        pending={store.pending.has(session.session_id)}
        errorCode={store.error}
      />
    );
  }
  render(<Harness />);
  await waitFor(() => expect(api.list).toHaveBeenCalled());
  fireEvent.change(screen.getByRole("textbox", { name: "Rename" }), {
    target: { value: "Renamed" },
  });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save" })));
  expect(screen.getByText("The session changed elsewhere. Reload before saving.")).toBeTruthy();
});

test("metadata hook uses expected revision zero for an unknown session", async () => {
  const created = { ...session, revision: 1, title: "Created" };
  const api = {
    list: vi.fn(async () => []),
    patch: vi.fn(async (id: string, revision: number) => {
      expect(id).toBe(session.session_id);
      expect(revision).toBe(0);
      return created;
    }),
    remove: vi.fn(),
  };
  function Harness() {
    const store = useSessionMetadata(api);
    return (
      <button
        type="button"
        onClick={() => void store.update(session.session_id, { title: "Created" })}
      >
        create
      </button>
    );
  }
  render(<Harness />);
  await waitFor(() => expect(api.list).toHaveBeenCalled());
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "create" })));
  expect(api.patch).toHaveBeenCalledWith(session.session_id, 0, { title: "Created" });
});

test("a reload already in flight when the remove happens does not bring the row back", async () => {
  let resolveList: (rows: SessionMetadata[]) => void = () => {};
  const api = {
    list: vi.fn(() => new Promise<SessionMetadata[]>((resolve) => (resolveList = resolve))),
    patch: vi.fn(),
    remove: vi.fn(async () => true),
  };
  function Harness() {
    const store = useSessionMetadata(api);
    return (
      <div>
        <span data-testid="count">{store.sessions.length}</span>
        <button type="button" onClick={() => void store.remove(session.session_id)}>
          remove
        </button>
      </div>
    );
  }
  render(<Harness />);
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "remove" })));
  expect(api.remove).toHaveBeenCalledWith(session.session_id, 0);
  expect(screen.getByTestId("count").textContent).toBe("0");
  await act(async () => resolveList([session]));
  expect(screen.getByTestId("count").textContent).toBe("0");
});

test("an in-flight reload does not resurrect a session recreated after its removal", async () => {
  let resolveList: (rows: SessionMetadata[]) => void = () => {};
  const oldRow = { ...session, revision: 3, title: "Old" };
  const recreated = { ...session, revision: 1, title: "New" };
  const api = {
    list: vi.fn(() => new Promise<SessionMetadata[]>((resolve) => (resolveList = resolve))),
    patch: vi.fn(async () => recreated),
    remove: vi.fn(async () => true),
  };
  function Harness() {
    const store = useSessionMetadata(api);
    const found = store.sessions.find((item) => item.session_id === session.session_id);
    return (
      <div>
        <span data-testid="title">{found?.title ?? "gone"}</span>
        <button type="button" onClick={() => void store.remove(session.session_id)}>
          remove
        </button>
        <button
          type="button"
          onClick={() => void store.update(session.session_id, { title: "New" })}
        >
          update
        </button>
      </div>
    );
  }
  render(<Harness />);
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "remove" })));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "update" })));
  expect(screen.getByTestId("title").textContent).toBe("New");
  // The stale row from before the delete lands only now; it must not overwrite the re-create.
  await act(async () => resolveList([oldRow]));
  expect(screen.getByTestId("title").textContent).toBe("New");
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "update" })));
  expect(api.patch).toHaveBeenLastCalledWith(session.session_id, 1, { title: "New" });
});

test("an update that re-creates a removed session picks up a later reload's server row", async () => {
  const recreated = { ...session, revision: 2, title: "Back" };
  // A later reload sees a row another window has since moved to revision 3.
  const movedElsewhere = { ...session, revision: 3, title: "Moved elsewhere" };
  const api = {
    list: vi.fn(async () => [session]),
    patch: vi.fn(async () => recreated),
    remove: vi.fn(async () => true),
  };
  function Harness() {
    const store = useSessionMetadata(api);
    const found = store.sessions.find((item) => item.session_id === session.session_id);
    return (
      <div>
        <span data-testid="title">{found?.title ?? "gone"}</span>
        <button type="button" onClick={() => void store.remove(session.session_id)}>
          remove
        </button>
        <button
          type="button"
          onClick={() => void store.update(session.session_id, { title: "Back" })}
        >
          update
        </button>
        <button type="button" onClick={() => void store.reload()}>
          reload
        </button>
      </div>
    );
  }
  render(<Harness />);
  await waitFor(() => expect(screen.getByTestId("title").textContent).toBe("Chat"));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "remove" })));
  expect(screen.getByTestId("title").textContent).toBe("gone");
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "update" })));
  expect(screen.getByTestId("title").textContent).toBe("Back");
  api.list.mockResolvedValueOnce([movedElsewhere]);
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "reload" })));
  // This reload starts after the remove, so its generation is newer than the tombstone's stamp:
  // the row is not hidden, and the fresher server revision from another window wins.
  expect(screen.getByTestId("title").textContent).toBe("Moved elsewhere");
});

test("a 409 from remove is reported as a conflict and keeps the row", async () => {
  const api = {
    list: vi.fn(async () => [session]),
    patch: vi.fn(),
    remove: vi.fn(async () => {
      throw new SessionMetadataError(409, "conflict", 2);
    }),
  };
  function Harness() {
    const store = useSessionMetadata(api);
    return (
      <div>
        <span data-testid="count">{store.sessions.length}</span>
        <span data-testid="error">{store.error}</span>
        <button type="button" onClick={() => void store.remove(session.session_id)}>
          remove
        </button>
      </div>
    );
  }
  render(<Harness />);
  await waitFor(() => expect(screen.getByTestId("count").textContent).toBe("1"));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "remove" })));
  expect(screen.getByTestId("count").textContent).toBe("1");
  expect(screen.getByTestId("error").textContent).toBe("conflict");
});

const archiveOnlyRow: SessionMetadata = {
  session_id: session.session_id,
  revision: 5,
  title: "",
  pinned: false,
  archived: true,
};

test("clearArchiveOnly deletes an archive-only label and leaves an earlier error showing", async () => {
  const api = {
    list: vi.fn(async () => [archiveOnlyRow]),
    patch: vi.fn(async () => {
      throw new Error("boom");
    }),
    remove: vi.fn(async () => true),
  };
  let cleared: boolean | undefined;
  function Harness() {
    const store = useSessionMetadata(api);
    return (
      <div>
        <span data-testid="count">{store.sessions.length}</span>
        <span data-testid="error">{store.error}</span>
        <button
          type="button"
          onClick={() => void store.update(archiveOnlyRow.session_id, { title: "x" })}
        >
          update
        </button>
        <button
          type="button"
          onClick={() => {
            void store.clearArchiveOnly(archiveOnlyRow.session_id).then((value) => {
              cleared = value;
            });
          }}
        >
          clear
        </button>
      </div>
    );
  }
  render(<Harness />);
  await waitFor(() => expect(screen.getByTestId("count").textContent).toBe("1"));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "update" })));
  expect(screen.getByTestId("error").textContent).toBe("update_failed");
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "clear" })));
  expect(api.remove).toHaveBeenCalledTimes(1);
  expect(api.remove).toHaveBeenCalledWith(archiveOnlyRow.session_id, 5);
  expect(screen.getByTestId("count").textContent).toBe("0");
  expect(cleared).toBe(true);
  // A failed `update` from before the clear is unrelated: clearArchiveOnly never touches `error`.
  expect(screen.getByTestId("error").textContent).toBe("update_failed");
});

test("clearArchiveOnly makes no request and keeps a row titled, pinned, unread or colored", async () => {
  const titled: SessionMetadata = {
    ...archiveOnlyRow,
    session_id: "623e4567-e89b-12d3-a456-426614174000",
    title: "Keep",
  };
  const pinned: SessionMetadata = {
    ...archiveOnlyRow,
    session_id: "723e4567-e89b-12d3-a456-426614174000",
    pinned: true,
  };
  const unread: SessionMetadata = {
    ...archiveOnlyRow,
    session_id: "923e4567-e89b-12d3-a456-426614174000",
    unread: true,
  };
  const colored: SessionMetadata = {
    ...archiveOnlyRow,
    session_id: "a23e4567-e89b-12d3-a456-426614174000",
    color: "blue",
  };
  const rows = [titled, pinned, unread, colored];
  const api = {
    list: vi.fn(async () => rows),
    patch: vi.fn(),
    remove: vi.fn(async () => true),
  };
  const results = new Map<string, boolean>();
  function Harness() {
    const store = useSessionMetadata(api);
    return (
      <div>
        <span data-testid="count">{store.sessions.length}</span>
        {rows.map((row) => (
          <button
            key={row.session_id}
            type="button"
            onClick={() => {
              void store.clearArchiveOnly(row.session_id).then((value) => {
                results.set(row.session_id, value);
              });
            }}
          >
            {`clear-${row.session_id}`}
          </button>
        ))}
      </div>
    );
  }
  render(<Harness />);
  await waitFor(() => expect(screen.getByTestId("count").textContent).toBe("4"));
  for (const row of rows) {
    await act(async () =>
      fireEvent.click(screen.getByRole("button", { name: `clear-${row.session_id}` })),
    );
  }
  expect(api.remove).not.toHaveBeenCalled();
  expect(screen.getByTestId("count").textContent).toBe("4");
  expect(rows.map((row) => results.get(row.session_id))).toEqual([false, false, false, false]);
});

test("clearArchiveOnly refuses a label with a request pending", async () => {
  let resolvePatch: (row: SessionMetadata) => void = () => {};
  const api = {
    list: vi.fn(async () => [archiveOnlyRow]),
    patch: vi.fn(() => new Promise<SessionMetadata>((resolve) => (resolvePatch = resolve))),
    remove: vi.fn(async () => true),
  };
  let cleared: boolean | undefined;
  function Harness() {
    const store = useSessionMetadata(api);
    return (
      <div>
        <span data-testid="count">{store.sessions.length}</span>
        <button
          type="button"
          onClick={() => void store.update(archiveOnlyRow.session_id, { archived: true })}
        >
          update
        </button>
        <button
          type="button"
          onClick={() => {
            void store.clearArchiveOnly(archiveOnlyRow.session_id).then((value) => {
              cleared = value;
            });
          }}
        >
          clear
        </button>
      </div>
    );
  }
  render(<Harness />);
  await waitFor(() => expect(screen.getByTestId("count").textContent).toBe("1"));
  // This patch stays unresolved through the clear below: the row itself never changes, so only
  // the pending check can refuse the clear.
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "update" })));
  expect(api.patch).toHaveBeenCalledTimes(1);
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "clear" })));
  expect(api.remove).not.toHaveBeenCalled();
  expect(cleared).toBe(false);
  expect(screen.getByTestId("count").textContent).toBe("1");
  // Settle the held patch so nothing resolves after the test ends.
  await act(async () => resolvePatch({ ...archiveOnlyRow, revision: 6 }));
});

test("clearArchiveOnly keeps the row and the error untouched when the delete answers conflict", async () => {
  const api = {
    list: vi.fn(async () => [archiveOnlyRow]),
    patch: vi.fn(),
    remove: vi.fn(async () => {
      throw new SessionMetadataError(409, "conflict", 2);
    }),
  };
  let cleared: boolean | undefined;
  function Harness() {
    const store = useSessionMetadata(api);
    return (
      <div>
        <span data-testid="count">{store.sessions.length}</span>
        <span data-testid="error">{store.error}</span>
        <button
          type="button"
          onClick={() => {
            void store.clearArchiveOnly(archiveOnlyRow.session_id).then((value) => {
              cleared = value;
            });
          }}
        >
          clear
        </button>
      </div>
    );
  }
  render(<Harness />);
  await waitFor(() => expect(screen.getByTestId("count").textContent).toBe("1"));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "clear" })));
  expect(screen.getByTestId("count").textContent).toBe("1");
  expect(screen.getByTestId("error").textContent).toBe("");
  expect(cleared).toBe(false);
});

test("clearArchiveOnly keeps the row and the error untouched when the delete throws offline", async () => {
  const api = {
    list: vi.fn(async () => [archiveOnlyRow]),
    patch: vi.fn(),
    remove: vi.fn(async () => {
      throw new Error("offline");
    }),
  };
  let cleared: boolean | undefined;
  function Harness() {
    const store = useSessionMetadata(api);
    return (
      <div>
        <span data-testid="count">{store.sessions.length}</span>
        <span data-testid="error">{store.error}</span>
        <button
          type="button"
          onClick={() => {
            void store.clearArchiveOnly(archiveOnlyRow.session_id).then((value) => {
              cleared = value;
            });
          }}
        >
          clear
        </button>
      </div>
    );
  }
  render(<Harness />);
  await waitFor(() => expect(screen.getByTestId("count").textContent).toBe("1"));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "clear" })));
  expect(screen.getByTestId("count").textContent).toBe("1");
  expect(screen.getByTestId("error").textContent).toBe("");
  expect(cleared).toBe(false);
});

test("clearArchiveOnly drops the row and answers true when the delete resolves false (404)", async () => {
  const api = {
    list: vi.fn(async () => [archiveOnlyRow]),
    patch: vi.fn(),
    remove: vi.fn(async () => false),
  };
  let cleared: boolean | undefined;
  function Harness() {
    const store = useSessionMetadata(api);
    return (
      <div>
        <span data-testid="count">{store.sessions.length}</span>
        <span data-testid="error">{store.error}</span>
        <button
          type="button"
          onClick={() => {
            void store.clearArchiveOnly(archiveOnlyRow.session_id).then((value) => {
              cleared = value;
            });
          }}
        >
          clear
        </button>
      </div>
    );
  }
  render(<Harness />);
  await waitFor(() => expect(screen.getByTestId("count").textContent).toBe("1"));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "clear" })));
  expect(screen.getByTestId("count").textContent).toBe("0");
  expect(cleared).toBe(true);
  expect(screen.getByTestId("error").textContent).toBe("");
});

test("a reload in flight from before the clear does not bring the row back", async () => {
  let resolveList: (rows: SessionMetadata[]) => void = () => {};
  const api = {
    list: vi.fn(() => new Promise<SessionMetadata[]>((resolve) => (resolveList = resolve))),
    patch: vi.fn(),
    remove: vi.fn(async () => true),
  };
  // The initial mount's reload resolves right away; the next one (triggered below) hangs, open
  // to `resolveList`, like the other in-flight-reload tests above.
  api.list.mockResolvedValueOnce([archiveOnlyRow]);
  function Harness() {
    const store = useSessionMetadata(api);
    return (
      <div>
        <span data-testid="count">{store.sessions.length}</span>
        <button type="button" onClick={() => void store.reload()}>
          reload
        </button>
        <button
          type="button"
          onClick={() => void store.clearArchiveOnly(archiveOnlyRow.session_id)}
        >
          clear
        </button>
      </div>
    );
  }
  render(<Harness />);
  await waitFor(() => expect(screen.getByTestId("count").textContent).toBe("1"));
  // This reload starts, and is still in flight, before the clear below runs.
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "reload" })));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "clear" })));
  expect(screen.getByTestId("count").textContent).toBe("0");
  // The stale pre-delete row from that in-flight reload lands only now; it must not come back.
  await act(async () => resolveList([archiveOnlyRow]));
  expect(screen.getByTestId("count").textContent).toBe("0");
});

const second = { ...session, session_id: "223e4567-e89b-12d3-a456-426614174000", title: "Second" };
const third = { ...session, session_id: "323e4567-e89b-12d3-a456-426614174000", title: "Third" };

test("a pinned tab has no close button", () => {
  const close = vi.fn();
  render(
    <SessionTabs
      sessions={[session, second]}
      selectedId={session.session_id}
      onSelect={vi.fn()}
      onClose={close}
      pinned={new Set([session.session_id])}
    />,
  );
  expect(screen.queryByRole("button", { name: "Close: Chat" })).toBeNull();
  expect(screen.getByRole("button", { name: "Close: Second" })).toBeTruthy();
  fireEvent.keyDown(screen.getAllByRole("tab")[0] as HTMLElement, { key: "Delete" });
  expect(close).not.toHaveBeenCalled();
});

test("the pin toggle reports the opposite state", () => {
  const pin = vi.fn();
  render(
    <SessionTabs
      sessions={[session, second]}
      selectedId={session.session_id}
      onSelect={vi.fn()}
      onClose={vi.fn()}
      pinned={new Set([session.session_id])}
      onPin={pin}
    />,
  );
  const chat = screen.getByRole("button", { name: "Pin: Chat" });
  expect(chat.getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(chat);
  expect(pin).toHaveBeenCalledWith(session.session_id, false);
  const other = screen.getByRole("button", { name: "Pin: Second" });
  expect(other.getAttribute("aria-pressed")).toBe("false");
  fireEvent.click(other);
  expect(pin).toHaveBeenCalledWith(second.session_id, true);
});

test("Ctrl+Shift+Arrow moves a tab past its neighbour", () => {
  const move = vi.fn();
  const select = vi.fn();
  render(
    <SessionTabs
      sessions={[session, second, third]}
      selectedId={second.session_id}
      onSelect={select}
      onClose={vi.fn()}
      onMove={move}
    />,
  );
  const tab = screen.getAllByRole("tab")[1] as HTMLElement;
  fireEvent.keyDown(tab, { key: "ArrowLeft", ctrlKey: true, shiftKey: true });
  expect(move).toHaveBeenLastCalledWith(second.session_id, session.session_id);
  fireEvent.keyDown(tab, { key: "ArrowRight", ctrlKey: true, shiftKey: true });
  expect(move).toHaveBeenLastCalledWith(second.session_id, null);
  expect(select).not.toHaveBeenCalled();
});

test("dropping a dragged tab on another calls onMove", () => {
  const move = vi.fn();
  render(
    <SessionTabs
      sessions={[session, second]}
      selectedId={session.session_id}
      onSelect={vi.fn()}
      onClose={vi.fn()}
      onMove={move}
    />,
  );
  const store = new Map<string, string>();
  const dataTransfer = {
    setData: (type: string, value: string) => store.set(type, value),
    getData: (type: string) => store.get(type) ?? "",
  };
  const tabs = document.querySelectorAll(".session-tab");
  fireEvent.dragStart(tabs[1] as Element, { dataTransfer });
  fireEvent.drop(tabs[0] as Element, { dataTransfer });
  expect(move).toHaveBeenCalledTimes(1);
  expect(move).toHaveBeenCalledWith(second.session_id, session.session_id);
});

function dragTab(from: number, to: number): void {
  const store = new Map<string, string>();
  const dataTransfer = {
    setData: (type: string, value: string) => store.set(type, value),
    getData: (type: string) => store.get(type) ?? "",
  };
  const tabs = document.querySelectorAll(".session-tab");
  fireEvent.dragStart(tabs[from] as Element, { dataTransfer });
  fireEvent.drop(tabs[to] as Element, { dataTransfer });
}

function renderThreeTabs(move: (id: string, beforeId: string | null) => void, pinned?: string[]) {
  render(
    <SessionTabs
      sessions={[session, second, third]}
      selectedId={session.session_id}
      onSelect={vi.fn()}
      onClose={vi.fn()}
      onMove={move}
      {...(pinned ? { pinned: new Set(pinned) } : {})}
    />,
  );
}

test("dropping a tab on its right-hand neighbour moves it after that tab", () => {
  const move = vi.fn();
  renderThreeTabs(move);

  dragTab(0, 1);
  expect(move).toHaveBeenLastCalledWith(session.session_id, third.session_id);
  dragTab(0, 2);
  expect(move).toHaveBeenLastCalledWith(session.session_id, null);
  dragTab(2, 0);
  expect(move).toHaveBeenLastCalledWith(third.session_id, session.session_id);
});

test("dropping a tab on a tab of the other group asks for no move past it", () => {
  const move = vi.fn();
  renderThreeTabs(move, [session.session_id]);

  dragTab(0, 1);

  // The target itself is what App's reorder refuses across groups, so the strip stays as it was.
  expect(move).toHaveBeenCalledWith(session.session_id, second.session_id);
});

test("dropping on the last tab of a group moves to the end of that group", () => {
  const move = vi.fn();
  renderThreeTabs(move, [session.session_id, second.session_id]);

  dragTab(0, 1);

  expect(move).toHaveBeenCalledWith(session.session_id, null);
});

test("only Escape stops at the pin, tab menu and close buttons", () => {
  render(
    <SessionTabs
      sessions={[session]}
      selectedId={session.session_id}
      onSelect={vi.fn()}
      onClose={vi.fn()}
      onPin={vi.fn()}
      menuFor={() => [{ label: "Export", onSelect: vi.fn() }]}
    />,
  );
  const seen: string[] = [];
  const spy = (event: KeyboardEvent) => seen.push(event.key);
  window.addEventListener("keydown", spy);
  try {
    for (const name of ["Pin: Chat", "Tab actions: Chat", "Close: Chat"]) {
      const button = screen.getByRole("button", { name });
      fireEvent.keyDown(button, { key: "Escape" });
      fireEvent.keyDown(button, { key: "n", altKey: true });
    }
  } finally {
    window.removeEventListener("keydown", spy);
  }
  expect(seen).toEqual(["n", "n", "n"]);
});

test("no tab menu renders without menuFor or with an empty list, and no beside button exists", () => {
  const props = {
    sessions: [session],
    selectedId: session.session_id,
    onSelect: vi.fn(),
    onClose: vi.fn(),
  };
  const { rerender } = render(<SessionTabs {...props} />);
  expect(screen.queryByRole("button", { name: "Tab actions: Chat" })).toBeNull();
  expect(screen.queryByText("◧")).toBeNull();
  rerender(<SessionTabs {...props} menuFor={() => []} />);
  expect(screen.queryByRole("button", { name: "Tab actions: Chat" })).toBeNull();
});

test("the tab menu renders its items, skips a disabled one, and calls onSelect for an enabled one", () => {
  const exportItem = vi.fn();
  const deleteItem = vi.fn();
  render(
    <SessionTabs
      sessions={[session]}
      selectedId={session.session_id}
      onSelect={vi.fn()}
      onClose={vi.fn()}
      menuFor={() => [
        { label: "Export", onSelect: exportItem },
        { label: "Delete", disabled: true, onSelect: deleteItem },
      ]}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Tab actions: Chat" }));
  expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
    "Export",
    "Delete",
  ]);
  fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
  expect(deleteItem).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("menuitem", { name: "Export" }));
  expect(exportItem).toHaveBeenCalledTimes(1);
});

test("a foreign id dropped on a tab calls onMove with that id", () => {
  const move = vi.fn();
  render(
    <SessionTabs
      sessions={[session, second]}
      selectedId={session.session_id}
      onSelect={vi.fn()}
      onClose={vi.fn()}
      onMove={move}
    />,
  );
  const dataTransfer = { setData: () => {}, getData: () => "foreign-id" };
  const tabs = document.querySelectorAll(".session-tab");
  fireEvent.drop(tabs[0] as Element, { dataTransfer });
  expect(move).toHaveBeenCalledWith("foreign-id", session.session_id);
});

test("activeId marks a pane's active tab only while another tab is the selected one", () => {
  const props = {
    sessions: [session, second],
    onSelect: vi.fn(),
    onClose: vi.fn(),
  };
  const { rerender } = render(
    <SessionTabs {...props} selectedId={null} activeId={second.session_id} />,
  );
  const [first, other] = [...document.querySelectorAll<HTMLElement>(".session-tab")];
  expect(other?.dataset.active).toBe("true");
  expect(first?.hasAttribute("data-active")).toBe(false);
  // Visual only: the active tab is not the selected one.
  expect(screen.getByRole("tab", { name: /Second/ }).getAttribute("aria-selected")).toBe("false");

  rerender(<SessionTabs {...props} selectedId={second.session_id} activeId={second.session_id} />);
  expect(document.querySelectorAll("[data-active]")).toHaveLength(0);
  expect(screen.getByRole("tab", { name: /Second/ }).getAttribute("aria-selected")).toBe("true");
});

test("a disabled function runs only while the tab menu is open", () => {
  const disabled = vi.fn(() => true);
  render(
    <SessionTabs
      sessions={[session]}
      selectedId={session.session_id}
      onSelect={vi.fn()}
      onClose={vi.fn()}
      menuFor={() => [{ label: "Split right", disabled, onSelect: vi.fn() }]}
    />,
  );
  expect(disabled).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "Tab actions: Chat" }));

  expect(disabled).toHaveBeenCalled();
  expect(screen.getByRole("menuitem", { name: "Split right" }).getAttribute("aria-disabled")).toBe(
    "true",
  );
});

test("the tablist name comes from paneLabel, else keeps its language default", () => {
  const props = {
    sessions: [session],
    selectedId: session.session_id,
    onSelect: vi.fn(),
    onClose: vi.fn(),
  };
  const { rerender } = render(<SessionTabs {...props} />);
  expect(screen.getByRole("tablist", { name: "Sessions" })).toBeTruthy();
  rerender(<SessionTabs {...props} paneLabel="Left" />);
  expect(screen.getByRole("tablist", { name: "Left tabs" })).toBeTruthy();
});

const fileRow = {
  ...session,
  session_id: "423e4567-e89b-12d3-a456-426614174000",
  file: { name: "README.md", dirty: false, ref: "C:/repo/README.md" },
};

test("a file tab's extension sits outside the truncated stem and carries the path as a tooltip", () => {
  render(
    <SessionTabs
      sessions={[fileRow]}
      selectedId={fileRow.session_id}
      onSelect={vi.fn()}
      onClose={vi.fn()}
    />,
  );
  expect(document.querySelector(".session-tab-stem")?.textContent).toBe("README");
  expect(document.querySelector(".session-tab-ext")?.textContent).toBe(".md");
  expect(document.querySelector(".session-tab-label")?.getAttribute("title")).toBe(
    "C:/repo/README.md",
  );
});

test("a dotfile's leading dot is not read as an extension", () => {
  const dotfile = { ...fileRow, file: { ...fileRow.file, name: ".env" } };
  render(
    <SessionTabs
      sessions={[dotfile]}
      selectedId={dotfile.session_id}
      onSelect={vi.fn()}
      onClose={vi.fn()}
    />,
  );
  expect(document.querySelector(".session-tab-stem")?.textContent).toBe(".env");
  expect(document.querySelector(".session-tab-ext")?.textContent).toBe("");
});

test("a file tab's name keeps its real case", () => {
  const shouting = { ...fileRow, file: { ...fileRow.file, name: "README.MD" } };
  render(
    <SessionTabs
      sessions={[shouting]}
      selectedId={shouting.session_id}
      onSelect={vi.fn()}
      onClose={vi.fn()}
    />,
  );
  expect(document.querySelector(".session-tab-stem")?.textContent).toBe("README");
  expect(document.querySelector(".session-tab-ext")?.textContent).toBe(".MD");
});

test("a dirty file tab shows the unsaved dot; a clean one shows none", () => {
  const dirty = { ...fileRow, file: { ...fileRow.file, dirty: true } };
  const { rerender } = render(
    <SessionTabs
      sessions={[dirty]}
      selectedId={dirty.session_id}
      onSelect={vi.fn()}
      onClose={vi.fn()}
    />,
  );
  expect(screen.getByLabelText("Unsaved changes").textContent).toBe("●");

  rerender(
    <SessionTabs
      sessions={[fileRow]}
      selectedId={fileRow.session_id}
      onSelect={vi.fn()}
      onClose={vi.fn()}
    />,
  );
  expect(screen.queryByLabelText("Unsaved changes")).toBeNull();
});

const sessionsCss = readCss(import.meta.url, "Sessions.css");

test("the file extension never shrinks and keeps its case", () => {
  expect(ruleIn(sessionsCss, ".session-tab-ext")).toMatch(/flex:\s*none/);
  expect(ruleIn(sessionsCss, ".session-tab-ext")).toMatch(/text-transform:\s*none/);
});

test("the file stem ellipsizes and keeps its case", () => {
  expect(ruleIn(sessionsCss, ".session-tab-stem")).toMatch(/text-overflow:\s*ellipsis/);
  expect(ruleIn(sessionsCss, ".session-tab-stem")).toMatch(/text-transform:\s*none/);
});

test("a file tab sizes to its name with a max width", () => {
  expect(ruleIn(sessionsCss, ".session-tab:has(.session-tab-stem)")).toMatch(
    /flex:\s*0\s+1\s+auto/,
  );
  expect(ruleIn(sessionsCss, ".session-tab:has(.session-tab-stem)")).toMatch(/max-width:\s*260px/);
});

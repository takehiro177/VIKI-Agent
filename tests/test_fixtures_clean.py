"""The two guards with no other home.

Nothing the repo commits may carry personal data (paths, names, e-mail, employer), and the one
script that drives the real claude.exe, `scripts/capture.py`, may not approve a tool by name
alone, leave a session running after a scenario breaks, or run anywhere but its seeded scratch
folder.
"""

from __future__ import annotations

import asyncio
import codecs
import functools
import hashlib
import inspect
import io
import json
import re
import sys
import zipfile
import zlib
from collections import Counter
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from viki_agent import catalog, config, winproc
from viki_agent.drivers.base import StartError
from viki_agent.drivers.claude.control import Launch
from viki_agent.drivers.claude.transport import EXIT

from replay import (
    SID,
    Entry,
    FakeSpawner,
    init_frame,
    out,
    permission_response_in,
    result_frame,
    text_delta,
    user_in,
)

TESTS = Path(__file__).parent
REPO = TESTS.parent
sys.path.insert(0, str(REPO / "scripts"))

import capture  # noqa: E402
import scrub_fixture  # noqa: E402
from release_files import (  # noqa: E402
    CERTIFICATE_LIMIT,
    certificate_bundle_problem,
    private_path_problem,
)
from scrub_fixture import (  # noqa: E402
    DO_NOT_COMMIT,
    OWNER,
    cli,
    kept_entries,
    problems,
    scrub_line,
    scrub_text,
)
from scrub_fixture import main as scrub_main  # noqa: E402

Allow = capture.PermissionResultAllow
Deny = capture.PermissionResultDeny

# The guard scans this file too. If the strings it forgives elsewhere were spelled out here, it
# would have to forgive itself everything, and a leak pasted into the guard would go unseen. So
# they are spelled in halves, and the test data below is built from them.
USERS = "c:" + "\\users"  # the Windows home prefix as it appears in raw text
USERS_JSON = "c:" + "\\\\users"  # and as JSON escapes it
USERS_JSON2 = "c:" + "\\" * 4 + "users"  # and as a second JSON escaping doubles it again
USERS_SLASH = "c:" + "/users"
SLUG = "C--" + "Users-"  # Claude Code's project-folder form of the same prefix
# Stand-ins for the private words a contributor keeps in scrub_fixture.PRIVATE_FILE (the
# `private_words` fixture): a family name, and an employer's three letters, short enough to turn
# up by chance inside binary media.
EMPLOYER = "QZX"
SURNAME = "Smithee"
CREDENTIALS = "." + "credentials"
API_KEY = "sk-" + "ant-"
FOREIGN_MAIL = "someone@" + "corp.test"

# Committed files that carry a banned string on purpose, and exactly which strings each may
# carry. Anything else in these files, and anything at all in any other committed file, fails.
# Per-string rather than per-file, so an exempt file still fails on every banned string it is not
# exempt from, though an allowed prefix admits whatever follows it in that file:
# errors.accept.test.tsx is forgiven a fake path, never a credential.
ALLOWED: dict[str, frozenset[str]] = {
    # Public upstream copyright emails, retained verbatim in the bundled legal notices.
    "frontend/public/THIRD_PARTY_NOTICES.txt": frozenset(
        {
            "luke.edwards05@" + "gmail.com",
            "marijn@" + "haverbeke.berlin",
            "mark@" + "remarkablemark.org",
            "rulfzid@" + "gmail.com",
            "sindresorhus@" + "gmail.com",
            "tats.u@" + "live.jp",
            "thekashey@" + "gmail.com",
            "tituswormer@" + "gmail.com",
            "tj@" + "vision-media.ca",
            "victor@" + "draft.li",
        }
    ),
    # Settings alias guards and synthetic tests name the protected filename, not its contents.
    "src/viki_agent/config.py": frozenset({CREDENTIALS}),
    # Workspace inspection lists this private filename as a refused code-folder name.
    "src/viki_agent/workspaces.py": frozenset({CREDENTIALS}),
    "tests/test_settings_aliases.py": frozenset({CREDENTIALS}),
    # Redaction and export tests plant the very strings the redactor must remove.
    "tests/app/test_diagnostics.py": frozenset(
        {CREDENTIALS, USERS, USERS_JSON, USERS_SLASH, API_KEY}
    ),
    "tests/app/test_exports.py": frozenset({USERS_JSON, API_KEY}),
    "tests/app/test_workspaces.py": frozenset({"test@example" + ".invalid"}),
    # The data-locations catalog names the provider location kinds the API reports (`home`,
    # `credentials`) as keys; the values never carry a path or a file name.
    "frontend/src/i18n/data.ts": frozenset({CREDENTIALS}),
    # The scrubber is where the banned list lives.
    "scripts/scrub_fixture.py": frozenset(
        {CREDENTIALS, USERS_SLASH, USERS_JSON, USERS_JSON2, API_KEY, SLUG}
    ),
    # git's own commit identity for the e2e fixture repo, deliberately a reserved domain.
    "scripts/e2e_server.py": frozenset({"e2e@example" + ".invalid"}),
    # The installers' placeholder support address (ADR-055), deliberately a reserved
    # domain; a test blocks an update feed while it remains.
    "desktop/package.json": frozenset({"support@viki-agent" + ".invalid"}),
    "desktop/test/build.test.mjs": frozenset({"support@viki-agent" + ".invalid"}),
    # npm copies a dependency's own deprecation notice into the lockfile, and an old glob's names
    # its author's address; generated by npm, not anyone's data here.
    "desktop/package-lock.json": frozenset({"i@" + "izs.me"}),
    # third-party authors' addresses in CPython's verbatim licence acknowledgements
    "desktop/build/notices/cpython-3.14.7-incorporated.txt": frozenset(
        {
            "timo@alum" + ".mit.edu",
            "zooko@" + "zooko.com",
            "marek@" + "popcount.org",
            "jloup@" + "gzip.org",
            "madler@" + "alumni.caltech.edu",
            "jeff@" + "FreeBSD.org",
        }
    ),
    "desktop/build/notices/cpython-3.14.4-incorporated.txt": frozenset(
        {
            "timo@alum" + ".mit.edu",
            "zooko@" + "zooko.com",
            "marek@" + "popcount.org",
            "jloup@" + "gzip.org",
            "madler@" + "alumni.caltech.edu",
            "jeff@" + "FreeBSD.org",
        }
    ),
    # bzip2's own licence text names its author's address
    "desktop/build/notices/bzip2-1.0.8.txt": frozenset({"jseward@" + "bzip.org"}),
    # The chat UI's label for the user's own turns is the `userLabel` prop, so Conversation.tsx
    # carries no name at all. The frozen acceptance test passes a sample first name in as that
    # prop and asserts on it; a contributor whose account has that name must not fail here.
    "frontend/src/accept/m2b/conversation.accept.test.tsx": frozenset({OWNER.lower()}),
    # The redactor is where its own banned list lives, exactly as the scrubber's is above.
    "src/viki_agent/app/diagnostics.py": frozenset({USERS, USERS_JSON, USERS_SLASH, API_KEY}),
    # The vault reader denies the provider credential filename; no credential content is present.
    "src/viki_agent/vaults/vault.py": frozenset({CREDENTIALS}),
    # Artifact display paths deny the credential filename, without reading its content.
    "src/viki_agent/conversations/artifacts.py": frozenset({CREDENTIALS}),
    # The client mirror of the reader's private set names the credential filename, so the
    # browser hides what the server refuses; no credential content is present.
    "frontend/src/state/vaultApi.ts": frozenset({CREDENTIALS}),
    # Frozen acceptance tests. Synthetic strings, not captured data: a made-up traceback under a
    # sample user's home, and a made-up vault folder under another user's home.
    "frontend/src/accept/m2b3/errors.accept.test.tsx": frozenset({USERS_JSON, OWNER.lower()}),
    "tests/acceptance/test_catalog_accept.py": frozenset({USERS_SLASH, SLUG}),
    # A made-up Windows path as a search query, for JSON-escaped matches (tests/test_catalog.py).
    "tests/test_catalog.py": frozenset({USERS_JSON}),
}
# Areas the walk must reach. A guard that scans nothing passes for the wrong reason, so a
# renamed fixture folder or a broken enumeration fails here instead of certifying zero bytes.
MUST_REACH = ("tests/fixtures/", "tests/contracts/", "frontend/src/", "scripts/", "docs/")
# These identifiers are public. Exempt only their exact URLs, and only in the pages that link
# them, rather than forgiving the account name anywhere in those files. Split them so this guard
# scans itself.
PUBLIC_REPO_URL = "https://github.com/" + "take" + "hiro177/VIKI-Agent"
SPONSOR_URL = "https://github.com/sponsors/" + "take" + "hiro177"
PROFILE_URL = "https://github.com/" + "take" + "hiro177"
# The GitHub Pages site serves docs/: the README may link its front page (the visual guide) or its
# showcase, each with an optional anchor, and nothing else on that host.
PAGES_URL = "https://" + "take" + "hiro177.github.io/VIKI-Agent/"
PAGES_TAIL = r"(?:showcase/)?(?:#[a-z0-9-]+)?"
REPO_URL_PAGES = frozenset({"README.md", "docs/index.html", "docs/showcase/assets/showcase.js"})
PROFILE_URL_PAGES = frozenset({"README.md"})
SPONSOR_URL_PAGES = frozenset({"README.md", "docs/index.html", "docs/showcase/assets/showcase.js"})
PAGES_URL_PAGES = frozenset({"README.md"})
# FOSSA scans the public repository: the README shows its license and security badges, each
# linking to the project's FOSSA page for the same issue type, and no other FOSSA URL.
FOSSA_PROJECT = "git%2Bgithub.com%2F" + "take" + "hiro177%2FVIKI-Agent"
FOSSA_BADGE_URL = "https://app.fossa.com/api/projects/" + FOSSA_PROJECT + ".svg"
FOSSA_BADGE_TAIL = r"\?type=shield&issueType=(?:license|security)"
FOSSA_PAGE_URL = "https://app.fossa.com/projects/" + FOSSA_PROJECT
FOSSA_PAGE_TAIL = r"\?ref=badge_shield&issueType=(?:license|security)"
FOSSA_URL_PAGES = frozenset({"README.md"})
# GitHub needs the owner's handle in CODEOWNERS and FUNDING.yml. Exempt only each file's one rule
# line, as a whole line of that file. Split them so this guard scans itself.
CODEOWNERS = ".github/CODEOWNERS"
CODEOWNERS_RULE = "* @" + "take" + "hiro177"
FUNDING = ".github/FUNDING.yml"
FUNDING_RULE = "github: " + "take" + "hiro177"
PUBLIC_DOCUMENTS = (
    REPO_URL_PAGES
    | PROFILE_URL_PAGES
    | SPONSOR_URL_PAGES
    | PAGES_URL_PAGES
    | FOSSA_URL_PAGES
    | {CODEOWNERS, FUNDING}
)


def _without_exact_url(content: str, url: str, tail: str = "") -> str:
    """Drop `url`, plus what the `tail` pattern allows after it, where it stands whole: not where
    a longer URL or word continues it."""
    return re.sub(re.escape(url) + tail + r"(?=$|[\s)>\"'`<])", "", content)


def _public_document_text(name: str, content: str) -> str:
    if name in SPONSOR_URL_PAGES:
        content = _without_exact_url(content, SPONSOR_URL)
    if name in REPO_URL_PAGES:
        content = _without_exact_url(content, PUBLIC_REPO_URL)
    if name in PROFILE_URL_PAGES:
        content = _without_exact_url(content, PROFILE_URL)
    if name in PAGES_URL_PAGES:
        content = _without_exact_url(content, PAGES_URL, PAGES_TAIL)
    if name in FOSSA_URL_PAGES:
        content = _without_exact_url(content, FOSSA_BADGE_URL, FOSSA_BADGE_TAIL)
        content = _without_exact_url(content, FOSSA_PAGE_URL, FOSSA_PAGE_TAIL)
    if name == CODEOWNERS:
        return re.sub("(?m)^" + re.escape(CODEOWNERS_RULE) + "$", "", content)
    if name == FUNDING:
        return re.sub("(?m)^" + re.escape(FUNDING_RULE) + "$", "", content)
    return content


def _owner_data_problems(name: str, content: str) -> set[str]:
    return set(problems(_public_document_text(name, content))) - ALLOWED.get(name, frozenset())


type MediaSpan = tuple[str, int, int, str]
type MediaAdmission = tuple[str, tuple[MediaSpan, ...]]

# Each reviewed collision binds one regex category and byte span, not a string-wide exemption.
# The complete file hash also pins the codec/container evidence reviewed for that span.
_DEMO_ADMISSION: MediaAdmission = (
    "03125e495b86346393a18eb80523e65c3297cc96aedd1047516ba5563a2a85bc",
    (
        (
            "non_allowlisted_email",
            25680679,
            25680686,
            "d9a5479b003e2c651cde18a4eea4071ccb3c818ad63ef6a69cc618362ca61a93",
        ),
        (
            "non_allowlisted_email",
            31971969,
            31971975,
            "6046de71cfa55b6924852d583d1ec1c9af54cb957203dfbab3770a8e11f562eb",
        ),
        (
            "non_allowlisted_email",
            36994744,
            36994752,
            "2fc3d96897a5f3beb009dd96d0304a2e8234cc2c1b5f7addfc2369182b96a07f",
        ),
    ),
)


def _with_private_collisions(records: dict[str, MediaAdmission]) -> dict[str, MediaAdmission]:
    """Add a contributor's reviewed chance matches of their own private words
    (scrub_fixture.PRIVATE_FILE) to the records for the same file and bytes."""
    merged = dict(records)
    for name, private in scrub_fixture.PRIVATE.get("media_collisions", {}).items():
        digest, spans = merged.get(name, (private["sha256"], ()))
        assert digest == private["sha256"], f"{name}: the private media record no longer matches"
        extra = tuple(("private_word_casefold", a, b, h) for a, b, h in private["spans"])
        merged[name] = (digest, spans + extra)
    return merged


MEDIA_COLLISIONS: dict[str, MediaAdmission] = _with_private_collisions(
    {
        "docs/assets/videos/chat-launcher-demo.mp4": _DEMO_ADMISSION,
        "docs/assets/videos/viki-agent-cinematic-launch-paced.mp4": (
            "3f30b9127435225d8c941fb4537832d60af759c02f8e951ab58b1b019be9ae56",
            (
                (
                    "non_allowlisted_email",
                    6398651,
                    6398660,
                    "dc55da91909a17f9a3fa67f4eadc1f21612b666aa48b9d24824671ffcaf69db4",
                ),
            ),
        ),
        "docs/assets/videos/viki-agent-cinematic-launch.png": (
            "64a0731843c66515e96f63bf423d7d1b1faab83286410a27108379cd4cd63ad5",
            (),
        ),
    }
)

MAX_ARCHIVE_MEMBERS = 64
MAX_ARCHIVE_BYTES = 100_000_000


def _decoded_offsets(data: bytes, boundaries: set[int]) -> dict[int, int]:
    """Map only reviewed UTF-8 match boundaries, with bounded decoder working memory."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    offsets = {}
    previous = characters = 0
    view = memoryview(data)
    for boundary in sorted(boundaries):
        for start in range(previous, boundary, 65536):
            characters += len(decoder.decode(view[start : min(start + 65536, boundary)]))
        # The caller checks each complete match's strict UTF-8 decoding before this mapping.
        # Its boundaries cannot split a valid code point; flush invalid prefix bytes here.
        characters += len(decoder.decode(b"", final=True))
        decoder.reset()
        offsets[boundary] = characters
        previous = boundary
    return offsets


def _media_problems(data: bytes, admission: MediaAdmission | None) -> Counter[str]:
    text = data.decode("utf-8", errors="replace")
    found = Counter(problems(text))
    if wide := _wide_text(data):
        found.update(problems(wide))
    if admission is None:
        return found
    expected_hash, spans = admission
    if hashlib.sha256(data).hexdigest() != expected_hash:
        found["reviewed media bytes changed"] += 1
        return found
    for _, start, end, expected in spans:
        assert 0 <= start < end <= len(data), "invalid reviewed media span"
        raw = data[start:end]
        assert hashlib.sha256(raw).hexdigest() == expected, "reviewed span bytes changed"
        raw.decode("utf-8")
    offsets = _decoded_offsets(data, {point for _, a, b, _ in spans for point in (a, b)})
    patterns = {"non_allowlisted_email": scrub_fixture.EMAIL}
    if private := scrub_fixture.private_word_pattern():
        patterns["private_word_casefold"] = private
    wanted: dict[str, set[tuple[int, int]]] = {}
    for category, start, end, _ in spans:
        assert category in patterns, "unreviewed media category"
        position = (offsets[start], offsets[end])
        assert position not in wanted.setdefault(category, set()), "duplicate reviewed media span"
        wanted[category].add(position)
    for category, positions in wanted.items():
        observed = {}
        for match in patterns[category].finditer(text):
            if match.span() in positions:
                if category == "non_allowlisted_email":
                    assert scrub_fixture._is_foreign(match[0]), "reviewed email is not a finding"
                observed[match.span()] = match[0]
        assert observed.keys() == positions, "reviewed span is not the original regex match"
        for matched in observed.values():
            assert found[matched] > 0, "reviewed span is not a finding"
            found[matched] -= 1
    return +found


def _wide_text(data: bytes) -> str:
    # UTF-32 LE starts with the UTF-16 LE BOM, so inspect the longer marker first.
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return data.decode("utf-32", errors="replace")
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", errors="replace")
    return ""


def _file_owner_problems(name: str, data: bytes) -> set[str]:
    if name in PUBLIC_DOCUMENTS:
        found = set(problems(_public_document_text(name, data.decode("utf-8", errors="replace"))))
        found.update(problems(_public_document_text(name, _wide_text(data))))
        return found
    admission = MEDIA_COLLISIONS.get(name)
    found = _media_problems(data, admission)
    if Path(name).suffix.lower() != ".zip" and not zipfile.is_zipfile(io.BytesIO(data)):
        return set(found)
    reviewed_archive = admission is not None and hashlib.sha256(data).hexdigest() == admission[0]
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if (
                len(infos) > MAX_ARCHIVE_MEMBERS
                or sum(i.file_size for i in infos) > MAX_ARCHIVE_BYTES
            ):
                found["archive scan limit exceeded"] += 1
                return set(found)
            found.update(problems(archive.comment.decode("utf-8", errors="replace")))
            for info in infos:
                found.update(problems(info.filename))
                found.update(problems(info.comment.decode("utf-8", errors="replace")))
                found.update(problems(info.extra.decode("utf-8", errors="replace")))
                if problem := private_path_problem(info.filename):
                    found[problem] += 1
                    continue
                if info.flag_bits & 1:
                    found["encrypted archive member"] += 1
                    continue
                if info.filename == "certifi/cacert.pem" and info.file_size > CERTIFICATE_LIMIT:
                    found["oversized public certificate bundle"] += 1
                    continue
                member = archive.read(info)  # CRC checked; no extraction or execution.
                key = f"{name}::{info.filename}"
                member_admission = MEDIA_COLLISIONS.get(key) if reviewed_archive else None
                found.update(_media_problems(member, member_admission))
                if info.filename == "certifi/cacert.pem" and (
                    problem := certificate_bundle_problem(member)
                ):
                    found[problem] += 1
                if zipfile.is_zipfile(io.BytesIO(member)):
                    found["nested archive"] += 1
    except zipfile.BadZipFile, RuntimeError, NotImplementedError, ValueError, OSError, zlib.error:
        found["unreadable archive"] += 1
    return set(found)


def _committed_files() -> list[str]:
    """Every path `git add -A && git commit` would capture, tracked or not yet — the set that
    can leave this machine on the next commit, not just the one after."""
    tracked = winproc.git(REPO, "ls-files", "-z").split("\0")
    untracked = winproc.git(REPO, "ls-files", "-z", "--others", "--exclude-standard").split("\0")
    return sorted({name for name in tracked + untracked if name})


def test_committed_files_carry_no_owner_data() -> None:
    offenders: dict[str, list[str]] = {}
    scanned: list[str] = []
    for name in _committed_files():
        path = REPO / name
        if problem := private_path_problem(name):
            offenders[name] = [problem]
            continue
        if path.is_symlink() or path.is_junction():
            offenders[name] = ["link in repository files"]
            continue
        if not path.is_file():
            continue  # staged deletion: no bytes left to leak
        scanned.append(name)
        # Every payload reaches the privacy patterns; admissions remove exact reviewed spans.
        if name == "certifi/cacert.pem":
            with path.open("rb") as stream:
                data = stream.read(CERTIFICATE_LIMIT + 1)
            found = _file_owner_problems(name, data)
            if problem := certificate_bundle_problem(data):
                found.add(problem)
        else:
            found = _file_owner_problems(name, path.read_bytes())
        if extra := sorted(found - ALLOWED.get(name, frozenset())):
            offenders[name] = extra
    for area in MUST_REACH:
        assert any(name.startswith(area) for name in scanned), f"the scan never reached {area}"
    assert offenders == {}


@pytest.mark.parametrize("name", ["README.md", "AGENTS.md"])
def test_public_entry_documents_reject_owner_data(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fix the banned account for this regression; CI can run under a generic account.
    monkeypatch.setattr(scrub_fixture, "OWNER", "take" + "hiro")
    assert _owner_data_problems(name, USERS + "\\private")
    assert _owner_data_problems(name, "private " + scrub_fixture.OWNER)
    assert _owner_data_problems(name, PUBLIC_REPO_URL + "/private/" + scrub_fixture.OWNER)
    assert _owner_data_problems(name, PUBLIC_REPO_URL + "-fork")
    assert _owner_data_problems(name, PAGES_URL + "private/" + scrub_fixture.OWNER)
    assert _owner_data_problems(name, PAGES_URL + "manual/docs/security.html")
    assert _owner_data_problems(name, FOSSA_PAGE_URL + "/refs/branch/main")
    assert _owner_data_problems(name, f"[![FOSSA]({FOSSA_BADGE_URL}?type=large)]")
    site = [PAGES_URL, PAGES_URL + "#core-team", PAGES_URL + "showcase/"]
    fossa = [
        f"[![FOSSA {kind} scan]({FOSSA_BADGE_URL}?type=shield&issueType={kind})]"
        f"({FOSSA_PAGE_URL}?ref=badge_shield&issueType={kind})"
        for kind in ("license", "security")
    ]
    if name == "README.md":
        assert _owner_data_problems(name, PUBLIC_REPO_URL) == set()
        assert all(_owner_data_problems(name, f"[site]({url})") == set() for url in site)
        assert all(_owner_data_problems(name, badge) == set() for badge in fossa)
    else:
        assert _owner_data_problems(name, PUBLIC_REPO_URL)
        assert all(_owner_data_problems(name, f"[site]({url})") for url in site)
        assert all(_owner_data_problems(name, badge) for badge in fossa)


@pytest.mark.parametrize("name", ["README.md", "AGENTS.md"])
def test_byte_scan_preserves_the_exact_public_document_rule(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scrub_fixture, "OWNER", "take" + "hiro")
    found = _file_owner_problems(name, PUBLIC_REPO_URL.encode())
    assert not found if name == "README.md" else found
    for private in (USERS + "\\someone", CREDENTIALS, API_KEY, FOREIGN_MAIL):
        assert _file_owner_problems(name, (PUBLIC_REPO_URL + "\n" + private).encode())
    assert _file_owner_problems(name, (PUBLIC_REPO_URL + "-fork").encode())


def test_codeowners_exempts_only_its_one_rule_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scrub_fixture, "OWNER", "take" + "hiro")
    comment = "# The Project Owner reviews every change.\n"
    rule = CODEOWNERS_RULE + "\n"
    assert _owner_data_problems(CODEOWNERS, comment + rule) == set()
    assert _file_owner_problems(CODEOWNERS, (comment + rule).encode()) == set()
    for other in (
        "# " + scrub_fixture.OWNER + "\n" + rule,
        rule + "src/ @" + scrub_fixture.OWNER + "\n",
        CODEOWNERS_RULE + " extra\n",
        CODEOWNERS_RULE + "x\n",
        " " + CODEOWNERS_RULE + "\n",
    ):
        assert _owner_data_problems(CODEOWNERS, other)
        assert _file_owner_problems(CODEOWNERS, other.encode())
    for name in ("README.md", "AGENTS.md", ".github/CODEOWNERS.md", "CODEOWNERS"):
        assert _owner_data_problems(name, rule)
        assert _file_owner_problems(name, rule.encode())


def test_funding_exempts_only_its_one_rule_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scrub_fixture, "OWNER", "take" + "hiro")
    rule = FUNDING_RULE + "\n"
    assert _owner_data_problems(FUNDING, rule) == set()
    assert _file_owner_problems(FUNDING, rule.encode()) == set()
    for other in (
        "# " + scrub_fixture.OWNER + "\n" + rule,
        FUNDING_RULE + "-fork\n",
        " " + FUNDING_RULE + "\n",
        "custom: " + SPONSOR_URL + "\n",
    ):
        assert _owner_data_problems(FUNDING, other)
        assert _file_owner_problems(FUNDING, other.encode())
    for name in ("README.md", "AGENTS.md", ".github/FUNDING.yaml"):
        assert _owner_data_problems(name, rule)


@pytest.mark.parametrize(
    ("name", "url"),
    [
        ("README.md", SPONSOR_URL),
        ("README.md", PROFILE_URL),
        ("docs/index.html", SPONSOR_URL),
        ("docs/index.html", PUBLIC_REPO_URL),
        ("docs/showcase/assets/showcase.js", SPONSOR_URL),
        ("docs/showcase/assets/showcase.js", PUBLIC_REPO_URL),
    ],
)
def test_public_pages_exempt_only_their_exact_links(
    name: str, url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scrub_fixture, "OWNER", "take" + "hiro")
    for linked in (url, f"({url})", f'href="{url}"', f"sponsor:'{url}'"):
        assert _owner_data_problems(name, linked) == set()
        assert _file_owner_problems(name, linked.encode()) == set()
    for other in (url + "/private", url + "?tier=1", url + "-fork", url + "\n" + API_KEY):
        assert _owner_data_problems(name, other)
        assert _file_owner_problems(name, other.encode())
    assert _owner_data_problems("AGENTS.md", url)
    assert _owner_data_problems("docs/README.md", url)


@pytest.mark.parametrize("name", ["synthetic.txt", "README.md"])
@pytest.mark.parametrize(
    ("encoding", "bom"),
    [
        ("utf-8", codecs.BOM_UTF8),
        ("utf-16-le", codecs.BOM_UTF16_LE),
        ("utf-16-be", codecs.BOM_UTF16_BE),
        ("utf-32-le", codecs.BOM_UTF32_LE),
        ("utf-32-be", codecs.BOM_UTF32_BE),
    ],
)
def test_bom_marked_text_is_checked_for_private_content(
    name: str, encoding: str, bom: bytes
) -> None:
    assert API_KEY in _file_owner_problems(name, bom + API_KEY.encode(encoding))


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
def test_wide_encoded_archive_members_are_checked(encoding: str) -> None:
    data = _zip_fixture((API_KEY * 20).encode(encoding))
    assert API_KEY in _file_owner_problems("synthetic.zip", data)


@pytest.mark.parametrize("domain", scrub_fixture.ALLOWED_EMAIL_DOMAINS)
def test_email_domain_suffix_lookalikes_are_scrubbed(domain: str) -> None:
    address = "person" + "@private" + domain
    assert scrub_fixture._is_foreign(address)
    assert address in problems(address)
    assert scrub_text(address, Path("synthetic-vault")) == "owner@example.com"
    for allowed in (domain, "subdomain." + domain):
        assert not scrub_fixture._is_foreign("person@" + allowed.upper())


@pytest.fixture
def private_words(monkeypatch: pytest.MonkeyPatch) -> None:
    """EMPLOYER and SURNAME as a contributor's private words, scrubbed to ACME and Owner."""
    monkeypatch.setattr(scrub_fixture, "PRIVATE_WORDS", {SURNAME: "Owner", EMPLOYER: "ACME"})


def _admit_media_fixture(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    data: bytes,
    spans: tuple[tuple[str, int, int], ...],
) -> None:
    admission = (
        hashlib.sha256(data).hexdigest(),
        tuple((kind, a, b, hashlib.sha256(data[a:b]).hexdigest()) for kind, a, b in spans),
    )
    monkeypatch.setitem(MEDIA_COLLISIONS, name, admission)


def _zip_fixture(
    body: bytes, *, name: str = "note.md", comment: bytes = b"", member_comment: bytes = b""
) -> bytes:
    stream = io.BytesIO()
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.comment = member_comment
    with zipfile.ZipFile(stream, "w") as archive:
        archive.comment = comment
        archive.writestr(info, body)
    return stream.getvalue()


@pytest.mark.usefixtures("private_words")
def test_reviewed_media_admission_is_an_exact_occurrence(monkeypatch: pytest.MonkeyPatch) -> None:
    name = "synthetic.m4a"
    data = b"\xff " + EMPLOYER.encode() + b"\x00"
    spans = (("private_word_casefold", 2, 5),)
    _admit_media_fixture(monkeypatch, name, data, spans)
    assert _file_owner_problems(name, data) == set()
    assert EMPLOYER in _file_owner_problems("renamed.m4a", data)
    assert "reviewed media bytes changed" in _file_owner_problems(name, b"clean replacement")
    changed = data + b" " + EMPLOYER.encode()
    _admit_media_fixture(monkeypatch, name, changed, spans)
    assert _file_owner_problems(name, changed) == {EMPLOYER}


@pytest.mark.usefixtures("private_words")
@pytest.mark.parametrize("category", ["private_word_casefold", "non_allowlisted_email"])
def test_reviewed_media_does_not_admit_another_identical_match(
    monkeypatch: pytest.MonkeyPatch, category: str
) -> None:
    value = EMPLOYER if category == "private_word_casefold" else FOREIGN_MAIL
    raw = value.encode()
    data = b"\xff " + raw + b"\0 " + raw
    _admit_media_fixture(monkeypatch, "synthetic.mp4", data, ((category, 2, 2 + len(raw)),))
    assert _file_owner_problems("synthetic.mp4", data) == {value}


@pytest.mark.parametrize(
    "extra",
    [
        *scrub_fixture.BANNED,
        "synthetic-owner",
        SURNAME,
        SLUG + "someone-notes",
        "projects/" + "-home-someone-notes",
        "projects/" + "-Users-someone-notes",
        "Bear" + "er " + "synthetic-token-value-12345",
        FOREIGN_MAIL,
    ],
)
@pytest.mark.usefixtures("private_words")
def test_reviewed_media_keeps_every_other_privacy_pattern(
    monkeypatch: pytest.MonkeyPatch, extra: str
) -> None:
    monkeypatch.setattr(scrub_fixture, "OWNER", "synthetic-owner")
    data = b"\xff " + EMPLOYER.encode() + b"\0 " + extra.encode()
    _admit_media_fixture(monkeypatch, "synthetic.mp4", data, (("private_word_casefold", 2, 5),))
    assert _file_owner_problems("synthetic.mp4", data) == set(problems(extra))
    assert _file_owner_problems("synthetic.mp4", data)


@pytest.mark.usefixtures("private_words")
def test_reviewed_media_keeps_another_category_with_the_same_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scrub_fixture, "OWNER", EMPLOYER.lower())
    data = b"\xff " + EMPLOYER.lower().encode() + b"\0"
    _admit_media_fixture(monkeypatch, "synthetic.mp4", data, (("private_word_casefold", 2, 5),))
    assert _file_owner_problems("synthetic.mp4", data) == {EMPLOYER.lower()}


@pytest.mark.usefixtures("private_words")
def test_reviewed_media_maps_only_known_boundaries_with_original_utf8_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = b"\0" * 65535 + "\U0001f600".encode() + b"\xf0\x9f "
    raw = EMPLOYER.encode()
    email = ("\u00e9@" + "corp.test").encode()
    data = prefix + raw + b"\0 " + email
    start = len(prefix)
    email_start = start + len(raw) + 2
    spans = (
        ("private_word_casefold", start, start + len(raw)),
        ("non_allowlisted_email", email_start, len(data)),
    )
    _admit_media_fixture(monkeypatch, "synthetic.mp4", data, spans)
    assert _file_owner_problems("synthetic.mp4", data) == set()


@pytest.mark.usefixtures("private_words")
@pytest.mark.parametrize("mistake", ["category", "span", "match_hash", "duplicate", "substring"])
def test_reviewed_media_rejects_a_record_that_is_not_an_actual_regex_match(
    monkeypatch: pytest.MonkeyPatch, mistake: str
) -> None:
    name = "synthetic.mp4"
    raw = EMPLOYER.encode()
    data = b"\xff " + raw + b"\0 " + FOREIGN_MAIL.encode()
    category, start, end = "private_word_casefold", 2, 5
    if mistake == "category":
        category = "non_allowlisted_email"
    elif mistake == "span":
        start, end = 0, 1
    elif mistake == "substring":
        category = "non_allowlisted_email"
        start = data.index(b"@") - 1
        end = len(data)
    _admit_media_fixture(monkeypatch, name, data, ((category, start, end),))
    digest, spans = MEDIA_COLLISIONS[name]
    if mistake == "match_hash":
        kind, a, b, _ = spans[0]
        monkeypatch.setitem(MEDIA_COLLISIONS, name, (digest, ((kind, a, b, "0" * 64),)))
    elif mistake == "duplicate":
        monkeypatch.setitem(MEDIA_COLLISIONS, name, (digest, spans + spans))
    with pytest.raises((AssertionError, UnicodeDecodeError)):
        _file_owner_problems(name, data)


@pytest.mark.parametrize("leak", [USERS + "\\someone", CREDENTIALS, API_KEY, FOREIGN_MAIL])
def test_compressed_zip_text_is_scanned_even_when_raw_bytes_are_clean(leak: str) -> None:
    data = _zip_fixture((("a source line " + leak + "\n") * 20).encode())
    assert problems(data.decode("utf-8", errors="replace")) == []
    assert set(problems(leak)) <= _file_owner_problems("synthetic.zip", data)


@pytest.mark.parametrize("suffix", [".whl", ".docx", ".bin"])
def test_zip_contents_are_checked_regardless_of_filename(suffix: str) -> None:
    data = _zip_fixture((API_KEY * 20).encode())
    assert not problems(data.decode("utf-8", errors="replace"))
    assert API_KEY in _file_owner_problems("synthetic" + suffix, data)


def test_nested_archives_refuse_without_recursive_extraction() -> None:
    data = _zip_fixture(_zip_fixture((API_KEY * 20).encode()), name="inner.bin")
    assert "nested archive" in _file_owner_problems("synthetic.zip", data)


@pytest.mark.usefixtures("private_words")
def test_zip_admissions_bind_both_archive_and_member(monkeypatch: pytest.MonkeyPatch) -> None:
    name = "synthetic.zip"
    body = b"\xff " + EMPLOYER.encode() + b"\0"
    data = _zip_fixture(body)
    _admit_media_fixture(monkeypatch, name, data, ())
    _admit_media_fixture(monkeypatch, name + "::note.md", body, (("private_word_casefold", 2, 5),))
    assert _file_owner_problems(name, data) == set()
    assert EMPLOYER in _file_owner_problems("renamed.zip", data)
    other_member = _zip_fixture(body, name="other.md")
    _admit_media_fixture(monkeypatch, name, other_member, ())
    assert EMPLOYER in _file_owner_problems(name, other_member)
    changed_member = _zip_fixture(body + b" changed")
    _admit_media_fixture(monkeypatch, name, changed_member, ())
    assert "reviewed media bytes changed" in _file_owner_problems(name, changed_member)


@pytest.mark.parametrize("where", ["name", "comment", "member_comment"])
def test_zip_names_and_comments_are_scanned(where: str) -> None:
    options: dict[str, Any] = {where: FOREIGN_MAIL if where == "name" else FOREIGN_MAIL.encode()}
    assert FOREIGN_MAIL in _file_owner_problems("synthetic.zip", _zip_fixture(b"clean", **options))


def test_zip_scan_limits_and_malformed_archives_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _zip_fixture(b"two bytes")
    monkeypatch.setattr(sys.modules[__name__], "MAX_ARCHIVE_BYTES", 1)
    assert "archive scan limit exceeded" in _file_owner_problems("synthetic.zip", data)
    monkeypatch.setattr(sys.modules[__name__], "MAX_ARCHIVE_BYTES", 100)
    monkeypatch.setattr(sys.modules[__name__], "MAX_ARCHIVE_MEMBERS", 0)
    assert "archive scan limit exceeded" in _file_owner_problems("synthetic.zip", data)
    assert "unreadable archive" in _file_owner_problems("synthetic.zip", b"not a ZIP")


def test_committed_fixtures_are_unchanged_by_a_rescrub() -> None:
    """A scrubber change that would rewrite a committed fixture line (the fixture holds a value
    the scrubber now treats as secret) fails here, not in a later capture. The path names
    nothing, so only the home folder, names, mail and by-key rules can fire."""
    nothing = Path("no-such-vault-root")
    paths = sorted((TESTS / "fixtures").rglob("*.jsonl"))
    assert paths, "no fixtures found: the folder was renamed or emptied"
    changed = [
        f"{path.relative_to(TESTS).as_posix()}:{number}"
        for path in paths
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if line and scrub_line(line, nothing) != line
    ]
    assert changed == []


def test_committed_transcripts_have_whole_parent_chains() -> None:
    paths = sorted((TESTS / "fixtures" / "transcripts").glob("*.jsonl"))
    assert paths, "no transcript fixtures found: the folder was renamed or emptied"
    for path in paths:
        entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        uuids = {e["uuid"] for e in entries}
        assert all(e.get("parentUuid") in uuids | {None} for e in entries), path.name


def test_kept_entries_link_past_the_dropped_ones() -> None:
    entries = [
        {"type": "user", "uuid": "u1", "parentUuid": None},
        {"type": "attachment", "uuid": "a1", "parentUuid": "u1"},
        {"type": "system", "uuid": "s1", "parentUuid": "a1"},
        {"type": "assistant", "uuid": "r1", "parentUuid": "s1"},
        {"type": "user", "uuid": "u2", "parentUuid": "r1"},
    ]
    kept = [
        json.loads(line)
        for line in kept_entries([json.dumps(e) for e in entries], frozenset({"user", "assistant"}))
    ]
    assert [(e["uuid"], e["parentUuid"]) for e in kept] == [
        ("u1", None),
        ("r1", "u1"),
        ("u2", "r1"),
    ]


@pytest.mark.usefixtures("private_words")
def test_scrub_replaces_paths_names_and_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    home = Path.home()
    vault = home / "src" / "Workspace" / "VIKI"
    sep = "\\" if sys.platform == "win32" else "/"  # the platform's own path separator
    # A synthetic, non-generic name: the real account can be a CI runner's generic one
    # (GENERIC_ACCOUNTS), which must not make this prove the account-name rule by accident.
    monkeypatch.setattr(scrub_fixture, "OWNER", "someone")
    frame = {
        "cwd": str(vault),
        "memory": str(home / ".claude" / "projects"),
        "note": f"someone {SURNAME} at {EMPLOYER} wrote to {FOREIGN_MAIL}",
        "bot": "noreply@anthropic.com",
    }
    text = scrub_text(json.dumps(frame), vault)
    assert problems(text) == []
    assert json.loads(text) == {
        "cwd": "%VAULT%",
        "memory": f"%HOME%{sep}.claude{sep}projects",
        "note": "owner Owner at ACME wrote to owner@example.com",
        "bot": "noreply@anthropic.com",
    }


def test_scrub_replaces_the_project_folder_slug() -> None:
    home = Path.home()
    vault = home / "src" / "Workspace" / "VIKI"
    sep = "\\" if sys.platform == "win32" else "/"
    slug = "".join(ch if ch.isalnum() else "-" for ch in str(vault))
    frame = {"transcript": f"{home}{sep}.claude{sep}projects{sep}{slug}{sep}a.jsonl"}
    text = scrub_text(json.dumps(frame), vault)
    assert problems(text) == []
    assert (
        json.loads(text)["transcript"]
        == f"%HOME%{sep}.claude{sep}projects{sep}%VAULT-SLUG%{sep}a.jsonl"
    )


def test_problems_flags_a_project_slug_the_username_rule_would_leave_standing() -> None:
    # A slug from another machine carries no `OWNER` to trip on, so the slug form needs its own
    # rule.
    assert problems(f'"projects/{SLUG}someone-notes"') == [SLUG]
    # Fixtures scrubbed before %VAULT-SLUG% existed carry the anonymised form; not an offence.
    assert problems(f'"projects/{SLUG}owner-src-Workspace-VIKI"') == []


@pytest.mark.usefixtures("private_words")
def test_scrub_line_redacts_identity_and_signatures_by_key() -> None:
    reply = {
        "type": "control_response",
        "response": {
            "response": {
                "account": {
                    "email": FOREIGN_MAIL,
                    "organization": "Someone's Org",
                    "apiProvider": "x",
                }
            }
        },
    }
    # A signature is opaque base64 that can spell anything, a banned name included.
    thinking = {"delta": {"type": "signature_delta", "signature": f"{EMPLOYER}{OWNER}XYZ=="}}
    line = scrub_line(json.dumps({"dir": "out", "frame": [reply, thinking]}), Path.home())
    account = json.loads(line)["frame"][0]["response"]["response"]["account"]
    assert account == {
        "email": "owner@example.com",
        "organization": "Example Org",
        "apiProvider": "x",
    }
    assert json.loads(line)["frame"][1]["delta"]["signature"] == "signature-placeholder"


def _path_transcript(raw: Path, root: Path) -> None:
    """A two-entry transcript naming `root` as a JSON value (escaped in the file's text, raw once
    parsed), forward-slashed, and as the CLI's project-folder slug."""
    slug = "".join(ch if ch.isalnum() else "-" for ch in str(root))
    entries = [
        {"type": "user", "uuid": "u1", "parentUuid": None, "cwd": str(root)},
        {
            "type": "assistant",
            "uuid": "r1",
            "parentUuid": "u1",
            "text": f"read {root.as_posix()}/CLAUDE.md, logged in projects/{slug}/r1.jsonl",
        },
    ]
    raw.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def _scrubbed_values(dest: Path) -> tuple[str, str]:
    entries = [json.loads(line) for line in dest.read_text(encoding="utf-8").splitlines()]
    return entries[0]["cwd"], entries[1]["text"]


SCRUBBED = ("%VAULT%", "read %VAULT%/CLAUDE.md, logged in projects/%VAULT-SLUG%/r1.jsonl")


def test_cli_vault_names_the_scratch_root(tmp_path: Path) -> None:
    scratch = tmp_path / "captures" / "stamp" / "scratch"
    raw, dest = tmp_path / "raw.jsonl", tmp_path / "out" / "fixture.jsonl"
    _path_transcript(raw, scratch)
    argv = [str(raw), str(dest), "--keep-types", "user,assistant", "--vault", str(scratch)]
    assert cli(argv) == 0
    assert _scrubbed_values(dest) == SCRUBBED
    assert problems(dest.read_text(encoding="utf-8")) == []


def test_cli_without_vault_exits_before_writing(tmp_path: Path) -> None:
    raw, dest = tmp_path / "raw.jsonl", tmp_path / "fixture.jsonl"
    _path_transcript(raw, tmp_path / "vault")
    with pytest.raises(SystemExit):
        cli([str(raw), str(dest)])
    assert not dest.exists()


def _streamed_capture(raw: Path, cwd: Path, pieces: list[str]) -> None:
    """One process's capture: the initialize request, `system/init` naming `cwd`, and a tool
    input streamed as `pieces`, so a path in it sits in the capture's text escaped twice."""
    request = {"type": "control_request", "request_id": "r0", "request": {"subtype": "initialize"}}
    init = {"type": "system", "subtype": "init", "cwd": str(cwd), "session_id": SID}
    lines = [{"dir": "in", "frame": request}, {"dir": "out", "frame": init}]
    for piece in pieces:
        delta = {"type": "input_json_delta", "partial_json": piece}
        event = {"type": "content_block_delta", "index": 1, "delta": delta}
        lines.append({"dir": "out", "frame": {"type": "stream_event", "event": event}})
    raw.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")


@pytest.mark.parametrize("name", ["VAULT", "HOME"])
def test_cli_scrubs_a_path_escaped_twice_in_a_streamed_tool_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    scratch, home = tmp_path / "scratch", tmp_path / "home"
    monkeypatch.setattr(scrub_fixture, "HOME", home)
    base = scratch if name == "VAULT" else home
    raw, dest = tmp_path / "raw.jsonl", tmp_path / "fixture.jsonl"
    tool_input = {"file_path": str(base / "_templates" / "daily-note.md")}
    _streamed_capture(raw, base, [json.dumps(tool_input)])
    assert cli([str(raw), str(dest), "--vault", str(scratch)]) == 0
    written = dest.read_bytes()
    assert b"\r" not in written  # LF on every platform, as the committed fixtures are
    lines = [json.loads(line) for line in written.decode("utf-8").splitlines()]
    assert lines[1]["frame"]["cwd"] == f"%{name}%"
    partial = lines[2]["frame"]["event"]["delta"]["partial_json"]
    sep = "\\" if sys.platform == "win32" else "/"
    assert json.loads(partial) == {"file_path": f"%{name}%{sep}_templates{sep}daily-note.md"}
    assert problems(written.decode("utf-8")) == []


def test_cli_refuses_a_path_split_across_two_deltas(tmp_path: Path) -> None:
    # Neither half is a whole path form, so both survive the per-line scrub; the home prefix they
    # carry (a capture's scratch folder lives under the home folder) must refuse the file.
    scratch = Path(USERS + "\\someone\\captures\\scratch")
    tool_input = json.dumps({"file_path": str(scratch / "_templates" / "daily-note.md")})
    cut = tool_input.index("scratch") + 3
    raw, dest = tmp_path / "raw.jsonl", tmp_path / "fixture.jsonl"
    _streamed_capture(raw, scratch, [tool_input[:cut], tool_input[cut:]])
    assert cli([str(raw), str(dest), "--vault", str(scratch)]) == 1
    assert not dest.exists()


# --- scripts/capture.py: the real CLI in a seeded scratch folder; the user-scope hooks still load


def _cfg(tmp_path: Path) -> config.Config:
    return config.Config(
        vault=tmp_path / "VIKI",
        claude_home=tmp_path / ".claude",
        cli=tmp_path / "claude.exe",
        edge=None,
        data_dir=tmp_path / "data",
    )


@pytest.fixture
def fake_claude(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """`Capture.open` without a process: `state["info"]` is the initialize reply every session
    gets back, `state["made"]` the controls that were built, each with a `closed` flag, and
    `state["spawns"]` the keyword arguments each transport was built with."""
    state: dict[str, Any] = {
        "info": {"account": {"apiProvider": "firstParty", "subscriptionType": "max"}},
        "made": [],
        "spawns": [],
    }

    class _Transport:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.pid = 4242
            state["spawns"].append(kwargs)

    class _Control:
        def __init__(self, transport: Any, can_use_tool: Any) -> None:
            self.closed = False
            state["made"].append(self)

        async def start(self) -> dict[str, Any]:
            return state["info"]

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(capture, "TappedTransport", _Transport)
    monkeypatch.setattr(capture, "ClaudeControl", _Control)
    return state


async def _ask(tool: str, tool_input: dict[str, Any]) -> capture.Ask:
    loop = asyncio.get_running_loop()
    return capture.Ask(tool, tool_input, capture.ToolPermissionContext(), loop.create_future())


async def test_expect_exact_allows_only_the_command_the_scenario_asked_for() -> None:
    asked: list[str] = []
    answer = capture.expect_exact("PowerShell", "Start-Sleep 30", asked)
    for command in ("Start-Sleep 30", "  Start-Sleep 30\n"):  # only surrounding space forgiven
        assert isinstance(answer(await _ask("PowerShell", {"command": command})), Allow)
    assert asked == ["PowerShell", "PowerShell"]


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        # Approving PowerShell by name let any command through, in a vault whose PreToolUse
        # guard does not fire for PowerShell.
        ("PowerShell", {"command": "Start-Sleep 30; Set-Content 00-inbox\\x.md probe"}),
        ("PowerShell", {"command": "Start-Sleep 60"}),
        ("PowerShell", {}),
        # Matching on the serialized input let any tool through, Write included.
        ("Write", {"file_path": "00-inbox\\x.md", "content": "Start-Sleep 30"}),
        ("Bash", {"command": "Start-Sleep 30"}),
    ],
)
async def test_expect_exact_denies_everything_else(tool: str, tool_input: dict[str, Any]) -> None:
    asked: list[str] = []
    answer = capture.expect_exact("PowerShell", "Start-Sleep 30", asked)
    assert isinstance(answer(await _ask(tool, tool_input)), Deny)
    assert asked == [tool]  # denied, but still recorded: the summary shows what was tried


async def test_capture_open_passes_a_subscription_login(
    tmp_path: Path, fake_claude: dict[str, Any]
) -> None:
    cap = capture.Capture(_cfg(tmp_path), tmp_path, tmp_path / "scratch")
    rec = capture.Recorder(tmp_path / "01.jsonl", "01")
    live = await cap.open(rec, Launch("s1"))
    assert live.init == fake_claude["info"]
    assert fake_claude["made"][0].closed is False
    await cap.close(live)
    rec.close()


@pytest.mark.parametrize(
    "account",
    [
        {},  # logged out
        {"apiProvider": "firstParty", "subscriptionType": ""},  # Console / API credits
        {"apiProvider": "bedrock", "subscriptionType": "max"},  # someone else's bill
    ],
)
async def test_capture_open_refuses_a_login_that_is_not_the_owners_subscription(
    tmp_path: Path, fake_claude: dict[str, Any], account: dict[str, Any]
) -> None:
    fake_claude["info"] = {"account": account}
    cap = capture.Capture(_cfg(tmp_path), tmp_path, tmp_path / "scratch")
    rec = capture.Recorder(tmp_path / "01.jsonl", "01")
    with pytest.raises(StartError):
        await cap.open(rec, Launch("s1"))
    assert fake_claude["made"][0].closed is True  # no process left billing the wrong account
    assert cap._open == []
    rec.close()


def _stub_run(monkeypatch: pytest.MonkeyPatch, cfg: config.Config) -> list[config.Config]:
    """`run()` with no real CLI: `cfg` as the loaded config, a clean billing preflight, and a
    `--version` answer. Returns the configs the preflight was asked about."""
    preflight: list[config.Config] = []

    def billing_problems(checked: config.Config) -> list[str]:
        preflight.append(checked)
        return []

    monkeypatch.setattr(capture.config, "load", lambda: cfg)
    monkeypatch.setattr(capture.config, "billing_problems", billing_problems)
    version = SimpleNamespace(stdout="2.1.281 (Claude Code)\n")
    monkeypatch.setattr(capture.winproc, "run", lambda argv, *, cwd, timeout: version)
    return preflight


async def test_run_closes_the_session_a_broken_scenario_abandoned(
    tmp_path: Path, fake_claude: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _cfg(tmp_path)
    _stub_run(monkeypatch, cfg)

    async def abandons(cap: capture.Capture, rec: capture.Recorder) -> capture.Obs:
        await cap.open(rec, Launch("abandoned"))
        raise TimeoutError("the turn never finished")

    monkeypatch.setitem(capture.SCENARIOS, "zz", ("abandons its session", abandons))
    assert await capture.run(["zz"]) == 0
    assert [control.closed for control in fake_claude["made"]] == [True]
    run_dir = next(cfg.captures_dir.iterdir())
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert "TimeoutError" in summary["results"]["zz"]["error"]


async def test_run_seeds_a_scratch_folder_and_never_starts_in_the_vault(
    tmp_path: Path, fake_claude: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _cfg(tmp_path)
    preflight = _stub_run(monkeypatch, cfg)

    async def opens(cap: capture.Capture, rec: capture.Recorder) -> capture.Obs:
        for launch in (Launch("s-one"), Launch("s-two"), Launch("s-one", resume=True)):
            await cap.close(await cap.open(rec, launch))
        return {"pass": True}

    monkeypatch.setitem(capture.SCENARIOS, "zz", ("opens sessions", opens))
    assert await capture.run(["zz"]) == 0
    run_dir = next(cfg.captures_dir.iterdir())
    root = run_dir / "scratch"
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()) == [
        "CLAUDE.md",
        "_templates/daily-note.md",
        "_templates/meeting.md",
        "_templates/weekly.md",
    ]
    assert (root / "CLAUDE.md").read_text(encoding="utf-8") == (
        "# Scratch project\n\nPrime directive 1: raw sources live in _raw/ and are never edited.\n"
    )
    assert [spawn["cwd"] for spawn in fake_claude["spawns"]] == [root] * 3
    assert [checked.vault for checked in preflight] == [root]
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["scratch_root"] == str(root)
    assert summary["cli_version"] == "2.1.281 (Claude Code)"
    assert summary["results"]["zz"]["session_ids"] == ["s-one", "s-two"]


@pytest.mark.parametrize("key", sorted(capture.REFUSED))
async def test_run_refuses_a_scenario_that_needs_the_vault_or_the_owners_sessions(
    tmp_path: Path,
    fake_claude: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    key: str,
) -> None:
    cfg = _cfg(tmp_path)
    preflight = _stub_run(monkeypatch, cfg)
    assert await capture.run(["01", key]) == 2
    assert capsys.readouterr().err == f"[{key}] refused: {capture.REFUSED[key]}\n"
    assert fake_claude["spawns"] == [] and preflight == []
    assert not cfg.captures_dir.exists()


async def test_run_refuses_a_set_claude_config_dir(
    tmp_path: Path,
    fake_claude: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # With it set, the CLI reads settings and writes transcripts in a folder that neither the
    # billing preflight nor the transcript checks look at.
    cfg = replace(_cfg(tmp_path), claude_config_dir_set=True)
    preflight = _stub_run(monkeypatch, cfg)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "config-dir-marker")
    assert await capture.run(["01"]) == 2
    err = capsys.readouterr().err
    assert "CLAUDE_CONFIG_DIR" in err and "config-dir-marker" not in err
    assert fake_claude["spawns"] == [] and preflight == []
    assert not cfg.captures_dir.exists()


# --- capture.py over the real ClaudeTransport and ClaudeControl, fed by the replay engine -------

# The whole in-process session finishes in milliseconds; this only stops a hang from stalling
# the suite for a scenario's four-minute turn timeout.
HANG_GUARD_S = 5.0


def _said(text: str) -> Entry:
    message = {"role": "assistant", "content": [{"type": "text", "text": text}]}
    return out({"type": "assistant", "message": message, "session_id": SID})


def _used(tool: str, tool_input: dict[str, Any]) -> Entry:
    block = {"type": "tool_use", "id": "toolu_agent", "name": tool, "input": tool_input}
    message = {"role": "assistant", "content": [block]}
    return out({"type": "assistant", "message": message, "session_id": SID})


def _thought(text: str) -> Entry:
    event = {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "thinking_delta", "thinking": text},
    }
    return out({"type": "stream_event", "event": event, "session_id": SID})


def _asked(request_id: str, tool: str, tool_input: dict[str, Any], **extra: Any) -> list[Entry]:
    """A permission request, and the answer the replay engine then expects from the client."""
    request = {
        "subtype": "can_use_tool",
        "tool_name": tool,
        "input": tool_input,
        "tool_use_id": f"toolu_{request_id}",
        **extra,
    }
    frame = {"type": "control_request", "request_id": request_id, "request": request}
    return [out(frame), permission_response_in(request_id)]


def _turn(*middle: Entry, subtype: str = "success") -> list[Entry]:
    return [user_in(), out(init_frame()), *middle, out(result_frame(subtype=subtype))]


_FETCH = {"url": "https://example.com"}
_SUGGESTION = {
    "type": "addRules",
    "rules": [{"toolName": "WebFetch"}],
    "behavior": "allow",
    "destination": "session",
}

# One script per scenario the replay engine can play. Every session a scenario opens replays its
# script from the start, and control calls the script does not list are answered by the engine.
SCENARIO_SCRIPTS: dict[str, list[Entry]] = {
    # A leading frame, before the first user_in(), pins the moment a live run once broke on: a
    # frame arriving before the client has even sent its initialize request.
    "01": [
        out({"type": "system", "subtype": "hook_started"}),
        *_turn(_said("Raw sources in _raw/ are never edited.")),
        *_turn(_said("B")),
    ],
    "06": _turn(_used("Agent", {"subagent_type": "Explore"}), _said("daily-note.md")),
    "07": _turn(
        *_asked("q1", "AskUserQuestion", {"questions": [{"question": "Tea or Coffee?"}]}),
        _said("You chose Tea."),
    ),
    "08": _turn(
        *_asked("f1", "WebFetch", _FETCH, permission_suggestions=[_SUGGESTION]),
        _said("The fetch was declined."),
    ),
    "09": [*_turn(out(text_delta("1")), subtype="error_during_execution"), *_turn(_said("done"))],
    "10": [*_turn(subtype="error_during_execution"), *_turn(_said("ok"))],
    "11": _turn(_said("B")),
    "12": _turn(_said("hi")),
    "13": [],
    "22": _turn(_thought("twin primes below 200"), _said("3831")),
    "25": [*_turn(_said("warm")), *_turn(), *_turn(), *_turn()],
    "26": _turn(*_asked("p1", "ExitPlanMode", {"plan": "1. list 2. report"})),
    "27": _turn(*_asked("a1", "WebFetch", _FETCH, agent_id="agent-1")),
    # The background agent's continuation turn arrives with no prompt of its own.
    "28": [
        *_turn(_said("launched")),
        out(init_frame()),
        _said("daily-note.md"),
        out(result_frame()),
    ],
}


def _replayed(monkeypatch: pytest.MonkeyPatch, script: list[Entry]) -> FakeSpawner:
    """The real TappedTransport, spawning the replay engine instead of claude.exe."""
    spawner = FakeSpawner(script)
    tapped = functools.partial(capture.TappedTransport, spawner=spawner, grace_s=0.05, poll_s=0.05)
    monkeypatch.setattr(capture, "TappedTransport", tapped)
    return spawner


def _recorded(path: Path) -> list[tuple[str, str]]:
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return [(line["dir"], line["frame"]["type"]) for line in lines]


async def test_capture_records_a_turn_through_the_real_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawner = _replayed(monkeypatch, _turn(_said("B")))
    cap = capture.Capture(_cfg(tmp_path), tmp_path, tmp_path / "scratch")
    rec = capture.Recorder(tmp_path / "01.jsonl", "01")
    async with asyncio.timeout(HANG_GUARD_S):
        live = await cap.open(rec, Launch(SID))
        frames = await cap.turn(live, "Reply with exactly the letter B.")
        await cap.close(live)
    rec.close()
    assert capture.types_of(frames) == ["system/init", "assistant", "result/success"]
    assert capture.text_of(frames) == "B"
    assert _recorded(tmp_path / "01.jsonl") == [
        ("in", "control_request"),  # initialize
        ("out", "control_response"),
        ("in", "user"),
        ("out", "system"),
        ("out", "assistant"),
        ("out", "result"),
        ("out", EXIT),
    ]
    assert [frame["type"] for frame in spawner.children[0].process.replayer.seen] == [
        "control_request",
        "user",
    ]


@pytest.mark.parametrize("key", sorted(SCENARIO_SCRIPTS))
async def test_scenario_runs_through_the_real_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    cfg = _cfg(tmp_path)
    # Never created: the fake ignores its cwd. A short root keeps the transcript path, which
    # repeats the root inside the project folder's name, under Windows' MAX_PATH.
    root = Path("scratch")
    _replayed(monkeypatch, SCENARIO_SCRIPTS[key])
    real_sleep = asyncio.sleep

    async def no_wait(delay: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", no_wait)  # the scenarios' waits on the real CLI
    monkeypatch.setattr(capture, "SHARED", {"two_turns": "base-session"})
    monkeypatch.setattr(capture, "new_id", lambda: SID)
    notice = {"type": "user", "message": {"content": "<task-notification><status>completed"}}
    _transcript(cfg, root, SID, [notice])  # scenario 28 reads its own transcript back
    cap = capture.Capture(cfg, tmp_path, root)
    rec = capture.Recorder(tmp_path / f"{key}.jsonl", key)
    _, scenario = capture.SCENARIOS[key]
    try:
        async with asyncio.timeout(HANG_GUARD_S):
            obs = await scenario(cap, rec)
    finally:
        await cap.close_all()
        rec.close()
    assert obs["pass"] is True
    assert cap._open == []


def test_capture_never_reads_the_configured_vault(tmp_path: Path) -> None:
    assert "cfg.vault" not in inspect.getsource(capture)
    # The CLI's project folder replaces every non-ASCII-alphanumeric character, é included.
    root = tmp_path / "scratché"
    cfg = _cfg(tmp_path)
    assert capture._project_dir(cfg, root) == catalog.project_dir(cfg.claude_home, root)


def test_scenario_23s_capture_is_named_so_the_scrubber_refuses_it(tmp_path: Path) -> None:
    poison = tmp_path / f"{capture._capture_name('23')}.jsonl"
    dest = tmp_path / "out.jsonl"
    assert capture._capture_name("16") == "16"
    # Neither file exists: a scenario whose capture may be committed is read (and fails on the
    # missing file), scenario 23's is refused before anything is read.
    with pytest.raises(FileNotFoundError):
        scrub_main(tmp_path / "16.jsonl", dest, tmp_path)
    assert DO_NOT_COMMIT in poison.name
    assert scrub_main(poison, dest, tmp_path) == 1
    assert not dest.exists()


def _transcript(cfg: config.Config, root: Path, name: str, entries: list[dict[str, Any]]) -> None:
    path = capture._transcript(cfg, root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def test_small_recent_session_skips_the_copy_a_previous_run_forked(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    # Never created. A short root keeps the transcript path, which repeats the root inside the
    # project folder's name, under Windows' MAX_PATH.
    root = Path("scratch")
    owners = [{"version": "2.1.266", "entrypoint": "cli", "pad": "x" * 200}] * 4
    # A fork copy carries the source's entries plus the turn this script drove, so it is always
    # smaller and newer than the session it came from.
    copy = [owners[0], {"version": "2.1.266", "entrypoint": config.ENTRYPOINT}]
    _transcript(cfg, root, "owner-session", owners)
    _transcript(cfg, root, "fork-copy", copy)
    found = capture._small_recent_session(cfg, root, min_version=(2, 1, 266))
    assert found is not None and found.stem == "owner-session"

    _transcript(cfg, root, "owner-session", [{"version": "2.1.200", "entrypoint": "cli"}])
    assert capture._small_recent_session(cfg, root, min_version=(2, 1, 266)) is None

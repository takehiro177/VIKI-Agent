# Contributing to VIKI Agent

VIKI Agent is licensed under the [GNU Affero General Public License](LICENSE), version 3 or any
later version. External contributions also require the
[Contributor License Agreement](CLA.md) before merge. You keep ownership while granting the
Project Owner copyright and patent rights, including commercial relicensing rights. Using the app
does not require a CLA.

## Before submitting

- Read [AGENTS.md](AGENTS.md) for the repository's rules and narrower test commands.
- Run the full check from the repository root: `uv run python scripts/check.py` runs the lint,
  type checks and test suites, and `uv run python scripts/linux_check.py` runs the Linux suites
  in Docker.
- Describe the change, how you verified it, and any remaining limitations.
- Include only material you are authorized to contribute. Identify third-party files, their
  sources and licenses, and any rights you cannot grant; their licenses must allow distribution
  under the GNU Affero General Public License.
- Keep secrets, personal conversation records, credentials and private vault content out of the
  contribution. Do not post private contact details to sign the CLA.

## Accept the agreement

Ask a maintainer in your pull request for the approved canonical repository commit containing CLA
version 1.0 and the README's Project Owner identification. Read both at that immutable revision,
then post this statement in your pull request from your own authenticated GitHub account, filling
in the fields:

> I have read and agree to VIKI Agent Contributor License Agreement version 1.0 and intend this
> comment to be my electronic signature. The canonical CLA and README revision is: [full commit
> SHA and links to both files at that revision]. I accept for my contributions in this pull request
> through head commit: [full head SHA]. I act [for myself / as an authorized representative of named
> entity]. I have authority to grant these rights. Third-party material and exceptions: [list or none].

Every external author must accept for the rights they hold, or be covered by a representative's
express authorization. Update the acceptance when the pull request head changes. A commit
`Signed-off-by` trailer or a checked box is not a substitute for this acceptance. If you cannot
make the capacity or authority representations, discuss an authorized arrangement before
contributing; an account or comment does not supply missing authority.

## Review before merge

A maintainer checks your acceptance by hand: the head commit it covers, every contributing rights
holder, the CLA and README revision you accepted, and the third-party restrictions you disclosed.
No bot or signing service confirms it, so keep your acceptance comment, because it is the record.
The Project Owner does not need to sign an agreement with themselves for their own work.
Acceptance does not guarantee merge. GitHub requests the Project Owner's review on every pull
request through `.github/CODEOWNERS`, and a pull request merges only after that approval.

## Release a version (maintainers)

1. Bump the version in the three version fields, keeping them identical: `pyproject.toml`,
   `desktop/package.json` and `frontend/package.json`.
2. Lock the roots: `uv lock`, then `npm install --package-lock-only` in `frontend/` and in
   `desktop/`.
3. Add the release's entries to `frontend/src/help/whats-new.md`.
4. Open a pull request with these changes and merge it once a maintainer approves it.
5. Tag the merge commit and push the tag: `git tag v<V> <merge commit>`, then
   `git push <remote> v<V>`.
6. Pushing the tag starts `.github/workflows/release.yml`, which builds the three installers and
   stages their checksums, update feeds and SBOMs into a draft release (its `release` job's
   `gh release create`). Watch it with `gh run watch`.
7. Once it finishes, check the draft's assets: `gh release view v<V> --json assets`.
8. Write the release notes on the draft release, then publish it: `gh release edit v<V>
   --draft=false`.

To check the workflow without publishing anything, run it by hand instead of pushing a tag:
`gh workflow run release.yml --ref main`. This builds and stages the same assets as a real
release but creates none: only a tag push runs the workflow's `release` job.

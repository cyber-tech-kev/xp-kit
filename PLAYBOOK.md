# The xp-kit playbook

A practical guide to running an XP and badge system for a club, team or community: setting it up,
designing it so people actually care, launching it, and handing it off when you move on.

1. [Set it up](#1-set-it-up-15-minutes)
2. [Design your XP economy](#2-design-your-xp-economy)
3. [Make your badges](#3-make-your-badges)
4. [Launch it](#4-launch-it)
5. [Run it week to week](#5-run-it-week-to-week)
6. [Keep it fair and safe](#6-keep-it-fair-and-safe)
7. [Hand it off](#7-hand-it-off)
8. [Troubleshooting](#8-troubleshooting)

---

## 1. Set it up (15 minutes)

You need a GitHub account and the [GitHub CLI](https://cli.github.com) (`gh auth login`).

1. On the xp-kit repo, click **Use this template → Create a new repository**.
   Put it under your group's GitHub **organization** if you have one, not your personal account, so it
   survives leadership changes. Make it **public** (GitHub Pages is free for public repos).
2. Clone it and run the setup script:
   ```bash
   gh repo clone YOUR-ORG/xp && cd xp
   ./setup.sh
   ```
   It fills in your repo details, makes you an officer, turns on GitHub Pages, creates the join label and
   a pinned **XP desk** issue, optionally connects Discord, and deploys.
3. Your board is live at `https://YOUR-ORG.github.io/xp` within a minute or two.

<details>
<summary>Doing it by hand instead</summary>

1. In `xp-config.yml`, set `group.owner` (your user/org), `group.repo` and add yourself under `officers`.
2. **Settings → Pages:** Source = **GitHub Actions**.
3. **Issues → Labels:** create `xp-join`.
4. Open an issue titled **XP desk** and pin it.
5. Optional: **Settings → Secrets → Actions:** add `DISCORD_WEBHOOK_XP`.
6. **Actions → build-and-deploy → Run workflow.**
</details>

## 2. Design your XP economy

This is the part that decides whether people care. A few rules of thumb:

**Reward what you want more of.** List the 3-5 behaviors that make your group better (showing up, helping
newcomers, finishing projects, presenting) and make those your sources. If it isn't a source, the system
says it doesn't matter.

**Make effort worth more than presence.** Attendance should be the smallest award (5-15 XP). Things that
take real work (a project, a talk, running an event) should be worth 3-6x a meeting. Otherwise the board
just ranks who has the most free time.

**Space levels about 2x apart** (0, 100, 300, 700, 1500). The first level-up should come within the first
month for anyone who shows up regularly. The top level should feel rare, reached by a few people a year.

**Give badges identity, not just volume.** Levels say *how much*; badges say *what kind* of member someone is.
A good set mixes:
- one **consistency** badge (`streak`): rewards showing up, the habit everything else depends on
- several **contribution** badges (`count`): one per kind of contribution
- one **founding member** badge (`joined_before`): creates urgency at launch, then can never be earned again

**Use tiers for things people repeat** (bronze / silver / gold), and one-time badges for milestones.
Make bronze easy (first time), gold impressive.

**Name things in your group's language.** `packet_sniffer` beats `level_2` for a cyber club; `bookworm`
beats it for a book club. This is what makes it feel like *yours*.

Not sure where to start? [PROMPTS.md #1](PROMPTS.md#1-design-your-whole-xp-system) has an LLM draft the whole
thing from a description of your group.

## 3. Make your badges

Every badge is generated from your config: shape (`shield`, `hex` or `coin`), color, icon, and up to three
metal tiers. Iterate locally:

```bash
pip install pyyaml
python scripts/xp.py preview     # writes _preview/index.html with every badge in every tier
```

- **Icons:** 16 are built in. For anything else, use [PROMPTS.md #2](PROMPTS.md#2-draw-a-custom-badge-icon) or
  drop in an SVG from a free icon set, save it as `icons/<name>.svg`, and set `icon: <name>`.
- **Colors:** palette names or any hex. [PROMPTS.md #3](PROMPTS.md#3-turn-your-brand-colors-into-a-theme)
  turns your brand colors into a full theme.
- **Safety:** custom icons are parsed and rebuilt from an allowlist of shapes and attributes. Scripts, links,
  images, styles, text and outside references are rejected with a clear message. The `validate` workflow runs
  the same checks on every pull request.

## 4. Launch it

Launch it live. Sending a link and hoping people click it doesn't work.

- **Do the sign-up in a meeting.** Show the board, give everyone 10 minutes to make a GitHub account and join,
  then award attendance on the spot so they see their XP appear. Seeing it happen together is the hook.
- **Use the founding member badge.** "Join before [date] and you get this forever" is the strongest
  reason to sign up now instead of "later".
- **Sell the GitHub profile angle.** Members can embed their live level and badges on their GitHub profile.
  For students and early-career folks, that's something real to show.
- **Pin the links** (leaderboard and join form) wherever your group talks. [PROMPTS.md #5](PROMPTS.md#5-announce-it-to-your-members)
  writes the announcement.

Joining requires a GitHub account, and GitHub accounts require users to be at least 13. For younger groups,
an adult organizer can track XP with `/award` for members who already have accounts; don't create accounts
for minors.

## 5. Run it week to week

**Awarding XP:** comment on the pinned XP desk issue.

```
/award @alex @sam attendance 2027-01-20        # several people at once
/award @alex project robot-arm-v2              # default XP from the config
/award @alex award 50 organized the hackathon  # you choose the amount
/award @alex correction -25 duplicate entry    # undo a mistake
```

The bot replies with exactly what it did. `unique` sources ignore repeats of the same ref, so re-running a
command is safe. Use a consistent ref style (dates for meetings, names or links for projects).

**A 2-minute routine after each meeting:** award attendance to everyone who came, plus anything special.
That's it. Level-ups and badges post to Discord automatically if you connected it.

**Automating it:** your other repos can award XP on their own (a CTF board, a quiz bot, a project tracker)
by sending a `repository_dispatch` event:

```bash
gh api repos/YOUR-ORG/xp/dispatches -f event_type=xp-event \
  -F 'client_payload={"user":"alexdoe","source":"project","ref":"robot-arm-v2"}'
```

The caller needs a token with write access to the xp repo. Use a fine-grained token scoped to that one repo.

**New season?** Add it under `seasons:` in the config. The "this season" tab resets; all-time XP stays.

## 6. Keep it fair and safe

- **Only officers can award.** The `officers:` list is checked on every `/award`. Keep at least two, so the
  system doesn't depend on one person.
- **Typos can't become disasters.** `max_award` caps any single award, and negative XP is only allowed as an
  explicit `correction`.
- **History is append-only.** The ledger is never edited, only added to, so git history shows who awarded what
  and when.
- **Consent first.** Joining requires ticking a box that says the board is public. Only GitHub usernames are
  shown. If someone wants off, remove them from `data/members.json` (their ledger lines are then ignored).
- **Workflows are hardened.** User-written text (comments, form answers) is only passed through environment
  variables and files, never pasted into shell commands, so a crafted comment can't run code.
- **Branch protection:** if you protect `main`, allow GitHub Actions to push, or the bots can't record XP.

## 7. Hand it off

The system is built to outlive you:

- Repo lives in the group's org, not your account.
- Before you leave: add the next leaders to `officers:`, make them org owners, and add the next season.
- Everything else (point values, badges, look) is in one config file they can change without touching code.

## 8. Troubleshooting

| Problem | Fix |
|---|---|
| Bot never replies to `/award` or join | Check the **Actions** tab. A 403 on `git push` means Actions can't write: **Settings → Actions → General → Workflow permissions → Read and write** (at the org level if the repo setting is greyed out). |
| `git push` rejected ("fetch first") | The bots commit to the repo too. Run `git pull --rebase`, then push. `git config --global pull.rebase true` makes this automatic. |
| Board didn't update | Wait a minute, then hard-refresh (Cmd/Ctrl+Shift+R). GitHub Pages caches. |
| Badge images on a GitHub profile are stale | GitHub caches profile images for a while. They catch up on their own. |
| "isn't valid YAML" | Usually a missing space after a colon or a tab. The error gives the line number. |
| Join issue says to tick the box | The consent checkbox wasn't ticked. Open a new Join XP issue. |

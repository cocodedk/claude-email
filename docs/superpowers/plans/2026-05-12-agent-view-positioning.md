# Agent View Positioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Position claude-email relative to Claude Code's newly announced built-in **Agent View** (https://claude.com/blog/agent-view-in-claude-code, May 2026) in README and the bilingual website, so users understand what claude-email offers that the native CLI feature does not.

**Architecture:** Docs-only change on branch `docs/agent-view-positioning`. Adds a short, parallel "Compared to Claude Code Agent View" block to (a) `README.md`, (b) `website/index.html`, and (c) `website/fa/index.html` — in lockstep, with identical framing and three bullets: *Remote-first*, *Inter-agent bus*, *Multi-surface persistence*. No code changes, no test changes.

**Tech Stack:** Markdown (README), static HTML/CSS (website), Persian (Farsi) RTL HTML for the `fa/` mirror. No build step.

**Out of scope / deferred (peer-coordinated):**
- **Status taxonomy alignment** between Agent View's `waiting | working | completed` and the dashboard's agent-row vocabulary. Needs `agent-Claude-Email-App` (the frontend agent) at the table; tracked separately for a later branch.
- **Spawner refactor to use `claude --bg` / `/bg`**. Worth doing the next time `src/spawner.py` is touched; not part of this plan.

---

## File Structure

Three files modified, nothing created:

| File | Responsibility | Change |
|------|----------------|--------|
| `README.md` | Repo elevator pitch + reference | Insert a new `## Compared to Claude Code Agent View` section between the existing intro/diagram block (ends line 52) and `## Features` (line 54) |
| `website/index.html` | EN landing page | Insert a new `<section class="comparison">` between the `how-it-works` section (closes ~line 191) and `installation` (opens ~line 193) |
| `website/fa/index.html` | FA landing page (RTL lockstep) | Same as above, Persian copy, RTL-compatible |

The new website section reuses existing `.container`, `.section-head`, `.section-num`, `.section-title`, and `.prose` classes — no new CSS required. Verify visually that the dark/light theme already styles a generic `<section>` correctly.

---

## Task 1: README positioning section

**Files:**
- Modify: `README.md` (insert between line 52 and line 54)

- [ ] **Step 1: Insert the positioning subsection**

Use Edit to insert this block after the closing fence of the architecture diagram (line 52 reads `` ``` ``) and before `## Features` (line 54).

```markdown
## Compared to Claude Code's Agent View

Claude Code now ships a built-in [Agent View](https://claude.com/blog/agent-view-in-claude-code) — a terminal-side overview of every concurrent session, with inline replies and `claude --bg` for backgrounded tasks. It is excellent when you are at the laptop.

`claude-email` starts where Agent View stops:

- **Remote-first.** Drive every agent from any inbox — phone, web, mutt — without ssh, VPN, or a terminal open. Agent View is local to one machine; an email is not.
- **Inter-agent bus.** Agents talk to *each other* over the MCP chat bus via `chat_message_agent`, not just to you. Agent View has no agent-to-agent channel.
- **Persistent, multi-surface state.** Conversations, task history, and liveness live in SQLite (WAL) and surface on a graphical CRT dashboard at `/dashboard`, the Android companion (in progress), and the same email thread you started in — across reboots and bus restarts.

In short: Agent View is the cockpit when you're at the laptop; `claude-email` is the radio when you're not.

```

- [ ] **Step 2: Verify the edit landed cleanly**

Run: `sed -n '50,80p' README.md`
Expected: closing ` ``` ` of the diagram on line 52, blank line, the new `## Compared to Claude Code's Agent View` heading, the three bullets, and the `## Features` heading still present immediately after the new block.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(readme): position claude-email vs Claude Code Agent View"
```

---

## Task 2: English website section

**Files:**
- Modify: `website/index.html` (insert between the `how-it-works` section close and the `installation` section open — around line 191)

- [ ] **Step 1: Read the boundary so the insertion point is unambiguous**

Run: `sed -n '188,195p' website/index.html`
Expected: end of `<div class="arch-block">…</div>`, then `</div></div></section>` closing `how-it-works`, then `<section class="installation">` opening.

- [ ] **Step 2: Insert the comparison section**

Use Edit. Find the unique anchor:

```html
  </section>

  <section class="installation">
```

Replace with:

```html
  </section>

  <section class="comparison">
    <div class="container">
      <div class="section-head">
        <div class="section-num">03</div>
        <h2 class="section-title">
          <small>Positioning</small>
          Compared to Claude Code's Agent View
        </h2>
      </div>

      <div class="content">
        <div class="prose">
          <p>
            Claude Code now ships a built-in
            <a href="https://claude.com/blog/agent-view-in-claude-code">Agent View</a>
            — a terminal-side overview of every concurrent session, with inline replies
            and <code>claude --bg</code> for backgrounded tasks. It is excellent when you
            are at the laptop. <code>claude-email</code> starts where Agent View stops.
          </p>
          <ul>
            <li><strong>Remote-first.</strong> Drive every agent from any inbox — phone, web, <code>mutt</code> — without ssh, VPN, or a terminal open. Agent View is local to one machine; an email is not.</li>
            <li><strong>Inter-agent bus.</strong> Agents talk to <em>each other</em> over the MCP chat bus via <code>chat_message_agent</code>, not just to you. Agent View has no agent-to-agent channel.</li>
            <li><strong>Persistent, multi-surface state.</strong> Conversations, task history, and liveness live in SQLite (WAL) and surface on a graphical CRT dashboard at <code>/dashboard</code>, the Android companion (in progress), and the same email thread you started in — across reboots and bus restarts.</li>
          </ul>
          <p>
            In short: Agent View is the cockpit when you're at the laptop;
            <code>claude-email</code> is the radio when you're not.
          </p>
        </div>
      </div>
    </div>
  </section>

  <section class="installation">
```

- [ ] **Step 3: Bump the `installation` section number**

The new comparison section took `section-num` `03`. The existing `installation` section is also `03`. Edit `website/index.html` to change `installation`'s `<div class="section-num">03</div>` to `04`. Find this unique anchor:

```html
  <section class="installation">
    <div class="container">
      <div class="section-head">
        <div class="section-num">03</div>
        <h2 class="section-title">
          <small>Getting Started</small>
```

Replace with:

```html
  <section class="installation">
    <div class="container">
      <div class="section-head">
        <div class="section-num">04</div>
        <h2 class="section-title">
          <small>Getting Started</small>
```

- [ ] **Step 4: Visual smoke check (manual, headless OK)**

Open `website/index.html` in any browser (or `python3 -m http.server` from `website/`). Confirm:
- New section renders between "Mechanics" and "Install & post your first letter"
- Bullet list is styled (inherits `.prose` or default `<ul>` styling — if it looks unstyled, fall through to Step 5)
- Section numbering reads 01, 02, 03, 04 in order down the page

- [ ] **Step 5: Fallback styling if `<ul>` looks unstyled inside `.prose`**

If the visual check shows the `<ul>` looks broken (no bullets, wrong indent, wrong colour), append the following minimal rule to `website/styles.css`:

```css
.comparison .prose ul {
  margin: 1em 0;
  padding-left: 1.5em;
  list-style: disc;
}
.comparison .prose li {
  margin: 0.4em 0;
  line-height: 1.6;
}
```

Otherwise skip this step.

- [ ] **Step 6: Commit**

```bash
git add website/index.html website/styles.css
git commit -m "docs(website): add Agent View comparison section (EN)"
```

(If Step 5 was skipped, `website/styles.css` won't be in the diff — `git add` will simply skip it.)

---

## Task 3: Persian website section (lockstep)

**Files:**
- Modify: `website/fa/index.html` (same insertion point as English)

- [ ] **Step 1: Read the boundary**

Run: `sed -n '188,200p' website/fa/index.html`
Expected: end of `how-it-works` section, opening of `installation` section. Anchor strings differ from EN (Persian + RTL).

- [ ] **Step 2: Insert the comparison section in Persian**

Use Edit. Find the unique anchor:

```html
  </section>

  <section class="installation">
```

Replace with:

```html
  </section>

  <section class="comparison">
    <div class="container">
      <div class="section-head">
        <div class="section-num">۰۳</div>
        <h2 class="section-title">
          <small>جایگاه</small>
          در مقایسه با Agent View کلود کد
        </h2>
      </div>

      <div class="content">
        <div class="prose">
          <p>
            کلود کد اکنون قابلیتی به نام
            <a href="https://claude.com/blog/agent-view-in-claude-code">Agent View</a>
            دارد — یک نمای ترمینالی از همه نشست‌های هم‌زمان، با پاسخ درون‌خطی و
            <code>claude --bg</code> برای کارهای پس‌زمینه. وقتی پای لپ‌تاپ نشسته‌اید عالی است.
            <code>claude-email</code> دقیقاً از همان‌جایی شروع می‌کند که Agent View تمام می‌شود.
          </p>
          <ul>
            <li><strong>اول از همه از راه دور.</strong> هر عامل را از هر صندوق ایمیلی — موبایل، وب، <code>mutt</code> — هدایت کنید، بی‌نیاز از ssh، VPN یا ترمینال باز. Agent View محدود به یک ماشین است؛ یک ایمیل نیست.</li>
            <li><strong>گذرگاه میان عامل‌ها.</strong> عامل‌ها می‌توانند با <code>chat_message_agent</code> روی گذرگاه چت MCP <em>به یکدیگر</em> پیام بدهند، نه فقط به شما. Agent View هیچ کانال عامل‌به‌عامل ندارد.</li>
            <li><strong>وضعیت پایدار و چندسطحی.</strong> گفت‌وگوها، تاریخچه وظایف و وضعیت زنده‌بودن در SQLite (حالت WAL) ذخیره می‌شوند و در یک داشبورد گرافیکی CRT روی <code>/dashboard</code>، در همراه اندروید (در دست ساخت)، و در همان رشته ایمیلی که آغاز کرده‌اید نمایان می‌گردند — حتی پس از ری‌استارت سیستم یا گذرگاه.</li>
          </ul>
          <p>
            خلاصه: Agent View کابینِ خلبان شماست وقتی پای لپ‌تاپ‌اید؛
            <code>claude-email</code> بی‌سیمِ شماست وقتی نیستید.
          </p>
        </div>
      </div>
    </div>
  </section>

  <section class="installation">
```

- [ ] **Step 3: Bump the `installation` section number to ۰۴**

Find this unique anchor in `website/fa/index.html`:

```html
  <section class="installation">
    <div class="container">
      <div class="section-head">
        <div class="section-num">۰۳</div>
        <h2 class="section-title">
          <small>شروع به کار</small>
```

Replace with:

```html
  <section class="installation">
    <div class="container">
      <div class="section-head">
        <div class="section-num">۰۴</div>
        <h2 class="section-title">
          <small>شروع به کار</small>
```

- [ ] **Step 4: Visual smoke check (RTL)**

Open `website/fa/index.html` and confirm:
- New section renders right-to-left correctly (no LTR leak in bullet rendering)
- Latin code spans (`claude --bg`, `chat_message_agent`, `/dashboard`, `mutt`, `claude-email`) display inline cleanly without flipping their internal letter order
- Persian section numbers read ۰۱, ۰۲, ۰۳, ۰۴ in order

- [ ] **Step 5: Commit**

```bash
git add website/fa/index.html
git commit -m "docs(website): add Agent View comparison section (FA)"
```

---

## Task 4: Verify nothing else broke

**Files:** none modified — pure verification.

- [ ] **Step 1: Line-limit script passes**

Run: `scripts/check-line-limit.sh`
Expected: exit 0, no output (the script only scans Python source under `src/`, `chat/`, plus `main.py` and `chat_server.py` — none of which were touched).

- [ ] **Step 2: Full test suite passes**

Run: `.venv/bin/pytest tests/ -q`
Expected: `1212 passed` (or whatever the current count is — must match what was on `master` before this branch).

- [ ] **Step 3: Confirm three docs files changed, nothing else**

Run: `git diff master --stat`
Expected output mentions only:
- `README.md`
- `website/index.html`
- `website/fa/index.html`
- *optionally* `website/styles.css` if Task 2 Step 5 fired
- *this plan file* `docs/superpowers/plans/2026-05-12-agent-view-positioning.md`

No code paths under `src/`, `chat/`, `tests/`, `scripts/`.

---

## Task 5: `/simplify` pass on the diff

Per the durable feedback rule (`/simplify` runs before any PR, including docs PRs).

**Files:** none directly modified by this task — it may *suggest* edits to the three docs files from Tasks 1–3.

- [ ] **Step 1: Run `/simplify` on the branch diff**

Invoke the `simplify` skill. Scope it to this branch's diff (`git diff master`). Expect mostly a no-op since the change is docs, but it may flag awkward phrasing, duplicated bullets between README and the website, or redundant section copy across EN/FA.

- [ ] **Step 2: Apply any agreed edits**

If `/simplify` proposes a change you accept, edit the relevant file(s) inline. If you reject a proposal, note why in the PR description. Re-run Task 4 (verify) if any file changed.

- [ ] **Step 3: Commit fixups (only if Step 2 produced changes)**

```bash
git add README.md website/index.html website/fa/index.html
git commit -m "docs: tighten Agent View comparison copy after simplify pass"
```

If Step 2 produced no changes, skip the commit.

---

## Task 6: Open PR

**Files:** none.

- [ ] **Step 1: Push the branch**

```bash
git push -u origin docs/agent-view-positioning
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --title "docs: position claude-email vs Claude Code Agent View" --body "$(cat <<'EOF'
## Summary
- Add a "Compared to Claude Code's Agent View" section to README, EN website, and FA website (lockstep).
- Frame the three differentiators: remote-first, inter-agent bus, persistent multi-surface state.
- Reference: https://claude.com/blog/agent-view-in-claude-code

## Out of scope
- Dashboard status-vocabulary alignment with Agent View (`waiting | working | completed`). Needs `agent-Claude-Email-App` coordination — separate branch.
- Spawner refactor to use `claude --bg`. Deferred until `src/spawner.py` is next touched.

## Test plan
- [ ] `scripts/check-line-limit.sh` exits 0
- [ ] `.venv/bin/pytest tests/ -q` matches pre-branch count
- [ ] EN page renders the new section between "Mechanics" and "Install"
- [ ] FA page renders RTL correctly, numbers read ۰۱–۰۴

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Return the PR URL**

The output of the previous command includes the PR URL. Surface it back to the user.

# D3 seminar

A private availability poll for finding one seminar time when the whole group can attend. The MVP uses a static website, a local Python backend, SQLite storage, and personalized emails through JMU’s SMTP server. Python 3.10+ is sufficient; there are no runtime packages to install.

Every participant gets the same short list of times and a personal link. They mark each option **Available**, **If necessary**, or **Unavailable**. Blank options stay **Unknown**. Partial answers can be saved and completed later, and other participants cannot see them.

The organizer dashboard shows saved answers, completion counts, notes, and availability for each candidate time. Finalization requires every required attendee to mark the chosen time Available. A majority, missing participants, unknown answers, and conditional replies cannot satisfy that rule.

## Run locally

For a fresh checkout, initialize your private settings and one test participant:

```sh
python3 scripts/seminar.py init --email YOUR_UNIVERSITY_EMAIL --name 'Your name' --smtp-user YOUR_JMU_USERNAME
python3 scripts/server.py
```

Keep the server running. In another terminal, from the project folder:

```sh
python3 scripts/seminar.py sync
python3 scripts/seminar.py open-test
python3 scripts/seminar.py admin
```

The last two commands open your personal test invitation and authenticated organizer dashboard on macOS. The public preview is at **http://127.0.0.1:8765/**. The dashboard key is kept out of the address bar after loading and lasts only for that page session; rerun `admin` after a browser reload.

The supplied configuration has four 30-minute sample options, a sample response deadline, and **one required participant for the test**. It does not claim that the other 19 people have been invited.

## Test with your university mailbox

Preview a personalized poll invitation without sending it:

```sh
python3 scripts/seminar.py send invite --to YOUR_UNIVERSITY_EMAIL
```

Review the `.eml` in `.private/outbox/`. To send only to your configured test address:

```sh
python3 scripts/seminar.py test-mail invite
```

Enter your JMU password in Terminal. It is not displayed or stored. SMTP uses `mailmaster.uni-wuerzburg.de:465` with certificate-verified TLS. The SMTP username is your JMU account username, which may differ from your email address. `configure --smtp-user YOUR_USERNAME` updates it. `smtp-check` checks the connection without logging in or sending.

1. Open the email link on the same Mac running the server. Localhost links do not work from another device.
2. Answer some options and save. The **Response saved** receipt appears only after the SQLite transaction commits. A demo response explicitly says that nothing was saved.
3. Reopen the personal link to see your saved answers. Open the organizer dashboard and click **Refresh responses**, or run `python3 scripts/seminar.py status` for a private snapshot.
4. To test reconfirmation, mark one option **If necessary**, answer the rest, then choose **Ask to reconfirm** in the dashboard. Run `python3 scripts/seminar.py test-mail check`. Reopen the personal link and change the highlighted option to **Available** if you can commit. Choosing Unavailable or clearing that option cancels the proposal.
5. Refresh the dashboard and choose **Finalize this time**. This closes the poll and prevents later edits to the selected seminar or roster. It sends no email by itself.
6. Run `python3 scripts/seminar.py test-mail confirm`. The final email contains the selected time and an `.ics` attachment. The participant page also offers the confirmed calendar download.

Finalization is intentional and closes this campaign. Before testing that last step, finish any response-editing checks. To start a separate seminar after finalization, stop the server, move `.private/invitations.json` and `.private/responses.sqlite3` together into a private archive directory, change `campaign_id` and the dates, run `prepare`, restart the server, and run `sync`. Retain `.private/settings.json` for the sender settings.

## Coordinate the group

Edit `site/config.json` with the real title, location, timezone, response deadline, and a shortlist of candidate times. Each option must have a unique ID, an explicit UTC offset, and the same duration. Set `required_participants` to **20** for this group. The deadline is a requested reply date; late answers remain possible until finalization.

The private `.private/participants.csv` uses columns `name,email`. Every listed person is required. An old `slot_id` column is ignored because the whole group now answers the same poll. Set `test_mode` to `false` only when you have replaced the sample dates and are ready to use the full roster.

After editing the roster or poll:

```sh
python3 scripts/seminar.py prepare
# Restart scripts/server.py after config changes.
python3 scripts/seminar.py sync
python3 scripts/seminar.py admin
```

`prepare` keeps existing personal links and delivery history. A change to poll details resets availability on `sync`, so everyone answers the revised options explicitly. Previous invitations are archived under `.private/history/`; SQLite retains the old response records in its history table. Old email links load the current poll. A submission from a stale, already-open form is rejected. Removing a participant and syncing revokes that person's link. Unchanged polls preserve saved answers.

If no common time works, review the private notes, add or replace candidate times, and prepare and sync another round. The dashboard will never select a majority winner automatically. Before finalization, the backend checks current responses again; a changed response invalidates a stale dashboard action.

Email is controlled from Terminal. These commands create previews; append `--send` to deliver:

```sh
python3 scripts/seminar.py send invite --all
python3 scripts/seminar.py send remind --all
python3 scripts/seminar.py send check --all
python3 scripts/seminar.py send confirm --all
```

Invitations go to people who have not completed the current poll. Reminders go only to invited people with unfinished answers, once per poll revision. After **Ask to reconfirm**, `check` contacts only people who marked that candidate If necessary. After finalization, `confirm` sends the chosen time and calendar attachment to every registered participant. The sender checks saved responses again after the password prompt and before each message. Saving answers or clicking a dashboard button never sends email automatically.

Repeated commands skip messages already sent or attempted. An SMTP timeout can occur after the mail server has accepted a message; the sender records the attempt before sending and does not retry automatically. Check the inbox before removing that specific delivery entry from `.private/invitations.json` to retry.

## Private data

The server binds to `127.0.0.1` and serves an explicit list of website files and API endpoints. It does not expose the repository or `.private/`. Availability persists in `.private/responses.sqlite3` across restarts. Back up `.private/` privately; it also contains personal invitation tokens, the organizer key, and the delivery ledger. Git ignores these files, and website deployment uploads only `site/`.

Personal links contain random 256-bit tokens. SQLite stores their hashes. Possession of a link allows its holder to view and update that participant's answers before finalization. The participant API returns only their own answers and seminar details. Organizer actions and the full response list require a separate secret. Requests are not logged or cached; opening a link never submits a response.

## GitHub Pages

The **Test and publish seminar site** workflow tests pushes to `main`. Publishing is manual through **Actions → Test and publish seminar site → Run workflow**, after enabling GitHub Actions in [Pages settings](https://github.com/d3group/d3seminar/settings/pages). The intended address is `https://d3group.github.io/d3seminar/`.

GitHub Pages can display the public preview. The local backend cannot collect responses from people on the internet. Before sending invitations to the real group, deploy a backend reachable over HTTPS and configure the site and API for it, or reconnect a managed backend. The development server currently accepts API requests only from its own local origin.

## Checks

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
node --check site/app.js
node --check site/admin.js
```

Tests cover persistent and partial answers, unknown values, 20-person scheduling, conditional reconfirmation, incomplete rosters, stale dashboard actions, finalization, migration history, access controls, email targeting, calendar attachments, and duplicate-send protection. HTTP tests use a temporary loopback port.

With Playwright available, run `node tests/browser.mjs`, or set `PLAYWRIGHT_MODULE` to an existing installation. The browser checks use an isolated database and test desktop and mobile layouts, saving screenshots under ignored `test-results/`. They do not alter your real poll or send email.

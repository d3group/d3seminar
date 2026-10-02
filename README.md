# D3 seminar

A seminar invitation MVP with a static website, a local Python backend, SQLite response storage, and personalized emails sent through JMU’s SMTP server. Python 3.10+ is sufficient to run it; there are no runtime packages to install.

The local version covers individual invitations, confirmation and change requests, saved responses, one reminder for nonresponders, and final confirmation emails with calendar attachments. Emails are sent when the organizer runs a command. Saving a response does not automatically send mail.

## Run locally

For a fresh checkout, initialize the private participant list and sender settings:

```sh
python3 scripts/seminar.py init --email YOUR_UNIVERSITY_EMAIL --name 'Your name' --smtp-user YOUR_JMU_USERNAME
python3 scripts/server.py
```

Open **http://127.0.0.1:8765/**. Keep the server running. In another terminal:

```sh
python3 scripts/seminar.py sync
python3 scripts/seminar.py smtp-check
python3 scripts/seminar.py send invite --to YOUR_UNIVERSITY_EMAIL
```

The `send` command creates a preview in `.private/outbox/`; it sends nothing without `--send`. Open the `.eml` file to review the invitation. In test mode only the address used during initialization can receive mail. The sample date is explicitly a test date.

## Test with your university mailbox

Run this command, or double-click `scripts/test-mail.command` on macOS:

```sh
python3 scripts/seminar.py test-mail invite
```

Enter your university password in the terminal prompt. The sender does not display or store it. The configured SMTP service is `mailmaster.uni-wuerzburg.de:465` with certificate-verified TLS, as documented by [JMU IT](https://www.rz.uni-wuerzburg.de/dienste/kommunikation/e-mail/serverdaten/). JMU’s SMTP username is the JMU account username, which may differ from the email address. Use `configure --smtp-user YOUR_USERNAME` to update it.

1. Check that the test invitation arrives in your university inbox. SMTP acceptance means the server accepted the message; check your inbox or spam folder to verify delivery.
2. Before responding, use `python3 scripts/seminar.py test-mail remind` to test the reminder.
3. Open the invitation link **on the same Mac running the server**. Localhost links will not work from your phone or another person’s computer.
4. Select **I need another time**, suggest an alternative, and submit. A receipt appears after the SQLite transaction commits.
5. Run `python3 scripts/seminar.py status` to see the saved response. Reopen the email link and check that the previous response is prefilled.
6. Change the response to **Yes, this works**, then run `python3 scripts/seminar.py test-mail confirm`. Check the confirmation email and its `.ics` attachment.

Repeated commands skip messages already sent or attempted. If the network fails during SMTP delivery, the sender records an `attempted` entry and does not retry automatically. Check the inbox before removing that entry from `.private/invitations.json` to retry. This avoids duplicate emails after an ambiguous timeout.

## Manage participants and dates

Public seminar details and available slots are in `site/config.json`. The local server overrides its website and backend URLs with localhost settings; it never changes the public configuration to contain participant data. Restart the server after editing the public configuration.

The private CSV at `.private/participants.csv` has columns `name,email,slot_id`. The slot ID must match an entry in the public configuration. After changing the CSV or seminar details:

```sh
python3 scripts/seminar.py prepare
python3 scripts/seminar.py sync
python3 scripts/seminar.py status
```

Changing a slot’s details rotates its invitation tokens and clears the associated delivery history. Synchronizing then invalidates the old links and resets those responses, so participants confirm the new details. Removing a participant from the CSV and synchronizing revokes that person’s invitation. Synchronizing an unchanged invitation preserves its response.

For a real seminar, set `test_mode` to `false` only after testing and replacing the sample details. `send invite --all` previews the individual emails. Add `--send` to deliver. `send remind --all --send` sends one reminder to invited people who are still pending. `send confirm --all --send` sends final details to people who confirmed. The sender checks live responses before starting each batch; a participant can still update a response while the batch is being delivered.

## Private data and access

The server binds to `127.0.0.1` and serves only the four website files plus its API. It does not expose the repository or `.private/`. Responses persist in `.private/responses.sqlite3` across server restarts. Back up `.private/` privately; it also contains invitation tokens and the delivery ledger. The files are excluded from Git, and the website deployment uploads only `site/`.

Each invitation uses a random 256-bit token. SQLite stores its hash. The browser sends the token to the local API, which authorizes access to only that invitation. Anyone with the full invitation link can view and update its response. Links use URL fragments, and requests and responses are not cached or logged. Looking at an email link or loading the site never confirms attendance.

## GitHub Pages

The **Test and publish seminar site** workflow tests pushes to `main`. Publishing is manual through **Actions → Test and publish seminar site → Run workflow**, after enabling **GitHub Actions** as the source in [Pages settings](https://github.com/d3group/d3seminar/settings/pages). The intended address is `https://d3group.github.io/d3seminar/`.

GitHub Pages can display the public preview now. The local backend cannot collect responses from other people on the internet. Before sending real invitations, deploy a backend reachable over HTTPS and configure the website and API access for it, or reconnect a managed service such as Apps Script. Do not expose this development server directly to the internet. The current local implementation deliberately accepts API requests only from its own origin.

## Checks

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
node --check site/app.js
```

The automated tests cover persistent responses, invalid and revoked links, revision checks, atomic synchronization, access controls, private file isolation, email previews, recipient restrictions, calendar attachments, and duplicate-send protection. HTTP tests open a temporary loopback port.

For the browser checks, install Playwright in a development environment and run `node tests/browser.mjs`. Alternatively, set `PLAYWRIGHT_MODULE` to an existing Playwright module path. This starts an isolated local database, tests the form at desktop and mobile sizes, and saves screenshots under the ignored `test-results/` directory. It does not use your real invitation or send email.

# D3 Seminars and Onsite

A website for organizing three D3 research seminars and an onsite meeting. Participants confirm availability, propose talks and share presentation materials.

The project has three main Docker services:

- **Frontend:** Displays the programme, availability calendars and agenda.
- **Backend:** Manages events and saves responses, talk proposals and materials.
- **Email:** Sends personalized messages through university email.

Email types:

- **Availability invitation:** Asks everyone to confirm availability for the next seminar.
- **Reminder:** Follows up with people who have not finished responding.
- **Presentation request:** Talk proposal for the next seminar or a provisional title for a later seminar.
- **Date confirmation:** Announces the finalized time and includes a calendar invitation.

[Setup guide](SETUP.md)

Licensed under the [GNU General Public License v3.0](LICENSE).

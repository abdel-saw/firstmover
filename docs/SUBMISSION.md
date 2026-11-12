# FirstMover Devpost submission

## Project name

FirstMover

## Tagline

Help small merchants fill group offers with PayPal commitments and optional early-buyer bonuses.

## Project description

Workshops, group classes and other small events often need a minimum turnout to run. A customer may want to join and still hesitate to commit before others do. FirstMover gives merchants a way to publish a conditional group offer. The merchant can fund a small bonus for eligible early buyers if the offer falls short.

A merchant creates a private workspace, connects their own PayPal Sandbox app and describes an offer in plain English. An AI model served through the Astropods AI Gateway drafts editable terms, flags assumptions and proposes values for the price, minimum group size, decision window and optional early bonus. The merchant reviews the terms and maximum bonus liability before publishing. Buyers use a public link, accept the published terms and approve a temporary authorization through PayPal Sandbox.

When the group fills, the merchant approves settlement and FirstMover verifies the resulting captures. If the deadline passes below the target, the merchant settles the campaign, FirstMover verifies the release of the authorizations, and eligible early buyers receive any promised merchant-funded bonus through PayPal Payouts. The dashboard records provider outcomes in a settlement ledger and surfaces unresolved operations for reconciliation.

The project adapts the idea of dominant assurance contracts to a merchant workflow with AI-assisted drafting and PayPal Sandbox settlement. Judges can run both settlement outcomes locally without API keys and connect their own sandbox and model accounts to exercise the integrations.

## Built with

- **PayPal Sandbox** — Orders v2 creates the checkout and authorizes commitments. Payments v2 handles capture, void and recovery refunds. Payouts sends and verifies the early bonuses.
- **Astropods AI Gateway** — Runs the model behind Terms Studio. The worker turns a merchant brief into a bounded campaign proposal with rationale and questions. FirstMover validates the output before the merchant can review it.
- **Render** — Hosts the app. The deployment supports PostgreSQL for persistent merchant workspaces, campaign states and settlement records.
- **React, Vite, FastAPI and SQLAlchemy** — Implement the public campaign pages, merchant dashboard, API and persistence.
- **OpenAI Codex** — Supported development, testing and documentation.

The demo uses Claude Haiku 4.5 (`claude-haiku-4-5`) for campaign proposals.

## Links and testing instructions

- [Hosted demo](https://firstmover-dn0t.onrender.com/)
- [Merchant dashboard](https://firstmover-dn0t.onrender.com/organizer)
- [Public GitHub repository](https://github.com/abdel-saw/firstmover) and [MIT license](../LICENSE)
- [Judge quickstart and independent deployment](DEPLOYMENT.md)
- [FirstMover demo video on YouTube](https://youtu.be/yU365gwfXNg)

Open the hosted dashboard to create a merchant workspace. For an account-independent walkthrough, clone the repository and follow the local simulation steps in the judge quickstart. For live integration testing on your own deployment, the same guide covers creating PayPal Sandbox accounts, connecting a merchant app and enabling an AI provider.

## Final submission checklist

- Confirm the public GitHub repository and MIT license are visible in a signed-out browser.
- Confirm the hosted homepage and `/api/health`. If you record a live AI proposal or sandbox settlement, verify the model label and provider results before making those claims.
- Record the desktop browser walkthrough using the [timed script](DEMO_SCRIPT.md). Check the exported runtime is below three minutes.
- Upload the video to YouTube as **Public**. Open the link while signed out to verify it.
- Paste the description, tools, hosted URL, public repository and YouTube URL into Devpost. Include the judge quickstart link in the testing instructions.
- Submit before **November 12, 2026 at 21:00 Morocco time**. The official deadline is noon Pacific Standard Time. The judging period runs through December 15. Check whether your Render database and Astropods access cover the judging period.

[Official Devpost rules](https://paypalaihackathon.devpost.com/rules)

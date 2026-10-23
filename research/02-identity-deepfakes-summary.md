# Theme 02: Identity & deepfakes (summary)

## The pick

**When a call asks for money, the real person signs the exact transaction on their own device.** A cloned voice or face cannot sign.

- **Problem:** the fake bank advisor call. EUR 516m lost to manipulation fraud in France in 2025, +34 %. The EU now makes the bank refund these victims, so the bank carries the loss and has a reason to buy.
- **Flow:** the AI (Mistral) listens to the call and scores the risk; above a threshold the real client's phone shows the exact transfer and asks for a passkey signature (Face ID). Clone: red. Real client: green.
- **Personal data:** the biometric stays on the phone; the bank stores scores, not voiceprints.
- **Blockchain, optional:** a public key revocation registry (EAS on Base Sepolia), nothing personal on chain.

## What not to say

- Not "we detect deepfakes": open voice detectors reach about 30 % equal error rate on recent fakes. A signature cannot fail on stage the way a detector can.
- Not "X % of these calls use a cloned voice": no public figure exists. Say voice cloning makes the scam cheaper and more convincing.

## Jury and times

- Grading: usage value 25 %, technical solidity 25 %, innovation 20 %, demo 15 %, pitch 15 %.
- Pitch: 7 min + 8 min questions. They want the problem, for whom, the solution, what the AI concretely brings, the demo.
- Checkpoint today 18:00, progress point 20:00, code freeze Sat 09:15, demos Sat 10:00, finals 11:00.

## Figures for the slide

1. EUR 516m lost to manipulation fraud in France in 2025, +34 % (Banque de France, 7 Sep 2026).
2. The bank now refunds in full when a fraudster poses as its employee (EU payments deal, 27 Nov 2025).
3. 88 % of scam losses refunded in the UK's first year of mandatory reimbursement: what EU banks will face.

Full research (the eight branches, other candidate projects, all figures and sources): [02-identity-deepfakes.md](02-identity-deepfakes.md)

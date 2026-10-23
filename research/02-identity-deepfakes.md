# Theme 02: Identity & deepfakes (research)

Organisers' wording (deck in #liens): "How do you verify an identity and fight voice fraud?"

Branches done: 8/8 (last update 14:04)

## Recommendation

All eight branches converge on one product: **when a call asks for money, the real person signs the exact transaction on their own device.** A deepfake can imitate a voice or a face, it cannot sign. Detection is only one input to the risk score, never the verdict.

- **Story (branch 7):** the fake bank advisor call. EUR 516m lost to manipulation fraud in France in 2025, and the EU now makes the bank refund when a fraudster poses as its employee: the bank pays, so it buys.
- **Flow:** a call asks for a transfer; the AI listens (transcription, extraction of amount, IBAN, urgency, secrecy cues) and scores the risk; above a threshold the advisor presses "Verify"; the real client's phone shows the exact transfer in plain words and asks for a passkey signature whose challenge is the transaction's hash (Face ID). Cloned voice: cannot sign, red. Real client: green.
- **What the AI brings (the deck asks it):** live understanding of the call (Mistral, credits provided) that triggers the check and explains the risk in one sentence; an anti-spoofing score as one signal among others.
- **Personal data:** the biometric never leaves the phone (passkey), the bank stores scores, not voiceprints (CNIL line).
- **Blockchain (Marius's idea), optional:** a public revocation and attestation registry for the keys (EAS on Base Sepolia), so a stolen phone is revoked everywhere at once. Nothing personal on chain.
- **Variants on the same build:** Not Me (branch 8, a deepfake CFO on a video call, the Arup case) and Prove it's you (branch 5, a ZK proof from the ID chip instead of a passkey).

## What the jury grades (organisers' deck)

| Criterion | Weight |
|---|---|
| Usage value | 25 % |
| Technical solidity | 25 % |
| Innovation | 20 % |
| Demo quality | 15 % |
| Pitch clarity | 15 % |

- Jury: 5 finance professionals, AI experts (Finaxys clients and tech partners).
- Deliverables: a demoable prototype, a 7 min pitch (+ 8 min questions), a summary of the problem and the choices, a link to the code or the demo.
- They want answered: the problem, for whom, the solution, what the AI concretely brings, the demo.
- Times: checkpoint today 18:00, progress point 20:00, code freeze Sat 09:15, demos Sat 10:00, finals 11:00.
- Provided: Mistral credits (10 EUR per person), Finaxys coaches.

## Synthesis

- **Do not pitch "we detect deepfakes".** Open voice detectors reach about 30 % equal error rate on recent fakes (branch 3); watermarks and C2PA credentials get stripped or forged (branch 2). Cryptographic "prove the real" needs no model to train and cannot fail on stage the way a detector can (branch 6). Past winners did the same: one named victim, one fraud reenacted live, a defence that still works when the deepfake is perfect (branch 8).
- **Blockchain:** a chain cannot prove a person exists, it binds a key; enrollment (KYC, later the EU wallet) does the real work, and the EDPB's July 2026 guidelines say no personal data on chain, not even a salted hash. It earns its place only as a shared, timestamped revocation and attestation registry (branch 1). At ETHGlobal, chain-based deepfake projects won sponsor tracks, not grand prizes (branch 8).
- **Constraints a finance jury will check (branch 7):** voice and face templates are sensitive data (GDPR art. 9), the CNIL wants them on the user's device with a non-biometric alternative. AI Act deepfake labelling applies since 2 Aug 2026; checking a person against their own claimed identity and fraud detection are not high-risk uses.
- **No public figure** says how many fake advisor calls use a cloned voice: say "voice cloning makes it cheaper and more convincing", not "X % are deepfakes".

## Candidate projects

| Project | Branch | Demo moment | Main risk |
|---|---|---|---|
| CallProof | 6 | Advisor presses "Verify", the clone cannot sign the EUR 48,000 transfer, the real client signs with Face ID | ABN AMRO and ASB ship a plain version: the edge is the transaction binding, the two-way check, the audit trail |
| CallGuard / CallShield | 3, 8 | Live risk score in the advisor console; a consenting teammate cloned with ElevenLabs passes the voiceprint, the transfer is still blocked | Loudspeaker audio fools the detector: record the demo setup in the first hours to set thresholds |
| Not Me / Signed or it didn't happen | 8, 1 | A face-swapped "CFO" on a real Meet call asks for a wire; nobody signs: red; replay with the real CFO approving: green | Live face swap setup on stage; keep a recorded fallback |
| Prove it's you | 5 | The real person taps their passport on a phone, a ZK proof turns green, the bank gets no personal data | zkPassport has no mock mode (a real passport on stage); can the proof be bound to one transaction? check in hour one |
| LiveGate / LiveCheck | 4, 8 | A printed photo, a phone replay and a live face swap through OBS Virtual Camera are each caught, the layer that fired is named | Demo lighting causes false rejects |
| SignedCapture KYC | 2 | A generated ID photo and a one-pixel edit turn red, a real capture is green | A virtual camera injects before the signing step |

## Figures for the pitch slide (branch 7, sources in the branch file)

1. EUR 516m lost to manipulation fraud in France in 2025, +34 %, 41.6 % of payment fraud; the fake bank advisor is its most frequent form (Banque de France, 7 Sep 2026).
2. The bank now refunds in full when a fraudster poses as its employee (EU payments deal, 27 Nov 2025; final adoption unconfirmed).
3. 88 % (GBP 173m) of scam losses refunded in the UK's first year of mandatory reimbursement: what EU banks will face.
4. 1 in 5 biometric fraud attempts is a deepfake; injection attacks +40 % in 2025 (Entrust, Nov 2025).
5. Deepfakes went from 0.1 % to 6.5 % of fraud attempts in European finance in three years; only 22 % of institutions use AI-based prevention (Signicat, Feb 2025).

## Branches

| # | Branch | Verdict in one line |
|---|---|---|
| 1 | [Blockchain and proof of personhood](branches/01-blockchain-personhood.md) | Chain only as a shared revocation and attestation registry, nothing personal on it |
| 2 | [Content provenance and watermarking](branches/02-provenance-watermarking.md) | Easiest to build (c2pa-python, AudioSeal), but proves the genuine only, cannot catch a fake |
| 3 | [Voice deepfake fraud and detection](branches/03-voice-deepfakes.md) | Detection alone is weak: layered live risk score with a challenge and out-of-band confirmation |
| 4 | [Remote KYC, liveness, injection attacks](branches/04-kyc-liveness.md) | Layered browser liveness check, pitched on KYC fraud cost and compliance (ETSI TS 119 461, ANSSI PVID) |
| 5 | [Zero-knowledge identity from real ID documents](branches/05-zk-identity.md) | ZK proof from the ID chip: the state's signature no AI can forge, no personal data to the bank |
| 6 | [Verification protocols for humans and agents](branches/06-verification-protocols.md) | "Prove the real" with a transaction-bound passkey signature: no model, cannot fail on stage |
| 7 | [Regulation, market figures, what the jury wants](branches/07-regulation-market.md) | Pitch the fake bank advisor call: EUR 516m in France, and the bank pays |
| 8 | [Past hackathon winners and 24 h project ideas](branches/08-winners-ideas.md) | Not Me: one-tap proof against a deepfake CFO on a video call; winners reenact one fraud live |
| P | [The problem: manipulation fraud, Banque de France +34 %](problem-manipulation-fraud.md) | The fake advisor plays the bank and the real client signs in his own app: EUR 516m, +34 %, and the bank increasingly pays |

Short version: [02-identity-deepfakes-summary.md](02-identity-deepfakes-summary.md)

# Past winners and 24 h project ideas

Scouted 2026-10-23 with 12 web searches. Dates are the event dates unless stated.

## What wins

- **Bank scenario plus a factual check, not a detector alone.** JBSwissHacks (Julius Baer, Zurich, 2024): the challenge was to identify deepfake calls and verify the client's identity "based on factual content"; the jury picked FraudFence by team "Les Détectives", an audio detection and verification tool for the bank. Source: https://www.juliusbaer.com/en/insights/company-insights/behind-the-scenes/jbswisshacks-2024-making-it-to-show-whos-faking-it/ (2024; the page refused my fetch with 403, details come from the search index, exact date unverified).
- **A live phone call on stage, a human victim, real APIs.** HughKnew won 1st place at Clawckathon (Resemble AI, Telnyx, Lovable, 20-22 March 2026): it calls family members with a cloned relative's voice to train them against voice scams; the write-up credits that "every feature runs live against real APIs". Source: https://www.resemble.ai/resources/could-you-tell-your-grandsons-voice-from-a-clone-of-it-hughknew-was-built-to-find-out-safely (March 2026).
- **Blockchain wins when it binds a verified human to content, and it wins sponsor tracks, not the grand prize.** DeepTruth (ETHGlobal Taipei, April 2025) embeds hashed identity markers (World ID proof of personhood, Self passport check, soulbound ENS subname) into video frames; it took World "Best Mini App" 3rd and ENS 2nd. Source: https://ethglobal.com/showcase/deeptruth-nx3h1 (the page shows no year; April 2025 is the Taipei date given in https://wise-token.notion.site/ETHGlobal-Taipei-2025-1f073aed339180f4a992c82e8ab5159a).
- **Sign at capture rather than detect afterwards.** Authenticity Witness (ETHGlobal Bangkok) hashes a photo at capture with a perceptual (wavelet) hash, stores it on IPFS, attests hash plus metadata on Polygon through Sign Protocol, gates users with World ID; won World, Blockscout and Sign Protocol prizes. Source: https://ethglobal.com/showcase/authenticity-witness-6ithr (November 2024 is unverified: the page shows no year).
- **French bank juries reward one concrete banking use case, and peer-learning schools win.** LCL x Google hackathon (36 h, AI for banking): the grand jury prize went to ESILV and to École 42 (an Epitech-like school), on predicting customers' financial behaviour. Source: https://www.esilv.fr/le-hackathon-lcl-x-google-une-equipe-esilv-remporte-la-competition-avec-une-solution-dia-pour-le-secteur-bancaire/ (page updated September 2024; event date unverified).
- **A risk score built from several layers beats one binary "fake" verdict.** Aurigin.ai took 2nd of 100+ projects at the Swiss Innovation Challenge 2025 with real-time detection, voice liveness and multi-layered authenticity scoring (a startup contest, not a hackathon). Source: https://ghost.dev.aurigin.ai/aurigin-ai-wins-2nd-place-at-the-swiss-innovation-challenge-2025/ (2025).

What they have in common: one named victim, one fraud they can reenact live, and a defence that still works when the deepfake is perfect (a factual check, a signature at the source, a second channel).
Not found: any past Finaxys hackathon or its winners, and any 2025-2026 deepfake hackathon winner at BNP Paribas, Société Générale, BPCE or Crédit Agricole. Any claim about those is unverified.

Context the jury will recognise:
- **Arup:** a finance employee in Hong Kong paid about USD 25 M after a video call where every other participant (the CFO included) was a deepfake (Hong Kong police, early 2024): https://incidentdatabase.ai/es/reports/3622/
- **Banque de France:** it warned about deepfake videos of its governors used to push fraudulent transfers (incident dated 2026-04-27): https://oecd.ai/en/incidents/2026-04-27-e5fe
- **Hong Kong account openings:** 30 of 44 attempts to open bank accounts with deepfaked ID photos succeeded (SCMP, 2025, date unverified): https://scmp.com/news/hong-kong/law-and-crime/article/3308091/hong-kong-banks-urged-use-government-database-combat-deepfake-applications
- **HKMA:** on 2025-08-25 it added "deepfake detection" to banks' e-banking security measures: https://brdr.hkma.gov.hk/chi/doc-ldg/docId/getPdf/20250825-1-EN/20250825-1-EN.pdf

## Five project ideas

Free audio deepfake detectors to start from (accuracy on phone-quality audio unverified): https://huggingface.co/nii-yamagishilab/wav2vec-large-anti-deepfake-nda (a front-end to fine-tune), the DeepFense checkpoints (455+ Wav2Vec2/WavLM + AASIST models) https://deepfense.readthedocs.io/en/latest/07_huggingface_hub/, and https://huggingface.co/lab260/AASIST3.

### 1. CallShield: a live copilot for the bank advisor's phone line (audio)
- **Pitch:** while a "client" talks to the advisor, a side panel scores the voice, the script and the request, and triggers a confirmation in the client's banking app before anything moves.
- **Persona and fraud:** Claire, an advisor at a retail branch. A fraudster has cloned a client's voice from his Instagram videos and calls to raise the transfer limit and add a new IBAN "urgently, I'm abroad".
- **Live demo:** a teammate clones his own voice in 30 s on stage, then calls the "bank". The panel goes from green to red: synthetic-voice score up, Whisper transcript flags urgency, secrecy and a new beneficiary, and the panel pushes "Are you calling your bank right now?" to the real client's phone, which says no.
- **Stack:** faster-whisper (ASR), a DeepFense or NII anti-deepfake checkpoint (voice score), a local LLM through Ollama (Mistral or Llama) to tag fraud-script cues, OpenVoice v2 or XTTS-v2 for the demo clone, FastAPI with WebSocket streaming, a small React panel, a PWA standing in for the client's banking app.
- **Wow:** three signals merged into one risk bar that moves while the person is still talking, and the hard decision rests on the out-of-band confirmation, not on the detector.
- **Main risk:** detectors miss a fresh clone played through a phone line, or flag the real voice. Present the voice score as one soft signal and keep a recorded call as a fallback.

### 2. Not Me: one-tap proof for "fraude au président" in video calls (multimodal, the pick)
- **Pitch:** when "the CFO" on a video call asks for an urgent transfer, the employee clicks Verify; the real CFO's phone must sign the request with its passkey, and a deepfake cannot.
- **Persona and fraud:** Karim, treasury officer at an asset manager. An Arup-style call: a live face-swapped "CFO" and a cloned voice ask for a confidential EUR 2 M wire to close an acquisition today.
- **Live demo:** a teammate joins a real Meet or Teams call wearing another teammate's face (Deep-Live-Cam through an OBS virtual camera). The jury sees the convincing fake. Karim clicks Verify: the request (amount, IBAN, reason) goes to the enrolled CFO's phone, nobody signs, and it turns red in 20 s. Replay with the real CFO: Face ID on his phone, green, the signed request shows exactly what he approved.
- **Stack:** WebAuthn passkeys (SimpleWebAuthn, Next.js), a PWA on the CFO's phone, a side web app next to the call (no call plugin needed), optional passive layer: Whisper plus a local LLM flagging the CEO-fraud script, the audio score from idea 1.
- **Wow:** the deepfake can be perfect and still fail, because the check is cryptographic and out of band. Nothing biometric leaves the CFO's phone (the passkey check stays on the device), which covers the personal-data part of the theme.
- **Main risk:** Deep-Live-Cam needs a decent GPU and setup time; tune it on day 1, record a backup video of the swap, and swap only teammates' faces (consent). The jury may say "just call back"; answer: a callback uses a number the fraudster may have supplied, a passkey is bound to the device enrolled at onboarding.

### 3. Origin: an on-chain registry of what a bank or asset manager really published (blockchain where it adds something)
- **Pitch:** every official video, voice note and PDF is fingerprinted and signed at publication; anyone forwards a suspicious clip to a bot and learns in seconds whether it came from the institution.
- **Persona and fraud:** Martine, 67, a retail investor. A WhatsApp video of a "Banque de France governor" (or an asset manager's star fund manager) promotes a guaranteed fund with an IBAN.
- **Live demo:** the issuer console publishes a real video (hash attested on chain). The jury forwards two clips to a Telegram bot from a phone: the official one re-encoded by WhatsApp says "Issued by X on <date>, attestation <link>", the deepfake says "No match, nearest official video: <link>".
- **Stack:** perceptual hashes (Python imagehash on keyframes, Chromaprint for audio), Ethereum Attestation Service or Sign Protocol on a free testnet (Base Sepolia or Polygon Amoy), C2PA manifest via the open-source c2patool, python-telegram-bot.
- **Why a chain here:** a public, timestamped, append-only registry that no party (neither the issuer nor us) can quietly rewrite, verifiable by anyone even when the issuer's website is spoofed, with revocation. What it does not do: prove that a person exists (tell the teammate who suggested it: a chain stores the claims it is fed).
- **Wow:** a re-encoded, cropped WhatsApp copy still matches the original; the fake has no attestation to point to.
- **Main risk:** perceptual hashes break on heavy crops or overlays, and the jury may ask why a signature on the issuer's website is not enough. Have that answer in the pitch and test the hash on real WhatsApp re-encodes on day 1.

### 4. KYC Once: prove "verified, adult, French resident" without handing over your passport (protects personal data)
- **Pitch:** the bank verifies a person once, then issues a reusable credential; brokers and fintechs check only the facts they need, so no ID scan sits in ten databases ready to feed the next deepfake.
- **Persona and fraud:** Léa opens accounts at a broker and a crypto platform. Each onboarding is a new chance for a deepfake KYC (the Hong Kong 30-of-44 case), and each copy of her passport is a future leak.
- **Live demo:** Léa scans a QR code at the "broker"; her wallet shows the request (KYC done, over 18, resident in France), she approves, and the verifier screen shows three ticks and no name, no photo, no document number. A teammate then replays the credential from another phone: refused, because it is bound to Léa's device key. The bank revokes it: refused everywhere.
- **Stack:** SD-JWT verifiable credentials over OpenID4VCI and OpenID4VP with an open-source library (walt.id or the OpenWallet Foundation libraries; which is fastest to set up is unverified), a PWA wallet, a status list for revocation. Pitch it next to the EU Digital Identity Wallet (eIDAS 2.0; the end-of-2026 deadline is unverified here).
- **Wow:** the verifier ends with less data than today and more assurance.
- **Main risk:** standards plumbing eats the 20 h and the demo looks like a QR login. Use a library's ready-made issuer and verifier, and spend the saved time on the replay and revocation moment.

### 5. LiveCheck: a liveness challenge a face swap cannot fake in time (multimodal KYC)
- **Pitch:** remote onboarding asks for random, unpredictable actions (read 4 random digits aloud, turn your head to the left) and cross-checks lips, voice and camera source.
- **Persona and fraud:** a neobank's onboarding team. An attacker injects a Deep-Live-Cam face swap through a virtual camera to open mule accounts.
- **Live demo:** an attack run and a genuine run side by side. The attacker's OBS virtual camera is flagged, lip movement (MediaPipe face mesh) does not line up with the audio energy of the spoken digits, Whisper hears the wrong digits or a synthetic voice; the real user passes in 10 s.
- **Stack:** browser getUserMedia plus MediaPipe Face Landmarker, Whisper for the spoken digits, a DeepFense audio checkpoint, device-label and frame-timing heuristics for virtual cameras, FastAPI.
- **Wow:** the jury watches a real-time deepfake fail a challenge it could not have prepared for.
- **Main risk:** heuristics a jury can poke holes in (an attacker renames the virtual camera), and lip-sync scoring is noisy. Frame it as raising the attacker's cost, not as proof.

## Verdict

Pick idea 2, Not Me: CEO fraud is the deepfake attack a finance jury already fears (Arup), the live face swap beaten by one tap is the clearest demo of the five, and it does not depend on winning the detection arms race. It fits 4 developers in 20 h (one on the deepfake setup, one on passkeys and the phone app, one on the verify panel, one on the passive audio and script signals plus the pitch), and the passkey keeps biometrics on the device. Ideas 1 and 3 can donate a passive signal or a "signed approvals log" slide if time allows.

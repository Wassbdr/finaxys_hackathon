# Voice deepfake fraud and detection

## Key facts (6 bullets max, each with a source link and a date)

- **Arup, Hong Kong (Jan 2024, reported May 2024):** an employee joined a video call where the "CFO" and several "colleagues" were all deepfakes, then sent HK$200M (about US$25.6M) in 15 transfers. Source: CNN via https://abc17news.com/money/cnn-business-consumer/2024/05/16/british-engineering-giant-arup-revealed-as-25-million-deepfake-scam-victim/
- **Italy, Feb 2025:** a cloned voice of Defence Minister Guido Crosetto (calls from spoofed Rome numbers) asked Moratti, Armani, Tronchetti Provera and others for about EUR 1M to "free kidnapped journalists". Moratti reportedly paid about EUR 1M, later frozen in a Dutch account (amounts and freeze are from secondary sources, unverified). Source (Feb 2025): https://quifinanza.it/attualita/truffa-crosetto/880594/
- **Voice ID at banks is considered broken (22/07/2025):** Sam Altman told a Federal Reserve conference that banks still accepting voiceprints are doing "a crazy thing", "AI has fully defeated that". Source: https://www.pymnts.com/news/2025/sam-altman-says-ai-has-defeated-bank-voice-id-protections/
- **Volume (Pindrop 2025 report, 2024 data, vendor figures):** over 1.2B calls analysed, deepfake activity +680% year over year, deepfake fraud attempts +1,300% (from 1 per month to 7 per day), estimated US$12.5B contact-center fraud losses in 2024. Source (2025): https://cioinfluence.com/security/pindrops-2025-voice-intelligence-security-report-reveals-1300-surge-in-deepfake-fraud/
- **Detection does not generalize to new generators:** on ASVspoof 5 (2024) the AASIST baseline has about 25% pooled EER (https://arxiv.org/pdf/2410.01108, Oct 2024); NII's AntiDeepfake models (2025, trained on 74k hours, 100+ languages) reach 2.25% EER on In-the-Wild but 30% on Deepfake-Eval-2024 (https://huggingface.co/nii-yamagishilab/wav2vec-large-anti-deepfake-nda); a Jan 2026 study finds detectors that work on one TTS architecture "may fail against others, particularly LLM-based synthesis" (https://arxiv.org/abs/2601.20510). A 2026 vendor-run benchmark puts open-source ASVspoof2019-trained models (AASIST, RawNet2, LCNN, Wav2Vec2) at 48% to 63% accuracy vs 98.1% for Resemble (vendor claim, unverified): https://resemble.ai/resources/audio-deepfake-detection-benchmark-results-how-8-systems-performed-in-2026
- **France has no public voice-clone fraud figure:** none found from Banque de France, ACPR or OSMP; the closest is OSMP's total payment fraud of EUR 1.189B over 7.8M transactions in 2024, all channels (Gazette du Palais, 2026: https://www.labase-lextenso.fr/gazette-du-palais/2026-n16/ia-generative-et-falsification-industrialisee-des-moyens-de-paiement-GPL490p6). The FBI's advice (03/12/2024) is non-technical: a family secret word, hang up and call back on a known number (https://www.ic3.gov/PSA/2024/PSA241203).

## What is buildable in 20 h (tools, APIs, models, libraries with links, free tier or not, setup time)

**Detection (the "AI" signal)**

| Tool | What it gives | Cost | Setup |
|---|---|---|---|
| NII AntiDeepfake, Wav2Vec2-Large or MMS-300M variant: https://huggingface.co/nii-yamagishilab/wav2vec-large-anti-deepfake-nda | Local real/fake probability, 16 kHz mono, any length; family goes up to XLS-R-2B | Free, CC BY-NC-SA 4.0 (fine for a hackathon, not for a product) | 1 to 3 h (needs fairseq 0.12.2, which is fussy on recent Python: pin Python 3.10, unverified) |
| XLS-R-SLS checkpoints: https://huggingface.co/sukhdeveyash/XLS-R-SLS-Deepfake-Detection | Same idea, XLS-R 300M + light head (ACM MM 2024 reproduction) | Free, MIT | 1 to 2 h |
| Reality Defender API: https://www.realitydefender.com/api (launch post https://news.ycombinator.com/item?id=44941580, launched July 2025) | Hosted audio/image scoring, SDKs in Python, TS, Go, Java, Rust | 50 free scans per month; streaming needs an email to sales | 30 min; use it as a second opinion on a few clips, not continuously |
| Resemble Detect through Telnyx: https://developers.telnyx.com/docs/voice/programmable-voice/deepfake-detection | Real-time verdict on a live phone call, telephony codecs (G.711, AMR-WB, Opus) | Paid: US$0.01 per call plus a Telnyx number and credit | 2 to 4 h; only if the team wants a real phone line |
| Pindrop Pulse | Contact-center and meeting deepfake detection | Enterprise sales only, no self-serve (unverified) | Not an option in 24 h, but a good "this is what banks buy" slide |

Real-time feasibility: score a sliding 3 to 4 s window every second. A 300M model on such a window should take tens of ms on a GPU and a few hundred ms on a laptop CPU (estimate, unverified: measure it in hour 1).

**Signals around detection (what makes it more than an arms race)**

- Speaker verification: SpeechBrain ECAPA, https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb (free, 30 min). Used to show that a voiceprint *accepts* the clone, then as one signal among others.
- Liveness challenge: the advisor screen asks the caller to repeat a random phrase or numbers; faster-whisper (https://github.com/SYSTRAN/faster-whisper, free, 30 min) checks content and response delay. TTS pipelines add latency and stumble on unexpected prompts (reasonable, unverified as a hard rule).
- Transaction context: an LLM tags urgency, new beneficiary, secrecy requests ("don't tell anyone", "it's for kidnapped journalists") from the transcript.
- Out-of-band step-up: push to the real customer's registered device or a WebAuthn passkey prompt, callback on the number on file. This is what the FBI and banks actually rely on.
- Privacy: process audio in memory, never store raw voice, keep only scores and the embedding needed for verification (GDPR treats voiceprints as biometric data).

**Producing the demo clone (consenting teammate)**

| Tool | Reference audio | French | Cost | Setup |
|---|---|---|---|---|
| ElevenLabs Instant Voice Cloning: https://elevenlabs.io/pricing | About 30 s to a few minutes (sources differ) | Yes | Starter US$6/month, US$1 first month until 01/11 (checked 23/10/2026); not on the free plan | 15 min, best quality, safest bet |
| Coqui XTTS-v2: https://huggingface.co/coqui/XTTS-v2 | 6 s | Yes (17 languages) | Free, Coqui Public Model License (non-commercial) | 1 h on a GPU; streaming about 200 ms to first chunk (third-party claim, unverified) |
| F5-TTS: https://github.com/SWivid/F5-TTS | 3 to 15 s | Base model English/Chinese; French needs a community fine-tune (unverified) | Free, code MIT, weights CC-BY-NC | 1 to 2 h on a GPU |

Record the consent on video at the start of the demo: it is part of the story ("this is Paul, he agreed, here is his clone made in 2 minutes for US$1").

## Project ideas (2 or 3: one-line pitch, the demo moment the jury sees, the stack, the main risk)

**1. CallGuard: a live risk score for the bank advisor's call (recommended)**
- Pitch: a co-pilot in the advisor's console that fuses deepfake score, voiceprint, a liveness challenge and the transaction context into one traffic light and a next step (proceed, challenge, step-up out of band).
- Demo moment: a teammate "calls" the advisor to wire EUR 50k to a new beneficiary. Real voice: green. Then his ElevenLabs clone played from a phone: the voiceprint says "match 0.82" (voice ID is dead, as Altman said), the deepfake gauge climbs, the random challenge "repeat 7 4 1 blue" comes back late and wrong, the console blocks and sends a push to the real customer's phone, who declines.
- Stack: browser mic (getUserMedia) to FastAPI over WebSocket; AntiDeepfake 300M + ECAPA + faster-whisper + an LLM for intent; a small React dashboard with the score timeline. Reality Defender as an optional second opinion.
- Main risk: the detector misreads loudspeaker-replayed or laptop-mic audio (channel shift). Mitigate in the first 4 h: record 10 real and 10 cloned clips per teammate through the exact demo path, calibrate the threshold, and lean on the fusion so the decision never rests on detection alone.

**2. Verify-the-boss: Arup-proof approvals for executive requests**
- Pitch: any urgent payment request heard in a call or meeting gets a one-click "verify" that sends a signed challenge to the real executive's registered device, plus an audio deepfake check of the meeting.
- Demo moment: a fake "CFO" (cloned voice, optional face filter) on a video call asks for an urgent transfer; the employee clicks verify, the real CFO's phone shows the request, he refuses, the dashboard shows the audio score and the red flags in the transcript.
- Stack: Next.js, WebAuthn passkeys, web push or a Telegram bot for the out-of-band channel, the same detection backend as idea 1.
- Main risk: the jury sees a 2FA app with little AI in it. The detection and transcript analysis must stay visible on screen.

**3. Voice red-team kit for banks (fits Finaxys' consulting business)**
- Pitch: an audit tool that clones consenting staff voices with several generators, replays them against the bank's voice-ID and IVR, and produces a scored vulnerability report with remediation.
- Demo moment: a report generated live: "voiceprint accepts 9 of 10 clones from 3 generators; with CallGuard's layered checks, 0 of 10 pass".
- Stack: ElevenLabs + XTTS-v2 + F5-TTS for attacks, ECAPA as the stand-in for a bank voiceprint, the detector, a PDF or HTML report.
- Main risk: no real bank system to attack, so the target is simulated; ethics and consent must be front and center.

## Verdict (2 or 3 lines)

Do not pitch "we detect deepfakes": open models fall to about 30% EER on recent fakes and the jury knows it is an arms race. Pitch idea 1, a layered risk score where detection is one signal next to voiceprint, a liveness challenge, transaction context and an out-of-band step-up; it is demoable live in 24 h with a US$1 ElevenLabs clone of a consenting teammate, and "the voiceprint accepts the clone, CallGuard stops it" is the moment the jury remembers. Idea 3 can be its closing slide for Finaxys.

# Verification protocols for humans and agents

Angle: stop trying to spot the fake, make the real one prove itself with something a voice clone cannot copy (a device-bound key, a channel the bank controls, a signed mandate). Research done 2026-10-23, 12 web searches.

## Key facts (6 bullets max, each with a source link and a date)

- **FBI, 3 Dec 2024 (PSA I-120324-PSA)**: says criminals use generative AI for financial fraud, voice cloning included. It advises families to agree on a secret word or phrase, and to hang up and call back on a number you already know. Source: https://www.ic3.gov/PSA/2024/PSA241203
- **FinCEN, 13 Nov 2024 (FIN-2024-Alert004)**: banks have filed more SARs about deepfake media since 2023, mostly forged IDs, photos and videos used to get past KYC and authentication. Its red flags include a device or location that does not match the documents. SAR keyword: "FIN-2024-DEEPFAKEFRAUD". Source: https://fincen.gov/news/news-releases/fincen-issues-alert-fraud-schemes-involving-deepfake-media-targeting-financial
- **Banks already ship "is it really the bank calling?" checks in their apps**: ABN AMRO Call Check (Sep 2024), ASB Caller Check (NZ, Mar 2025), ING Poland, PKO IKO, HSBC Australia Call Verify (two security words both sides must match). In each one, the advisor triggers a push in the app and the customer confirms. The ING, PKO and HSBC pages carry no date. Sources: https://abnamro.com/en/news/check-check-doublecheck-is-it-really-the-bank-calling , https://asb.co.nz/documents/media-centre/media-releases/asb-launches-caller-check.html , https://www.hsbc.com.au/help/security-centre/call-verify/
- **France, MAN (caller ID authentication, Arcep)**: every French operator now runs it. Arcep counted about 18,000 number-spoofing reports from Jan to Dec 2025 (531 in 2023, per Siècle Digital). Since 1 Jan 2026, a call from abroad that shows an unauthenticated French mobile number must be shown as hidden or as a generic number (Arcep decision of 2 Dec 2025). I did not re-check the start date of the MAN (Oct 2024) in this run. Sources: https://www.arcep.fr/mes-demarches-et-services/acteurs-regules/operateurs-telecoms/fiches-pratiques/authentification-numeros-telephone-fixe-ou-mobile-que-faire-en-tant-que-operateur-telephonique.html , https://siecledigital.fr/2026/02/02/appels-frauduleux-larcep-met-les-operateurs-sous-pression/ (2 Feb 2026). The US equivalent, STIR/SHAKEN, was not re-checked in this run.
- **EU Verification of Payee, mandatory since 9 Oct 2025** (Instant Payments Regulation 2024/886, EPC VOP rulebook): before any euro credit transfer, the bank checks the payee's name against the IBAN and returns match, close match, no match or not possible, while the payer can still cancel. A scam call can still talk the victim past a "no match". Source: https://legal.pwc.de/en/news/articles/verification-of-payee-requirements-vop-under-the-eus-instant-payments-regulation-ipr
- **Agent identity is converging on signatures, not detection**: Mastercard Agent Pay (29 Apr 2025: "Agentic Tokens" bound to a registered agent), Google AP2 (Sep 2025: user-signed "mandates", described as verifiable credentials, Apache-2.0 repo), Visa Trusted Agent Protocol (14 Oct 2025: the agent signs each HTTP request per RFC 9421 with Ed25519, in line with Cloudflare's Web Bot Auth IETF drafts). "Know Your Agent" (KYA) is an industry term, not a named standard (unverified as a formal spec). Sources: https://www.mastercard.com/global/en/news-and-trends/press/2025/april/mastercard-unveils-agent-pay-pioneering-agentic-payments-technology-to-power-commerce-in-the-age-of-ai.html , https://usa.visa.com/about-visa/newsroom/press-releases.releaseId.21716.html , https://blog.cloudflare.com/web-bot-auth

## What is buildable in 20 h (tools, APIs, models, libraries with links, free tier or not, setup time)

| Piece | What it gives the demo | Link | Cost | Setup |
|---|---|---|---|---|
| SimpleWebAuthn (`@simplewebauthn/server` + `/browser`, v14 docs) | Passkey registration and sign-in. `generateAuthenticationOptions({ challenge })` takes a custom challenge, so you can pass `SHA-256(amount, IBAN, payee, nonce)`: the passkey then signs this exact transaction (PSD2 "dynamic linking") | https://simplewebauthn.dev/docs/packages/server | Free, open source (license not read on the page) | 1 to 2 h |
| Secure Payment Confirmation | The browser itself shows the amount and payee and signs them, so a compromised page cannot show one amount and sign another. Chromium only (no Safari or Firefox, per a Jul 2026 vendor blog) | https://developer.chrome.com/docs/payments/authenticate-secure-payment-confirmation | Free | 2 h, optional |
| Node `crypto` (Ed25519) or `jose` (JWS/JWT) | Signed mandates and signed "verified" attestations, no extra dependency | https://nodejs.org/api/crypto.html , https://github.com/panva/jose | Free | 30 min |
| Cloudflare `web-bot-auth` (npm, Rust crate) | RFC 9421 `Signature` / `Signature-Input` / `Signature-Agent` headers for an agent's requests. Public key published as a JWKS under `/.well-known/http-message-signature-directory` | https://developers.cloudflare.com/bots/reference/bot-verification/web-bot-auth/ | Free | 1 h |
| Visa TAP sample | Agent (Streamlit), registry, CDN proxy that checks signatures, merchant (React + FastAPI). 5 local services | https://github.com/visa/trusted-agent-protocol | Free (license file not read) | 1 to 2 h to run, worth reading even if not reused |
| Google AP2 samples | Python/Go/Android scenarios of agent payments with mandates. Python 3.11 + `uv`, needs a Gemini key (`GOOGLE_API_KEY`) | https://github.com/google-agentic-commerce/AP2 | Apache-2.0. Gemini free tier (limits not checked) | 1 to 2 h |
| Push to the "bank app" | A PWA with WebSocket or SSE is enough on stage. ntfy.sh as a no-account push to a real phone (not checked in this run) | https://ntfy.sh | Free | 30 min |
| HTTPS tunnel | WebAuthn needs a secure context. `localhost` works on a laptop, but a phone needs HTTPS (cloudflared / ngrok) | https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/ | Free | 20 min |
| Voice clone for the "attack" | Plays the impostor on stage, using a team member's voice with consent. Open models (XTTS, F5-TTS) or ElevenLabs (cloning tier and price unverified) | https://elevenlabs.io | Free to cheap | 1 h |

Patterns worth copying:
- **Advisor-triggered in-app check** (ABN AMRO, ASB, ING PL): the advisor clicks "verify", the customer's app shows the advisor's name and photo, the customer confirms. Mutual version: the app shows two words both sides read out (HSBC AU).
- **Transaction-bound passkey**: the challenge is a hash of what is being approved, so a voice can talk the victim into "approving" but the signature only covers the exact amount and IBAN shown on the device.
- **Signed agent requests + human-signed mandate**: the agent proves *which agent* it is (RFC 9421 key in a registry), and the mandate proves *what the human allowed* (signed by the human's passkey, scoped by amount, merchant and expiry).
- **Safe word as challenge-response**: the FBI's family safe word, moved into the app as a shared secret the bank never says aloud (the customer types it on their device, never on the phone line).

## Project ideas (2 or 3: one-line pitch, the demo moment the jury sees, the stack, the main risk)

**1. CallProof: a "verify the human" button for bank advisors (best idea)**
- Pitch: during a suspicious call, the advisor (or the customer) presses one button, and the person on the line must prove themselves with a passkey on their own phone, bound to the exact transaction being requested. A deepfake can copy a voice, it cannot copy a device-bound key.
- Demo moment: a cloned voice of "the client" calls the advisor and asks for an urgent €48,000 transfer to a new IBAN. The advisor's console shows VoP "no match" and a risk flag, and the advisor clicks "Verify". The real client's phone buzzes and shows "Transfer €48,000 to X: did you ask for this?". The impostor cannot answer it, and the console turns red with a signed audit record. Replay with the real client: Face ID, green, signature checked live on screen. The reverse direction works too: the customer checks that the "advisor" calling is real.
- Stack: Next.js or Express + SimpleWebAuthn + SQLite, WebSocket for the push, a mocked VoP lookup, cloudflared for the phone, a pre-recorded cloned voice clip.
- Main risk: banks already ship the plain version (ABN AMRO, ASB). The edge has to be the transaction binding, the two-way check and the audit trail, so say that upfront. A phone passkey over a tunnel can fail on stage: keep a recorded fallback video.

**2. Agent Passport: KYA for a personal finance agent (links theme 01 and theme 02)**
- Pitch: the human signs a scoped mandate with a passkey ("my agent may pay up to €200 at grocery merchants until Sunday"), the agent signs every request (RFC 9421, Ed25519), and a bank-side gateway checks both before money moves.
- Demo moment: the agent does a normal grocery order, which is accepted. Then a prompt-injected page tells it to send €2,000 to a new IBAN: the gateway blocks it (outside the mandate), and a step-up push goes to the human's phone, who refuses. A third request, made with a forged agent key, is rejected before it reaches any logic.
- Stack: Node + `jose` + `web-bot-auth` + SimpleWebAuthn, an LLM agent with tool calls (Claude or Gemini), and Visa TAP / AP2 samples as reference reading, not as dependencies.
- Main risk: abstract for a jury of finance professionals, and three cryptographic actors to explain in 3 minutes. Show it as a gateway log with three lines: green, amber, red.

**3. Treasury guard: the deepfake CFO cannot sign (B2B, Finaxys clients' world)**
- Pitch: for asset managers and corporate treasuries, any payment request that arrives by call or video needs a second approver's passkey signature over the payment hash. This answers the Arup case, where a deepfaked "CFO" on a video call got an employee to send 15 transfers in Hong Kong in 2024 (amount not re-checked in this run).
- Demo moment: a fake "CFO" video asks for a payment, the payment-ops screen requires two transaction-bound passkey approvals, and the deepfake gets stuck at step 2.
- Stack: same as idea 1, with roles and a four-eyes rule.
- Main risk: close to a plain maker-checker flow that banks already have. Reuse it as a slide in idea 1 rather than build it separately.

## Verdict (2 or 3 lines)

"Prove the real" is the safest bet for 24 h: no model to train, standard cryptography, and a demo that cannot fail on stage the way a deepfake detector can. Build CallProof (idea 1) as the core. Add the Agent Passport mandate check (idea 2) as the stretch goal only if the core works by Saturday 02:00, since it is what connects the pitch to agentic payments.

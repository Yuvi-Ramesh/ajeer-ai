"""
seed_qdrant.py — Embed & upsert ALL Ajeer FAQ Q&As into Qdrant `ajeer_faq` collection.

Run once (or re-run to refresh all data):
    python seed_qdrant.py

Env vars required (.env):
    QDRANT_URL      — e.g. https://xxxx.us-west-1-0.aws.cloud.qdrant.io:6333
    QDRANT_API_KEY  — your Qdrant cloud API key
    GEMINI_API_KEY  — your Google AI / Gemini API key
"""

import os, time, uuid
from dotenv import load_dotenv
import google.generativeai as genai
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, VectorParams, Distance

load_dotenv()

QDRANT_URL = os.environ["QDRANT_URL"]
QDRANT_API_KEY = os.environ["QDRANT_API_KEY"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
COLLECTION = "ajeer_faq"
EMBED_MODEL = "text-embedding-004"
EMBED_DIM = 3072
BATCH_SIZE = 8

FAQ_DATA = [
    # ── TRANSFER ──────────────────────────────────────────────────────────────
    {
        "question": "Can I cancel a transaction?",
        "answer": "Once you click Confirm & Send, the transfer enters processing immediately. Cancellation is generally not possible after confirmation. If you believe an error was made immediately after sending, contact support via the Help Centre as quickly as possible. Recovery is not guaranteed but may be possible if the transfer has not yet been processed.",
        "category": "transfer",
    },
    {
        "question": "Why was my transfer declined?",
        "answer": "Common reasons include insufficient balance, exceeding daily limits, or a security hold. Your email will contain the specific reason and next steps.",
        "category": "transfer",
    },
    {
        "question": "What is the difference between Bank Account and Cash Pickup?",
        "answer": "Bank Account: Funds are transferred directly into the recipient's bank account. Requires bank name, branch name, and account number or IBAN.\nCash Pickup: The recipient collects cash from a partner location. Requires the collector's ID proof type and number instead of bank details.",
        "category": "transfer",
    },
    {
        "question": "Why was my transaction blocked?",
        "answer": "Transactions can be blocked for several reasons:\n• Insufficient balance — total to pay exceeds your available balance.\n• Daily transfer limit exceeded.\n• AML/compliance hold — flagged for review.\n• Restricted destination — recipient country or currency unavailable.\n• Recipient details mismatch — invalid IBAN or account number.",
        "category": "transfer",
    },
    {
        "question": "My transfer was blocked due to daily limits. What can I do?",
        "answer": "Wait until the next day. Daily limits reset every 24 hours from your first transaction of the day. You can retry the transfer once the limit resets.",
        "category": "transfer",
    },
    {
        "question": "Are there any hidden fees?",
        "answer": "No. All charges are shown clearly before you confirm a transfer. The send money form displays: amount you send, amount they receive (after exchange rate conversion), and total to pay (your send amount plus the service fee). No extra charges are applied after confirmation.",
        "category": "transfer",
    },
    {
        "question": "How do I send money?",
        "answer": "Step 1: Select the destination country.\nStep 2: Enter the beneficiary details or create a new beneficiary.\nStep 3: Choose the payout method (bank account / mobile wallet / cash collector).\nStep 4: Review fees and exchange rate (shown upfront before confirming).\nStep 5: Confirm the transfer.",
        "category": "transfer",
    },
    {
        "question": "How much does a transfer cost?",
        "answer": "Fees are shown upfront before you confirm. Exact cost depends on destination, amount, and payout method. Ajeer does not impose hidden fees — exchange rates are real market rates. Some correspondent banks may charge their own fees.",
        "category": "transfer",
    },
    {
        "question": "How long does a transfer take?",
        "answer": "Same-day transfers are available on most major currencies. If the beneficiary account is in the EEA in GBP, EUR or another EEA currency, funds arrive by the end of the next Business Day. For EEA accounts in non-EEA currencies, within 4 Business Days.",
        "category": "transfer",
    },
    {
        "question": "Can I save beneficiaries?",
        "answer": "Yes. Save beneficiaries to repeat transfers quickly without re-entering details. Once you add a recipient and complete a transfer, their details are saved to your Recipients list.",
        "category": "transfer",
    },
    {
        "question": "What is a Forward Contract?",
        "answer": "A Forward Contract lets you lock in an exchange rate for a future date. Margin (security deposit) may be required within 24 hours of the transaction receipt. You can also request a pre-delivery or roll-over of the delivery date.",
        "category": "transfer",
    },
    {
        "question": "What is a Limit Order?",
        "answer": "A Limit Order executes automatically when your target exchange rate is achieved within your specified time window. You can cancel a Limit Order at any time by phone or email before the rate is reached.",
        "category": "transfer",
    },
    {
        "question": "What is the Ajeer Card?",
        "answer": "The Ajeer prepaid card is globally accepted (Visa/Mastercard), tracks expenses, has no minimum balance, and works online and in stores. It enables you to spend in your held currencies without conversion fees.",
        "category": "transfer",
    },
    {
        "question": "What is the eSIM product?",
        "answer": "Ajeer offers instant data eSIMs for travel in 100+ countries. Activate in seconds, no physical SIM needed, and keep your existing number.",
        "category": "transfer",
    },
    {
        "question": "Will a payment reference appear on the recipient's bank statement?",
        "answer": "The reference field allows you to enter a reference. Whether it appears on the recipient's statement depends on their bank and the correspondent network used for that corridor. Not all banks pass payment references through.",
        "category": "transfer",
    },
    {
        "question": "What is the difference between transfer amount and total to pay?",
        "answer": "The transfer amount is what you enter as the send amount — the money converted and sent. The total to pay is the transfer amount plus the service fee, shown as a single clear figure before you confirm.",
        "category": "transfer",
    },
    {
        "question": "How often does the exchange rate update?",
        "answer": "Rates update in real time, typically every few seconds to minutes depending on market conditions and the currency pair. The rate shown when you enter your amount is live. The rate at the moment of confirmation is the rate applied to your transfer.",
        "category": "transfer",
    },
    {
        "question": "Can I lock in an exchange rate before confirming?",
        "answer": "The rate is locked at the moment of confirmation. There is no advance rate-locking or forward contract feature currently available as self-service. If you are concerned about rate movements, complete the transfer promptly after reviewing the amount.",
        "category": "transfer",
    },
    {
        "question": "Is the exchange rate the same for all transfer amounts?",
        "answer": "The rate margin may vary for very large or very small amounts depending on the corridor. Some corridors offer improved rates above certain thresholds. The rate applicable to your specific amount is always shown before you confirm.",
        "category": "transfer",
    },
    {
        "question": "Can I send multiple transfers to the same recipient on the same day?",
        "answer": "Yes, as long as your daily transfer count and total daily value limits are not exceeded. Each transfer is a separate transaction with its own fee. Unusually repetitive transfers to the same recipient in a short period may trigger an AML review flag.",
        "category": "transfer",
    },
    {
        "question": "Can I add a recipient without immediately sending them money?",
        "answer": "Yes. You can add recipients in advance via the Recipients section without initiating a transfer. This is useful for setting up beneficiaries ahead of time so transfers can be sent quickly when needed.",
        "category": "transfer",
    },
    {
        "question": "When will the daily limit be reset?",
        "answer": "Daily limits are reset on a rotating 24-hour basis, at a fixed midnight clock reset.",
        "category": "transfer",
    },
    {
        "question": "I was charged twice for the same transfer. What do I do?",
        "answer": "Check your Transactions history for two entries with the same recipient, amount, and date within minutes of each other. Note both Transaction IDs. Contact support immediately via the Help Center, provide both Transaction IDs, and request a duplicate payment investigation.",
        "category": "transfer",
    },
    {
        "question": "Which countries can I send money to?",
        "answer": "The list of supported destination countries is visible on the send money form when you select a recipient currency. Available corridors depend on regulatory licensing and partner banking/cash agent networks in the destination.",
        "category": "transfer",
    },
    {
        "question": "What exchange rate will I get? Is it live?",
        "answer": "The exchange rate displayed is a live rate fetched at the time you enter your amount. It reflects the current interbank rate with the portal's margin applied. The rate shown is guaranteed at the moment of confirmation. If you leave the form open for an extended period, the rate may refresh before you confirm.",
        "category": "transfer",
    },
    {
        "question": "What is an IBAN and when is it required?",
        "answer": "An IBAN (International Bank Account Number) is a standardised account identifier used across Europe and many other regions. It is required when sending to countries in the SEPA zone (most of Europe). For countries that do not use IBANs (such as the USA or some Asian countries), a standard account number is used instead.",
        "category": "transfer",
    },
    {
        "question": "My transfer has been on hold for more than 3 days. What should I do?",
        "answer": "Check your email inbox for any compliance request emails — you may have missed a document request (check spam too). Log in and check History → Ongoing for any status messages or action required prompts. If no communication has been received and it has been more than 3 business days, contact support with your Transaction ID and request a review status update.",
        "category": "transfer",
    },
    {
        "question": "Can I get a refund if I simply changed my mind after confirming?",
        "answer": "Unfortunately, change-of-mind refunds are not available once a transfer has been confirmed and entered Processing. If the transfer is still in Pending status, contact support immediately — cancellation may still be possible at that stage.",
        "category": "transfer",
    },
    {
        "question": "What happens to a transfer if the recipient's bank account is closed?",
        "answer": "The recipient bank will inform the account status. The portal will receive the bounce-back and update your transfer status to Failed. Your funds are then refunded to your portal balance. Timeline for the bounce-back varies by destination bank, typically 2–5 business days.",
        "category": "transfer",
    },
    {
        "question": "Can I schedule a transfer for a future date?",
        "answer": "Scheduled transfers are not currently available as a self-service feature. All transfers are processed immediately upon confirmation. You can save the recipient's details permanently so repeat transfers take only seconds to initiate.",
        "category": "transfer",
    },
    {
        "question": "What does 'on hold' mean; has my money left my account?",
        "answer": "When a transfer is in On-hold status, the system has accepted your transfer request but it has not yet been authorised by the admin or passed compliance checks. The funds have been reserved from your balance but not yet sent.",
        "category": "transfer",
    },
    {
        "question": "What does Failed mean and will I be charged?",
        "answer": "No — you are never charged for a failed transfer. If a transfer fails, the full amount is returned to your account balance or card with no deductions. Common reasons: invalid recipient account/IBAN, recipient bank rejected payment, AML/compliance block, or destination temporarily unavailable.",
        "category": "transfer",
    },
    {
        "question": "How do I track my transfer step by step?",
        "answer": "Log in and click History from the home page. Select the Ongoing tab for in-progress transfers or All for full history. Find your transfer and tap/click it to open the detail view showing current status, Transaction ID, timestamps, and fee breakdown. Refresh periodically or wait for the email notification when the status changes.",
        "category": "transfer",
    },
    {
        "question": "Can I filter or search my transaction history?",
        "answer": "Yes. The History section has three views: All (complete history), Ongoing (transfers in Pending or Processing), and Completed (successfully delivered transfers). Use the Transaction ID as a unique reference for support queries.",
        "category": "transfer",
    },
    {
        "question": "What is a transaction ID and where do I find it?",
        "answer": "The Transaction ID is a unique code assigned to every transfer the moment you click Confirm & Send. You can find it on the confirmation screen immediately after sending, in the transfer confirmation email, and in the History section on every transaction record.",
        "category": "transfer",
    },
    {
        "question": "My transfer has been in Processing for over 24 hours. What should I do?",
        "answer": "A delay can happen due to: banking holidays in the destination country, correspondent bank delays, compliance review (AML check), or payout partner issues. If no update after 2 full business days, contact support with your Transaction ID and request a status investigation.",
        "category": "transfer",
    },
    {
        "question": "The transfer shows Completed but the recipient has not received the money. Why?",
        "answer": "Completed means funds have left the portal and been delivered to the recipient's bank. The recipient's bank may take additional time to credit the funds. Ask the recipient to check with their bank. If the recipient's bank confirms no credit received within 2 business days of Completed status, contact support with your Transaction ID and request a payment trace.",
        "category": "transfer",
    },
    {
        "question": "I sent money to the wrong account. Can I get it back?",
        "answer": "Act immediately — speed is critical. Contact support immediately via the Help Centre live chat or helpline. Provide your Transaction ID, the incorrect recipient details, and the correct recipient details. If already Processing or Completed, a recall request is raised with the recipient's bank. Recovery is not guaranteed.",
        "category": "transfer",
    },
    {
        "question": "What does the service fee cover?",
        "answer": "The service fee covers currency conversion and exchange rate processing, and secure transaction processing and bank network charges.",
        "category": "transfer",
    },
    {
        "question": "Can I add notes or a memo to a transfer?",
        "answer": "Yes. The Reference field on the send money form allows you to add a free-text note or memo. This is optional and appears on your transaction record. The purpose of transfer field is separate and mandatory.",
        "category": "transfer",
    },
    {
        "question": "What if I enter the wrong amount?",
        "answer": "Correct it before clicking Confirm & Send on the review screen. After confirmation the amount cannot be changed. Contact support immediately if the transfer is still in Pending status.",
        "category": "transfer",
    },
    {
        "question": "How do I know my transfer was successful?",
        "answer": "You receive a confirmation email with full transfer details and a Transaction ID immediately after confirming. The transfer also appears in History → Ongoing and moves to Completed with a second email when delivered.",
        "category": "transfer",
    },
    {
        "question": "Can I track a transfer without logging in?",
        "answer": "No. Real-time status requires authentication. However, you receive automatic email notifications when a transfer reaches Completed or Failed, so you can stay informed without actively logging in.",
        "category": "transfer",
    },
    {
        "question": "Can the recipient track the transfer from their end?",
        "answer": "Recipients do not have portal access unless they have their own account. Forward your confirmation email to them so they have the Transaction ID and expected amount to reference with their bank.",
        "category": "transfer",
    },
    {
        "question": "Will the recipient be charged fees by their bank?",
        "answer": "The portal fee covers the sending side only. The recipient's bank may charge incoming transfer fees, currency conversion fees, or correspondent bank fees. The portal cannot control or predict third-party bank charges.",
        "category": "transfer",
    },
    {
        "question": "Do fees vary by destination country?",
        "answer": "Yes. Service fees vary by destination country, currency corridor, and transfer amount. The applicable fee is always shown before confirmation on the send money form.",
        "category": "transfer",
    },
    {
        "question": "How does the exchange rate affect how much the recipient gets?",
        "answer": "The rate determines how many units of destination currency the recipient receives. The rate is locked at the moment of confirmation and shown in full on the review screen — no surprises after you confirm.",
        "category": "transfer",
    },
    {
        "question": "What if the recipient's country does not use IBANs?",
        "answer": "Countries without IBANs (such as the USA or India) use alternative identifiers — typically a routing number plus account number, or a BSB code. The send money form shows the correct fields based on the destination country selected.",
        "category": "transfer",
    },
    {
        "question": "What details differ for a cash pickup recipient vs a bank recipient?",
        "answer": "Cash pickup recipients require an ID proof type and ID proof number instead of bank name and account number. A postal or ZIP code is also required. All other contact fields (name, email, phone, country, address) are the same as bank recipients.",
        "category": "transfer",
    },
    {
        "question": "What should I do if a recipient's bank details change?",
        "answer": "Go to Recipients, select the recipient, and choose Edit. Update the bank name, branch, and account number or IBAN. Save before initiating the next transfer. Sending to outdated details can cause misdirected payments.",
        "category": "transfer",
    },
    {
        "question": "Can a recipient be in the same country as the sender?",
        "answer": "Yes. Domestic transfers to recipients in the same country are supported where the corridor is available. The sender form shows available currencies and delivery options based on the destination country selected.",
        "category": "transfer",
    },
    {
        "question": "What payment methods can I use to fund a transfer?",
        "answer": "Accepted methods depend on your region and account tier. Some regions also support direct bank debit. Available methods are shown on the payment step of the send money form.",
        "category": "transfer",
    },
    {
        "question": "Can a business account send to personal recipients?",
        "answer": "Yes. Business accounts can send to both personal bank account recipients and cash pickup collectors. The purpose of transfer must accurately reflect the nature of the payment.",
        "category": "transfer",
    },
    {
        "question": "Can I use my business account for personal transfers?",
        "answer": "Business accounts are intended for business-related transfers. Using one for personal remittances may trigger a compliance review. For regular personal transfers, maintain a separate personal account.",
        "category": "transfer",
    },
    {
        "question": "How does cash pickup work for the recipient?",
        "answer": "Once a cash pickup transfer reaches Completed status, the recipient visits a partner agent location in the destination country and presents the same ID proof type and number registered in their recipient profile. No bank account is required.",
        "category": "transfer",
    },
    {
        "question": "How does the recipient know where to collect the cash?",
        "answer": "Share the partner agent location details, typically displayed during transfer setup or in the confirmation email.",
        "category": "transfer",
    },
    {
        "question": "Can the recipient send someone else to collect on their behalf?",
        "answer": "Cash pickup is identity-verified — the collector must present the exact ID proof number registered in the recipient profile. Third-party collection is generally not permitted unless the destination agent network has a specific authorisation process.",
        "category": "transfer",
    },
    {
        "question": "What if the recipient loses the ID they used to register?",
        "answer": "If the recipient cannot present the registered ID, they cannot collect. You would need to update the recipient profile with their new valid ID details and re-initiate the transfer. Contact support for guidance on any active transfer in this situation.",
        "category": "transfer",
    },
    {
        "question": "Can I transfer money to myself (e.g. my own foreign bank account)?",
        "answer": "Yes. Add yourself as a recipient with your foreign bank account details. This is common for people managing accounts in multiple countries. The same fees and compliance requirements apply.",
        "category": "transfer",
    },
    {
        "question": "How far back does my transaction history go?",
        "answer": "All past transfers including failed and cancelled ones remain accessible in your account. To view older records, scroll through the All tab in the History section. Records are ordered newest first by default.",
        "category": "transfer",
    },
    {
        "question": "Can I download or export my transaction history?",
        "answer": "A full export of your transaction history can be requested through the transport page. The export is typically provided as a CSV or PDF file and includes all transaction IDs, dates, times, recipients, amounts, statuses, fee breakdowns, and purpose of transfer entries.",
        "category": "transfer",
    },
    {
        "question": "The recipient says they received less than expected. What should I do?",
        "answer": "Ask the recipient to check whether their bank deducted an incoming fee or applied their own currency conversion. Compare the received amount against the 'they receive' figure on your confirmation. If there is an unexplained discrepancy beyond normal bank fees, contact support with the Transaction ID.",
        "category": "transfer",
    },
    {
        "question": "Can I test the portal with a small transfer before sending a large amount?",
        "answer": "Yes. Sending a small test transfer first is a sensible approach with a new recipient. Confirm delivery before sending larger amounts. All transfers, including small test amounts, attract the standard fee for the corridor.",
        "category": "transfer",
    },
    {
        "question": "What is the minimum transfer amount?",
        "answer": "A minimum transfer amount applies to all transactions. The exact minimum varies by destination currency and corridor. When you enter an amount below the minimum on the send money form, the system will display an error message and show the minimum required amount for that specific currency pair.",
        "category": "transfer",
    },
    {
        "question": "Can two different recipients share the same bank account number?",
        "answer": "The system allows the same bank account number to be saved under different recipient profiles — for example, a shared account held by two family members. Each profile is tracked independently with its own name and contact details.",
        "category": "transfer",
    },
    {
        "question": "Can I send to a recipient in a different currency than their bank account?",
        "answer": "Yes. The portal handles currency conversion automatically. You send in your currency, the portal converts at the live rate, and the recipient receives the converted amount.",
        "category": "transfer",
    },
    {
        "question": "Can I send multiple recipients in one transaction?",
        "answer": "No. Each transaction is a single transfer to one recipient. Initiate separate transfers for each recipient.",
        "category": "transfer",
    },
    # ── COMPLIANCE / KYC / AML ─────────────────────────────────────────────────
    {
        "question": "What is KYC verification and is it required?",
        "answer": "KYC (Know Your Customer) is an identity check required by regulation. You may need to complete it before higher transfer limits are available to you.",
        "category": "compliance",
    },
    {
        "question": "My business registration number was rejected. What does that mean?",
        "answer": "During Business account registration, the system validates your registration number against official records. A rejection means: the number was entered incorrectly (typos, missing digits, wrong format); the country of registration selected does not match the number's issuing authority; or the business is not yet registered or the record is not yet in the database.",
        "category": "compliance",
    },
    {
        "question": "What is AML and why does it apply to me?",
        "answer": "AML stands for Anti-Money Laundering. AML compliance protects you and other users by ensuring the platform cannot be misused for criminal activity. As a user, AML rules affect you in three main ways: identity verification (you must prove who you are before transferring money); purpose of transfer (you must state the reason for every transaction); and transfer limits (large or unusual transfers may trigger a review or require additional documentation).",
        "category": "compliance",
    },
    {
        "question": "Why has my transaction been flagged for AML review?",
        "answer": "A flag does not mean you have done anything wrong. Transactions are flagged automatically when they match one or more risk indicators: the transfer amount is unusually large or significantly higher than your typical activity; multiple transfers were made in a short time to different recipients; the destination country or recipient is on a monitored list; your KYC verification is incomplete or has expired; or the stated purpose of transfer is inconsistent with your account profile.",
        "category": "compliance",
    },
    {
        "question": "Why do I need to verify my identity?",
        "answer": "Identity verification is required to comply with Anti-Money Laundering (AML) regulations, Counter-Terrorist Financing (CTF) regulations, FCA regulatory requirements (FRN: 510848), and UK Money Laundering Regulations 2017.",
        "category": "compliance",
    },
    {
        "question": "Are my funds safe?",
        "answer": "Yes. Ajeer is authorised by the FCA. Customer funds are safeguarded in segregated bank accounts separately from company funds, in accordance with EMRs. In the event of insolvency, these funds form a separate asset pool — you are reimbursed in priority to other creditors. Ajeer uses industry-standard encryption and secure infrastructure.",
        "category": "compliance",
    },
    {
        "question": "What is Ajeer's data protection policy?",
        "answer": "Ajeer complies with GDPR 2018. Data Protection Officer: Mr G Kiruba. Personal data is processed lawfully under the 'legitimate interests' condition. Sensitive personal data is NOT collected or processed. Automated decision-taking is NOT used. You have rights of access, correction, and objection over your data.",
        "category": "compliance",
    },
    {
        "question": "Can my account be suspended?",
        "answer": "Yes. Ajeer may suspend accounts on grounds of security concerns, suspected fraud/unauthorised use, AML/CTF compliance, breach of agreement, or insolvency. You will be notified with reasons.",
        "category": "compliance",
    },
    {
        "question": "How is Ajeer regulated?",
        "answer": "Ajeer is a trading name of Monex International Limited, FCA-authorised Small Payment Institution (FRN: 510848). Registered in England and Wales (Company No. 04974470). The Financial Services Compensation Scheme (FSCS) does NOT cover Ajeer services. Unresolved complaints can be referred to the Financial Ombudsman Service (FOS): www.financialombudsman.org.uk",
        "category": "compliance",
    },
    {
        "question": "Do I need to verify my identity before using the portal?",
        "answer": "Basic registration allows limited use. To unlock full capabilities and higher limits, you must complete KYC verification via Account → Account Settings → KYC Verification.",
        "category": "compliance",
    },
    {
        "question": "My KYC document was rejected. What are the common reasons?",
        "answer": "Common rejection reasons: the document is expired; the photo is blurry or has glare; part of the document is cropped; the name does not exactly match your registered profile; or the document type is not accepted in your region. Re-upload a clear, full-frame photo under good lighting.",
        "category": "compliance",
    },
    {
        "question": "Can I submit KYC documents in a language other than English?",
        "answer": "Passports and national IDs in local languages are generally accepted as internationally recognised documents. If in a non-Latin script, a certified translation may be requested. Contact support to confirm requirements for your specific document language.",
        "category": "compliance",
    },
    {
        "question": "How do I know if my KYC has been approved?",
        "answer": "You receive a confirmation email when approved. Your KYC status also updates to Verified under Account → Account Settings → KYC Verification. If still showing Pending after 2 business days, contact support for an update.",
        "category": "compliance",
    },
    {
        "question": "Do I need to reverify KYC if I change my address?",
        "answer": "Address changes generally do not require full KYC re-verification, but you should update your address. Contact support to confirm for your account tier.",
        "category": "compliance",
    },
    {
        "question": "Do I need to re-verify my KYC if documents expire?",
        "answer": "Yes. If your identity document expires during KYC verification, you will need to upload a new valid document. The portal will notify you in advance and prompt you to update.",
        "category": "compliance",
    },
    {
        "question": "What counts as acceptable proof of address?",
        "answer": "A recent bank statement, utility bill, official government letter, or tenancy agreement — all dated within the last 3 months. The document must show your full name and current residential address matching your profile.",
        "category": "compliance",
    },
    {
        "question": "What happened if I am flagged on a sanction list?",
        "answer": "If your name, nationality, or destination matches a sanction list (such as OFAC or the UN Security Council list), the portal is legally prohibited from processing the transfer. Your account may be restricted pending a manual review. Contact support for guidance.",
        "category": "compliance",
    },
    {
        "question": "Who can see my transaction history?",
        "answer": "Your history is private and only accessible when you are authenticated. The compliance team can access records as required by law for AML reviews and regulatory reporting. Your data is never sold or shared for marketing purposes.",
        "category": "compliance",
    },
    {
        "question": "Does the portal share my data with third parties?",
        "answer": "Only as necessary: with correspondent banks to process transfers, with regulatory and legal authorities under valid legal orders, and with identity verification providers during KYC. Your data is never sold or shared for advertising purposes.",
        "category": "compliance",
    },
    {
        "question": "Is my data protected under GDPR?",
        "answer": "If you are based in the EU or UK, your data is protected under GDPR or UK GDPR respectively. Users in other regions are protected by equivalent local data protection laws. The Privacy Policy details your rights and how data is processed.",
        "category": "compliance",
    },
    {
        "question": "What regulations govern the portal?",
        "answer": "The portal is subject to AML laws, counter-terrorism financing (CTF) regulations, data protection laws (GDPR, CCPA, and regional equivalents), and payment services directives in each jurisdiction it operates. The Terms of Service details the specific regulatory framework.",
        "category": "compliance",
    },
    {
        "question": "Is the portal a licensed financial service provider?",
        "answer": "Yes. The portal operates under financial services licences granted by relevant regulatory authorities in each country where it operates. Licence details are available in the Terms of Service under Account → Support & Legal.",
        "category": "compliance",
    },
    {
        "question": "What is two-factor authentication (2FA)?",
        "answer": "2FA requires two separate verification steps — something you know (your password) and something you have (access to your email for the OTP). The portal requires 2FA on every login, making stolen passwords alone insufficient.",
        "category": "compliance",
    },
    {
        "question": "Is my financial data stored securely?",
        "answer": "Yes. All financial and personal data is encrypted at rest and in transit. Session credentials are HttpOnly cookies inaccessible to JavaScript. The portal operates under financial regulatory data protection requirements.",
        "category": "compliance",
    },
    # ── SUPPORT / ACCOUNT ─────────────────────────────────────────────────────
    {
        "question": "Can I change my account type after registering?",
        "answer": "No. Account type is fixed at registration. If you registered under the wrong type, contact support — they will advise on your options, which may involve creating a new account.",
        "category": "support",
    },
    {
        "question": "How do I contact support?",
        "answer": "Go to Account → Support & Legal → Help Centre. Live chat is available from any page via the icon in the bottom-right corner. Support is available 24/7. Always include your Transaction ID for transfer queries.",
        "category": "support",
    },
    {
        "question": "What information should I have ready when contacting support?",
        "answer": "Your Transaction ID for transfer queries. Your registration email address, account name, and a clear description of the issue. For security queries you may be asked to verify your identity before the team can share account information.",
        "category": "support",
    },
    {
        "question": "I am not receiving confirmation emails. What should I do?",
        "answer": "Check your spam or junk folder. Add the sender address to your contacts or safe-sender list. Verify that the email address on your account is correct under Account → Personal Information. Check that your inbox is not full. If the issue continues, contact support via the Help Centre.",
        "category": "support",
    },
    {
        "question": "What should I do if I forget which email I registered with?",
        "answer": "Try email addresses you commonly use on the login page. If you cannot identify the registered email, contact support with your full name, registered phone number, and date of birth. The team can locate your account after identity verification.",
        "category": "support",
    },
    {
        "question": "How do I make a complaint?",
        "answer": "Go to Account → Support & Legal → Help Centre and submit a complaint. Provide your Transaction ID, a clear description of the issue, and any supporting evidence. Contact Ajeer at cs@Ajeer.money or post to: Compliance Department, Monex International Ltd, 32 Spring Street, Paddington, W2 1JA. If unresolved, refer to the Financial Ombudsman Service (FOS): www.financialombudsman.org.uk",
        "category": "support",
    },
    {
        "question": "How do I close my account?",
        "answer": "Contact support via the Help Centre. Download your transaction history before closure — access to records is lost after the account is closed. Give at least 1 month's prior written notice. Remaining funds will be transferred to your nominated bank account after deducting any amounts owed.",
        "category": "support",
    },
    {
        "question": "What if a payment went to the wrong account?",
        "answer": "If you provided incorrect details, Ajeer is not liable but will use reasonable efforts to recover the payment. Reasonable costs may be charged for recovery attempts.",
        "category": "support",
    },
    {
        "question": "What if there was an unauthorised payment?",
        "answer": "Notify Ajeer without undue delay (within 13 months) via cs@Ajeer.money. Ajeer will immediately refund unauthorised payments subject to investigation. If fraud or gross negligence on your part is proven, you may bear liability.",
        "category": "support",
    },
    {
        "question": "Is support available on weekends and public holidays?",
        "answer": "Live chat is available 24/7 including weekends and public holidays. Compliance review and KYC processing may be limited to business days. Weekend email responses may take slightly longer than the standard 1 business day target.",
        "category": "support",
    },
    {
        "question": "How do I update my registered email address?",
        "answer": "Email changes require identity verification and are processed by support. Contact the Help Centre with your current registered details and provide the new email address. A verification step for the new email is required before the change is applied.",
        "category": "support",
    },
    {
        "question": "How do I find out why my transfer was blocked?",
        "answer": "A notification email is sent immediately with the specific reason. Check the transaction detail in History → All for any status messages. Contact the Help Centre with your Transaction ID if unclear.",
        "category": "support",
    },
    {
        "question": "Can a blocked transfer be resubmitted?",
        "answer": "Yes. Once the underlying issue is resolved, correct the problem and then initiate a new transfer. Blocked transfers are not automatically retried.",
        "category": "support",
    },
    {
        "question": "Can I temporarily disable my account?",
        "answer": "Contact support to request a temporary freeze — for example, if you suspect unauthorised access and want to protect your account while you regain control.",
        "category": "support",
    },
    {
        "question": "Can support access my account without my permission?",
        "answer": "Authorised staff can access account records (profile data, transaction history, KYC status) to resolve your query or conduct a compliance review. They cannot initiate transfers, change your password, or modify your profile without your explicit instruction.",
        "category": "support",
    },
    {
        "question": "Can I get help in my own language?",
        "answer": "Support is typically available in English. Some regions offer support in additional languages — check the Help Centre for your regional support contacts and available languages.",
        "category": "support",
    },
    {
        "question": "How do I raise a formal complaint about a transfer?",
        "answer": "Go to Account → Support & Legal → Help Centre and submit a complaint. Provide your Transaction ID, a clear description of the issue, and any supporting evidence. The portal must acknowledge your complaint within a defined timeframe and resolve it within the regulatory period for your region.",
        "category": "support",
    },
    {
        "question": "Can I turn off the transfer confirmation emails?",
        "answer": "No. Transfer confirmation emails are mandatory transactional communications required by financial regulations. They serve as official records. Security alerts are also non-optional.",
        "category": "support",
    },
    {
        "question": "Can I change the email address that receives portal notifications?",
        "answer": "Yes, but it requires identity verification through support. Contact the Help Centre, confirm your identity, and provide the new email address. A verification step for the new address is required before the change is applied.",
        "category": "support",
    },
    {
        "question": "What should I do immediately after registering?",
        "answer": "Complete your KYC verification to unlock full transfer capabilities. Add your first recipient under the Recipients section. Review the send money steps in the Help Centre before your first transfer to ensure a smooth experience.",
        "category": "support",
    },
    {
        "question": "I am blocked out and cannot access my email. What can I do?",
        "answer": "Contact support directly via the Help Centre using an alternative contact method. They will verify your identity through secondary means (account details, KYC documents, or registered phone) and help you regain access.",
        "category": "support",
    },
    {
        "question": "What is the trading name field in business registration?",
        "answer": "The trading name is the name your business operates under publicly if different from its registered legal name. For example, 'ABC Holdings Ltd' may trade as 'ABC Transfers'. Both fields are required for correct business identification and verification.",
        "category": "support",
    },
    {
        "question": "What happens to saved recipients if I change my account type?",
        "answer": "Saved recipients are not affected by account type changes. Your full Recipients list carries over. Contact support to confirm if any recipient re-verification is needed under the new account type's compliance framework.",
        "category": "support",
    },
    # ── FAQ / GENERAL / SECURITY / ACCOUNT ────────────────────────────────────
    {
        "question": "Why do I need to enter an OTP every time I log in?",
        "answer": "The login OTP is a two-factor authentication (2FA) measure. Even if someone else learns your password, they cannot access your account without also having access to your email inbox. This is standard practice for financial services portals and is required to protect your funds and personal data.",
        "category": "faq",
    },
    {
        "question": "I did not receive my OTP. What should I do?",
        "answer": "Try these steps in order: check your spam or junk folder; wait up to 2 minutes as delivery can be delayed during peak times; use the Resend OTP button once the countdown timer expires; make sure the email address or phone number you entered is correct. If the problem continues, contact the support team via the Help Centre.",
        "category": "support",
    },
    {
        "question": "What is Ajeer?",
        "answer": "Ajeer (trading name of Monex International Limited) is a digital money transfer and remittance platform. It lets you send money internationally to bank accounts, mobile wallets, or cash collectors — fast, securely, and with real exchange rates. Monex International Ltd is authorised by the FCA as a Small Payment Institution (FRN: 510848). Registered address: 32 Spring Street, Paddington, London, W2 1JA.",
        "category": "faq",
    },
    {
        "question": "Who can use Ajeer?",
        "answer": "Anyone who meets the eligibility criteria — including age (18+), residency, and identity verification requirements — can use the app to send money.",
        "category": "faq",
    },
    {
        "question": "What currencies does Ajeer support?",
        "answer": "Ajeer lets you hold 6+ currencies and send to 30+ currencies including USD, AED, SAR, INR, GBP, EUR, PKR, and more. Exchange rates are real market rates with no hidden markups.",
        "category": "faq",
    },
    {
        "question": "Is Ajeer available on mobile?",
        "answer": "Yes. The Ajeer app is available on the App Store and Google Play Store.",
        "category": "faq",
    },
    {
        "question": "What payout methods are available?",
        "answer": "Recipients can receive funds via: bank account transfer, mobile wallet, or cash collector.",
        "category": "faq",
    },
    {
        "question": "What are Ajeer's key features?",
        "answer": "Same-day transfers on most major currencies; real exchange rates (bank-beating rates); hold 6+ currencies in one account; eSIM product for travel (instant data in 100+ countries, no physical SIM needed); prepaid Ajeer card (globally accepted, spend anywhere, no minimum balance); freelancer/non-resident remittance support; repeat transfers with saved beneficiaries.",
        "category": "faq",
    },
    {
        "question": "How do I create an account?",
        "answer": "Click Register or Sign up on the Ajeer website or app. You'll need basic identification documents for electronic identity verification.",
        "category": "faq",
    },
    {
        "question": "What are Ajeer's contact details?",
        "answer": "Email: cs@Ajeer.money. Post: Compliance Department, Monex International Ltd, 32 Spring Street, Paddington, W2 1JA, UK.",
        "category": "faq",
    },
    {
        "question": "What happened if I enter an invalid email during registration?",
        "answer": "You are asked to correct it before continuing. The system will not allow registration to proceed with an invalid email format.",
        "category": "faq",
    },
    {
        "question": "How long do cookies last?",
        "answer": "You can clear all cookies at any time through your browser settings. Clearing session cookies will log you out of the portal immediately.",
        "category": "faq",
    },
    {
        "question": "How does the browser know which cookie to send with which request?",
        "answer": "The browser handles this automatically based on rules set when each cookie is created: mt_client is sent only during the signup process; mt_access is sent automatically on every dashboard request and API call; mt_refresh is sent only to POST /auth/refresh-token.",
        "category": "faq",
    },
    {
        "question": "Are the cookies visible if I inspect my browser?",
        "answer": "The cookies exist in the browser's cookie store and can be viewed in browser developer tools (Application → Cookies in Chrome DevTools). However, because they are HttpOnly, you can see cookie names and expiry dates but cannot read or copy the values through JavaScript. Always lock your device when stepping away.",
        "category": "faq",
    },
    {
        "question": "Why did my session expire?",
        "answer": "For your security, the portal automatically ends your login session after a period of inactivity (15 minutes). Your session may expire if you have been inactive for the session timeout period, closed the browser tab without logging out, or your session token was invalidated due to a security event.",
        "category": "faq",
    },
    {
        "question": "What happens to a transfer I was filling in when my session expired?",
        "answer": "If your session expires mid-transfer, the transfer is not submitted and no funds are moved. The form data is lost when the session ends. Log in again, navigate back to Recipients, select your recipient, re-enter the transfer amount, purpose, and reference, then review and confirm.",
        "category": "faq",
    },
    {
        "question": "What happens after 3 wrong login attempts?",
        "answer": "After 3 consecutive incorrect login attempts, your account is temporarily locked. Further login attempts are blocked. The lockout lifts automatically after a set waiting period (typically 15 minutes). Alternatively, you can unlock your account immediately by using Forgot Password to reset your credentials.",
        "category": "faq",
    },
    {
        "question": "How do I reset my password?",
        "answer": "From the login page: click Forgot Password, enter your registered email address, and a reset link is sent to your inbox. When already logged in: go to Account → Account Settings → Change Password, enter your current password, then enter and confirm your new password.",
        "category": "faq",
    },
    {
        "question": "How can I track a transaction after sending?",
        "answer": "Once a transfer is confirmed, monitor it from the History section. Select the Ongoing tab to see transfers currently in progress. You can view full details: recipient, Transaction ID, date, amount, and status. When the transfer completes, it moves to the Completed tab and you receive a status update email.",
        "category": "faq",
    },
    {
        "question": "My password reset link has expired. What do I do?",
        "answer": "Return to the login page, click Forgot Password again, and request a new reset link. Each new request invalidates the previous link.",
        "category": "faq",
    },
    {
        "question": "Can I stay logged in across browser sessions?",
        "answer": "Yes. The mt_refresh cookie keeps your session active for several days. You will only be asked to log in again when this cookie expires or you explicitly log out.",
        "category": "faq",
    },
    {
        "question": "Why was I logged out automatically?",
        "answer": "Automatic logout happens when your session token expires, when you log out from another device, or after a password change that invalidates all active sessions. Log in again normally to continue.",
        "category": "faq",
    },
    {
        "question": "Can I be logged in on multiple devices simultaneously?",
        "answer": "Yes. The portal supports concurrent sessions. Each session has an independent access token. Logging out on one device does not affect other sessions.",
        "category": "faq",
    },
    {
        "question": "Can I save a recipient for future transfers?",
        "answer": "Yes. Once you add a recipient and complete a transfer, their details are saved to your Recipients list. Future transfers require no re-entry — simply select the recipient and enter the new amount.",
        "category": "faq",
    },
    {
        "question": "Can I edit a saved recipient's details?",
        "answer": "Yes. Go to Recipients, select the vertical menu, and choose Edit. Changes apply to future transfers only. Previous transaction records retain the details at the time of sending.",
        "category": "faq",
    },
    {
        "question": "Can I delete a saved recipient?",
        "answer": "Yes. Go to Recipients, select the vertical menu, and choose Delete. Past transactions remain in your history unaffected.",
        "category": "faq",
    },
    {
        "question": "Why do I need to provide my phone number during registration?",
        "answer": "Phone verification proves you have access to the number and enables secure contact. Your verified number is also pre-filled into the nationality and residence section of your profile, saving you from re-entering it.",
        "category": "faq",
    },
    {
        "question": "Can I register with an email address already used elsewhere?",
        "answer": "Each email address can only be linked to one portal account. If the email is already in use, the system will inform you. Use the Forgot Password option on the login page to recover that existing account.",
        "category": "faq",
    },
    {
        "question": "Is there an age requirement to register?",
        "answer": "Yes. You must be 18 or older to open an account. The system may request identity verification during KYC to confirm age.",
        "category": "faq",
    },
    {
        "question": "How long does registration take?",
        "answer": "Typically 5–10 minutes if you have your details ready. OTP delivery usually takes under 2 minutes. Business accounts take slightly longer due to additional profile sections.",
        "category": "faq",
    },
    {
        "question": "Can I save my registration progress and come back later?",
        "answer": "Registration must be completed in a single session. If you close the page mid-way, you will need to start again. Have your details ready before starting.",
        "category": "faq",
    },
    {
        "question": "What should I do if I think my account has been hacked?",
        "answer": "Change your password immediately via Forgot Password. Contact support live chat and report the breach. The team will review activity, freeze suspicious transfers, and help you secure the account.",
        "category": "faq",
    },
    {
        "question": "Will the portal ever ask for my password via email?",
        "answer": "Never. The portal will never ask for your password, full OTP, or card details via email, phone, or chat. If you receive such a request, it is a phishing attempt — report it to support immediately.",
        "category": "faq",
    },
    {
        "question": "How do I recognise a phishing email from the portal?",
        "answer": "Legitimate portal emails come from the official registered sender address only and never ask for your password, OTP, or card number. If you receive a suspicious email with unexpected links or urgent requests, do not click anything — forward it to support.",
        "category": "faq",
    },
    {
        "question": "How does the portal protect my account?",
        "answer": "Multiple layers: email OTP on every login, HttpOnly session cookies inaccessible to JavaScript, automatic session expiry after 15 minutes of inactivity, account lockout after 3 failed attempts, and AML monitoring to detect unusual patterns.",
        "category": "faq",
    },
    {
        "question": "Can I use VPN while accessing the portal?",
        "answer": "VPN usage may interfere with IP-based security signals the portal uses to detect suspicious logins. Some VPN IPs may trigger additional verification steps. If you experience login issues while on a VPN, disconnect and access the portal directly.",
        "category": "faq",
    },
    {
        "question": "Can I use the portal on a mobile device?",
        "answer": "Yes. The portal is accessible via any modern mobile browser with a responsive interface. A mobile application is also available for a better experience.",
        "category": "faq",
    },
    {
        "question": "What browsers are supported?",
        "answer": "All modern browsers including Chrome, Firefox, and Edge in current and previous major versions. Internet Explorer is not supported. Keep your browser updated for the best experience.",
        "category": "faq",
    },
    {
        "question": "What should I do if the portal is not loading?",
        "answer": "Refresh the page. Clear your browser cache and cookies. Try a different browser or device. Check your internet connection. If the issue persists across devices, the portal may be under maintenance — contact support.",
        "category": "faq",
    },
    {
        "question": "What happens during portal maintenance?",
        "answer": "During scheduled maintenance, some or all functions may be temporarily unavailable. A notice is displayed in advance. Transfers already in progress are not affected — they continue on the banking side.",
        "category": "faq",
    },
    {
        "question": "Is there a mobile app available?",
        "answer": "App availability depends on the portal's current product offerings and your region. Check Account → Support & Legal → Help Centre or the portal's official website for the latest mobile app information.",
        "category": "faq",
    },
    {
        "question": "What language does the portal support?",
        "answer": "The default language is English. Additional language support may be available depending on your region. Check the settings or footer for language selection options.",
        "category": "faq",
    },
    {
        "question": "Can I use the portal without accepting cookies?",
        "answer": "Essential session cookies (mt_client, mt_access, mt_refresh) are always active — without them, login and transfers are impossible. Analytics and preference cookies may be optional and can be declined in cookie consent settings.",
        "category": "faq",
    },
    {
        "question": "What is the portal's operating currency?",
        "answer": "The portal supports multiple sending and receiving currencies. Your account displays balances in your home currency by default. The send money form handles all conversion calculations automatically.",
        "category": "faq",
    },
    {
        "question": "How many recipients can I save on my account?",
        "answer": "There is no fixed cap on saved recipients. You can add as many bank account recipients and cash pickup collectors as needed. All are stored in your Recipients list and available for future transfers instantly.",
        "category": "faq",
    },
    {
        "question": "Can I avoid session expiry mid-transfer?",
        "answer": "Have all details ready before starting. Complete the full transfer in one sitting (3–5 minutes). Avoid leaving the form open while switching to other tabs for extended periods.",
        "category": "faq",
    },
    {
        "question": "Why was I redirected to /login unexpectedly?",
        "answer": "This happens when mt_refresh expires, when you log out from another device with global sign-out, or after a password change invalidates all sessions. Log in again normally.",
        "category": "faq",
    },
    {
        "question": "Can my session extend automatically without logging out?",
        "answer": "Yes. The browser interceptor monitors mt_access and uses mt_refresh to renew it silently before it expires. As long as mt_refresh is valid and you remain active, the session continues indefinitely.",
        "category": "faq",
    },
    {
        "question": "What happens to cookies when I log out?",
        "answer": "The server invalidates all active tokens immediately. Even if someone copied the raw cookie values before logout, those values become useless instantly.",
        "category": "faq",
    },
    {
        "question": "Which support resources are accessible from the portal?",
        "answer": "Live chat, Help Centre, Terms of Service, and Privacy Policy are all accessible from the portal.",
        "category": "faq",
    },
    # ── REWARDS ────────────────────────────────────────────────────────────────
    {
        "question": "What is the Ajeer Rewards Program?",
        "answer": "The Ajeer Remittance Rewards Program rewards customers who send money internationally via the Ajeer app. Both senders (Remitters) and receivers (Beneficiaries) are entered into monthly lucky draws to win prizes.",
        "category": "rewards",
    },
    {
        "question": "Who is eligible for the rewards program?",
        "answer": "Individuals aged 18+ and corporates who are UK residents or citizens. Transactions must be made through the Ajeer app. Minimum remittance of £100 per transaction to qualify.",
        "category": "rewards",
    },
    {
        "question": "How are chances to win calculated in the rewards program?",
        "answer": "Chances are earned per transaction based on the amount sent (GBP): £100–£299 = 1 chance; £300–£499 = 2 chances; £500–£999 = 3 chances; £1000–£1999 = 4 chances; £2000–£3499 = 5 chances; £3500–£5000+ = 6 chances (capped). Multiple transactions in the same month earn more chances. Chances do NOT roll over to next month.",
        "category": "rewards",
    },
    {
        "question": "What prizes can I win in the Ajeer Rewards Program?",
        "answer": "Prizes include: Umrah tickets for two (complete pilgrimage packages); Premium Electronics (smartphones, laptops, tablets); Lifestyle Rewards (fashion accessories, home appliances, exclusive vouchers). All prizes are gifted by Bogo Technologies (Private) Limited under the Bogo Liv brand.",
        "category": "rewards",
    },
    {
        "question": "How does the monthly draw work?",
        "answer": "Separate draws for Remitters and Beneficiaries each month. 3 winners selected from each pool via computerized random selection. A participant may only win once per draw per month. Remitter winners announced on Ajeer social media and app/website.",
        "category": "rewards",
    },
    {
        "question": "How will I be notified if I win?",
        "answer": "Remitter winners: contacted by phone, and possibly email or in-app message. Beneficiary winners: contacted by phone. Prizes must be claimed within 15 days of notification — failure may result in disqualification. Proof of identity and transaction verification may be required.",
        "category": "rewards",
    },
    {
        "question": "Can rewards prizes be transferred or exchanged?",
        "answer": "No. Prizes are non-transferable and non-exchangeable. Ajeer may substitute prizes without notice.",
        "category": "rewards",
    },
    {
        "question": "What is the Bogo Liv Gold membership benefit?",
        "answer": "Beneficiaries who receive a total of £200 or more through the Ajeer app in a calendar month automatically receive 1 month of complimentary Gold membership on the Bogo Liv app.",
        "category": "rewards",
    },
]


def get_embeddings(texts: list[str]) -> list[list[float]]:
    genai.configure(api_key=GEMINI_API_KEY)
    results = []
    for text in texts:
        res = genai.embed_content(
            model=EMBED_MODEL,
            content=text,
            task_type="retrieval_document",
            output_dimensionality=EMBED_DIM,
        )
        results.append(res["embedding"])
    return results


def ensure_collection(client: QdrantClient):
    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION not in existing:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
        )
        print(f"✓ Created collection '{COLLECTION}'")
    else:
        print(f"✓ Collection '{COLLECTION}' exists — upserting {len(FAQ_DATA)} points")


def seed():
    print(f"Connecting to Qdrant: {QDRANT_URL}...")
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    ensure_collection(client)

    print(f"Embedding {len(FAQ_DATA)} FAQ entries with '{EMBED_MODEL}'...")
    points: list[PointStruct] = []

    for i in range(0, len(FAQ_DATA), BATCH_SIZE):
        batch = FAQ_DATA[i : i + BATCH_SIZE]
        texts = [f"{d['question']}\n{d['answer']}" for d in batch]
        try:
            embeddings = get_embeddings(texts)
        except Exception as e:
            print(f"  Embedding error batch {i//BATCH_SIZE+1}: {e} — retrying in 5s")
            time.sleep(5)
            embeddings = get_embeddings(texts)

        for doc, vec in zip(batch, embeddings):
            points.append(
                PointStruct(
                    id=str(uuid.uuid4()),
                    vector=vec,
                    payload={
                        "question": doc["question"],
                        "answer": doc["answer"],
                        "category": doc["category"],
                    },
                )
            )
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(FAQ_DATA) - 1) // BATCH_SIZE + 1
        print(
            f"  ✓ Batch {batch_num}/{total_batches} embedded ({len(points)} points so far)"
        )
        time.sleep(0.5)

    client.upsert(collection_name=COLLECTION, points=points)
    print(f"\n✅ Upserted {len(points)} points into '{COLLECTION}'")
    for cat in ["faq", "transfer", "compliance", "support", "rewards"]:
        n = sum(1 for p in points if p.payload["category"] == cat)
        print(f"   {cat}: {n} entries")
    print("\n✅ Qdrant seeding complete!")


if __name__ == "__main__":
    seed()

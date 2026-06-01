"""
seed_faq.py — Seed all FAQ Q&As into MongoDB `faq_kb` collection.

Each document stored as:
  {
    question:    str,         # the original question text
    answer:      str,         # the full answer text
    keywords:    [str],       # lowercase keywords extracted for fast lookup
    category:    str,         # faq | transfer | compliance | support
    created_at:  datetime
  }

Run once (or re-run to refresh):
    python seed_faq.py
"""

from pymongo import MongoClient, ASCENDING, TEXT
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/ajeer_db")

# ─────────────────────────────────────────────────────────────────
# All Q&A pairs extracted from the platform FAQ document
# ─────────────────────────────────────────────────────────────────
FAQ_DATA = [
    {
        "question": "Can I cancel a transaction?",
        "answer": (
            "Once you click Confirm & Send, the transfer enters processing immediately. "
            "Cancellation is generally not possible after confirmation. If you believe an error "
            "was made immediately after sending, contact support via the Help Centre as quickly "
            "as possible. Recovery is not guaranteed but may be possible if the transfer has not "
            "yet been processed."
        ),
        "category": "transfer",
    },
    {
        "question": "Why was my transfer declined?",
        "answer": (
            "Common reasons include insufficient balance, exceeding daily limits, or a security "
            "hold. Your email will contain the specific reason and next steps."
        ),
        "category": "transfer",
    },
    {
        "question": "Can I change my account type after registering?",
        "answer": (
            "No. Account type is fixed at registration. If you registered under the wrong type, "
            "contact support — they will advise on your options, which may involve creating a new account."
        ),
        "category": "support",
    },
    {
        "question": "What is KYC verification and is it required?",
        "answer": (
            "KYC (Know Your Customer) is an identity check required by regulation. You may need "
            "to complete it before higher transfer limits are available to you."
        ),
        "category": "compliance",
    },
    {
        "question": "My business registration number was rejected. What does that mean?",
        "answer": (
            "During Business account registration, the system validates your registration number "
            "against official records. A rejection means one of the following: the number was "
            "entered incorrectly (check for typos, missing digits, or wrong format); the country "
            "of registration selected does not match the number's issuing authority; or the "
            "business is not yet registered, or the record is not yet in the database."
        ),
        "category": "compliance",
    },
    {
        "question": "Why do I need to enter an OTP every time I log in?",
        "answer": (
            "The login OTP is a two-factor authentication (2FA) measure. Even if someone else "
            "learns your password, they cannot access your account without also having access to "
            "your email inbox. This is standard practice for financial services portals and is "
            "required to protect your funds and personal data."
        ),
        "category": "faq",
    },
    {
        "question": "I did not receive my OTP. What should I do?",
        "answer": (
            "Try these steps in order: check your spam or junk folder; wait up to 2 minutes as "
            "delivery can be delayed during peak times; use the Resend OTP button once the "
            "countdown timer expires; make sure the email address or phone number you entered is "
            "correct. If the problem continues, contact the support team via the Help Centre."
        ),
        "category": "support",
    },
    {
        "question": "What is the difference between Bank Account and Cash Pickup?",
        "answer": (
            "Bank Account: Funds are transferred directly into the recipient's bank account. "
            "Requires bank name, branch name, and account number or IBAN. "
            "Cash Pickup: The recipient collects cash from a partner location. Requires the "
            "collector's ID proof type and number instead of bank details."
        ),
        "category": "transfer",
    },
    {
        "question": "What is AML and why does it apply to me?",
        "answer": (
            "AML stands for Anti-Money Laundering. AML compliance protects you and other users "
            "by ensuring the platform cannot be misused for criminal activity. As a user, AML "
            "rules affect you in three main ways: identity verification (you must prove who you "
            "are before transferring money); purpose of transfer (you must state the reason for "
            "every transaction); and transfer limits (large or unusual transfers may trigger a "
            "review or require additional documentation)."
        ),
        "category": "compliance",
    },
    {
        "question": "Why has my transaction been flagged for AML review?",
        "answer": (
            "A flag does not mean you have done anything wrong. Transactions are flagged "
            "automatically when they match one or more risk indicators. Common reasons include: "
            "the transfer amount is unusually large or significantly higher than your typical "
            "activity; multiple transfers were made in a short time to different recipients; the "
            "destination country or recipient is on a monitored list; your KYC verification is "
            "incomplete or has expired; or the stated purpose of transfer is inconsistent with "
            "your account profile."
        ),
        "category": "compliance",
    },
    {
        "question": "How do I contact support?",
        "answer": (
            "Go to Account → Support & Legal → Help Centre. Live chat is available from any page "
            "via the icon in the bottom-right corner. Support is available 24/7. Always include "
            "your Transaction ID for transfer queries."
        ),
        "category": "support",
    },
    {
        "question": "What information should I have ready when contacting support?",
        "answer": (
            "Your Transaction ID for transfer queries. Your registration email address, account "
            "name, and a clear description of the issue. For security queries you may be asked "
            "to verify your identity before the team can share account information."
        ),
        "category": "support",
    },
    {
        "question": "How long do cookies last?",
        "answer": (
            "You can clear all cookies at any time through your browser settings. Clearing "
            "session cookies will log you out of the portal immediately."
        ),
        "category": "faq",
    },
    {
        "question": "Are the cookies visible if I inspect my browser?",
        "answer": (
            "The cookies exist in the browser's cookie store and can be viewed in browser "
            "developer tools (e.g. Application → Cookies in Chrome DevTools). However, because "
            "they are HttpOnly, you can see the cookie names and expiry dates in DevTools but "
            "you cannot read or copy the cookie values through JavaScript. If someone else has "
            "physical access to your device with DevTools open, they could potentially view the "
            "raw cookie values. Always lock your device when stepping away."
        ),
        "category": "faq",
    },
    {
        "question": "Why was my transaction blocked?",
        "answer": (
            "Transactions can be blocked for several reasons: insufficient balance (the total "
            "including fees exceeds your available balance); daily transfer limit exceeded; "
            "AML/compliance hold (flagged for review); restricted destination (the recipient "
            "country or currency is unavailable); or recipient details mismatch (invalid IBAN "
            "or account number)."
        ),
        "category": "transfer",
    },
    {
        "question": "My transfer was blocked due to daily limits. What can I do?",
        "answer": (
            "Wait until the next day. Daily limits reset every 24 hours from your first "
            "transaction of the day. You can retry the transfer once the limit resets."
        ),
        "category": "transfer",
    },
    {
        "question": "I am not receiving confirmation emails. What should I do?",
        "answer": (
            "Check your spam or junk folder. Add the sender address to your contacts or "
            "safe-sender list. Verify that the email address on your account is correct under "
            "Account → Personal Information. Check that your inbox is not full. If the issue "
            "continues, contact support via the Help Centre."
        ),
        "category": "support",
    },
    {
        "question": "Are there any hidden fees?",
        "answer": (
            "All charges are shown clearly before you confirm a transfer. The send money form "
            "displays: the amount you send, the amount they receive (after exchange rate "
            "conversion), and the total to pay (your send amount plus the service fee). The "
            "amount charged to your account or card is exactly the Total to pay shown on the "
            "review screen. No extra charges are applied after confirmation."
        ),
        "category": "transfer",
    },
    {
        "question": "Why did my session expire?",
        "answer": (
            "For your security, the portal automatically ends your login session after a period "
            "of inactivity (15 minutes). Your session may expire if you have been inactive for "
            "the session timeout period, you closed the browser tab without logging out, or your "
            "session token was invalidated due to a security event such as a password change on "
            "another device."
        ),
        "category": "faq",
    },
    {
        "question": "What happens to a transfer I was filling in when my session expired?",
        "answer": (
            "If your session expires mid-transfer, the transfer is not submitted and no funds "
            "are moved. The form data is lost when the session ends. Log in again, navigate back "
            "to Recipients, re-enter the transfer amount, purpose, and reference, then review "
            "and confirm the transfer."
        ),
        "category": "transfer",
    },
    {
        "question": "What happens after 3 wrong login attempts?",
        "answer": (
            "After 3 consecutive incorrect login attempts, your account is temporarily locked. "
            "You are shown a lockout message and further login attempts are blocked. The lockout "
            "lifts automatically after a set waiting period (typically 15 minutes). Alternatively, "
            "you can unlock your account immediately by using Forgot Password to reset your credentials."
        ),
        "category": "faq",
    },
    {
        "question": "How do I reset my password?",
        "answer": (
            "From the login page: click Forgot Password, enter your registered email address, "
            "click the reset link sent to your inbox, and set a new password. "
            "When already logged in: go to Account → Account Settings → Change Password, enter "
            "your current password, then enter and confirm your new password."
        ),
        "category": "faq",
    },
    {
        "question": "How can I track a transaction after sending?",
        "answer": (
            "Go to the History section and select the Ongoing tab to see transfers currently in "
            "progress. You can view the full details including recipient, transaction ID, date, "
            "amount, and status. When the transfer completes, it moves to the Completed tab and "
            "you receive a status update email."
        ),
        "category": "transfer",
    },
    {
        "question": "My transfer has been in Processing for over 24 hours. What should I do?",
        "answer": (
            "A delay beyond 24 hours in Processing can happen for several reasons: banking "
            "holidays in the destination country; correspondent bank delays (international "
            "transfers often pass through intermediate banks); compliance review (the transfer "
            "may have been flagged for a secondary AML check); or recipient bank issues such as "
            "system downtime. If no update after 2 full business days, contact support with your "
            "Transaction ID and request a status investigation."
        ),
        "category": "transfer",
    },
    {
        "question": "Can I send multiple transfers to the same recipient on the same day?",
        "answer": (
            "Yes, as long as your daily transfer count and total daily value limits are not "
            "exceeded. Each transfer is a separate transaction with its own fee. Note that "
            "unusually repetitive transfers to the same recipient in a short period may trigger "
            "an AML review flag."
        ),
        "category": "transfer",
    },
    {
        "question": "Can I add a recipient without immediately sending them money?",
        "answer": (
            "Yes. You can add recipients in advance via the Recipients section without initiating "
            "a transfer. This is useful for setting up beneficiaries ahead of time so transfers "
            "can be sent quickly when needed."
        ),
        "category": "transfer",
    },
    {
        "question": "When will the daily limit be reset?",
        "answer": (
            "Daily limits are reset on a rotating 24-hour basis, at a fixed midnight clock reset."
        ),
        "category": "transfer",
    },
    {
        "question": "I was charged twice for the same transfer. What do I do?",
        "answer": (
            "Check your Transactions history for two entries with the same recipient, amount, "
            "and date within minutes of each other. Note both Transaction IDs. Contact support "
            "immediately via the Help Centre, provide both Transaction IDs, and request a "
            "duplicate payment investigation."
        ),
        "category": "support",
    },
    {
        "question": "Which countries can I send money to?",
        "answer": (
            "The list of supported destination countries is visible on the send money form when "
            "you select a recipient currency. Available corridors depend on regulatory licensing "
            "in both the sending and receiving country, and partner banking and cash agent networks "
            "in the destination."
        ),
        "category": "transfer",
    },
    {
        "question": "What exchange rate will I get? Is it live?",
        "answer": (
            "The exchange rate displayed on the send money form is a live rate fetched at the "
            "time you enter your amount. It reflects the current interbank rate with the portal's "
            "margin applied. The rate shown is the rate you are guaranteed at the moment of "
            "confirmation. If you leave the form open for an extended period, the rate may refresh "
            "before you confirm."
        ),
        "category": "transfer",
    },
    {
        "question": "What is an IBAN and when is it required?",
        "answer": (
            "An IBAN (International Bank Account Number) is a standardised account identifier "
            "used across Europe and many other regions. It is a combination of country code, "
            "check digits, bank code, and account number, typically 15–34 characters long. It is "
            "required when sending to countries in the SEPA zone (most of Europe) and several "
            "other regions. For countries that do not use IBANs (such as the USA or some Asian "
            "countries), a standard account number is used instead."
        ),
        "category": "transfer",
    },
    {
        "question": "My transfer has been on hold for more than 3 days. What should I do?",
        "answer": (
            "Check your email inbox for any compliance request emails — you may have missed a "
            "document request. Check spam too. Log in and check History → Ongoing for any status "
            "messages or action required prompts. If no communication has been received and it "
            "has been more than 3 business days, contact support with your Transaction ID and "
            "request a review status update."
        ),
        "category": "support",
    },
    {
        "question": "What is the trading name field in business registration?",
        "answer": (
            "The trading name is the name your business operates under publicly if different "
            "from its registered legal name. For example, 'ABC Holdings Ltd' may trade as "
            "'ABC Transfers'. Both fields are required for correct business identification and verification."
        ),
        "category": "faq",
    },
    {
        "question": "How far back does my transaction history go?",
        "answer": (
            "All past transfers including failed and cancelled ones remain accessible in your "
            "account. To view older records, scroll through the All tab in the History section. "
            "Records are ordered newest first by default."
        ),
        "category": "faq",
    },
    {
        "question": "Can I download or export my transaction history?",
        "answer": (
            "A full export of your transaction history can be requested through the History page. "
            "The export is typically provided as a CSV or PDF file and includes all transaction "
            "IDs, dates, times, recipients, amounts, statuses, fee breakdown per transaction, "
            "and purpose of transfer entries."
        ),
        "category": "faq",
    },
    {
        "question": "Can the recipient track the transfer from their end?",
        "answer": (
            "Recipients do not have access to the portal unless they have their own account. "
            "You can notify your recipient or send your transfer confirmation email to them so "
            "they have the Transaction ID, expected amount, and estimated delivery time. Once "
            "funds arrive, the recipient's bank will credit the account."
        ),
        "category": "transfer",
    },
    {
        "question": "What is the minimum transfer amount?",
        "answer": (
            "A minimum transfer amount applies to all transactions. The exact minimum varies by "
            "destination currency and corridor. When you enter an amount below the minimum on "
            "the send money form, the system will display an error message and show the minimum "
            "required amount for that specific currency pair."
        ),
        "category": "transfer",
    },
    {
        "question": "Can I get a refund if I simply changed my mind after confirming?",
        "answer": (
            "Unfortunately, change-of-mind refunds are not available once a transfer has been "
            "confirmed and entered Processing. If the transfer is still in Pending status, "
            "contact support immediately — cancellation may still be possible at that stage."
        ),
        "category": "transfer",
    },
    {
        "question": "What happens to a transfer if the recipient's bank account is closed?",
        "answer": (
            "If the recipient's bank account has been closed, the recipient bank will inform the "
            "account status. The portal will receive the bounce-back and update your transfer "
            "status to Failed. Your funds are then refunded to your portal balance. The timeline "
            "for the bounce-back to reach the portal is typically 2–5 business days."
        ),
        "category": "transfer",
    },
    {
        "question": "Can I schedule a transfer for a future date?",
        "answer": (
            "Scheduled transfers are not currently available as a self-service feature in the "
            "portal. All transfers are processed immediately upon confirmation. You can save the "
            "recipient's details permanently so repeat transfers take only seconds to initiate."
        ),
        "category": "transfer",
    },
    {
        "question": "What does on hold mean? Has my money left my account?",
        "answer": (
            "When a transfer is in On-hold status, it means the system has accepted your transfer "
            "request, but it has not yet been authorised by the admin or passed compliance checks. "
            "The funds are reserved but have not yet been sent."
        ),
        "category": "transfer",
    },
    {
        "question": "What does Failed mean and will I be charged?",
        "answer": (
            "No — you are never charged for a failed transfer. If a transfer fails, the full "
            "amount is returned to your account balance or card, with no deductions. Common "
            "reasons: the recipient's bank account number or IBAN was invalid; the recipient's "
            "bank rejected the payment; AML/compliance block triggered during processing; or the "
            "destination currency or country became temporarily unavailable."
        ),
        "category": "transfer",
    },
    {
        "question": "How do I track my transfer step by step?",
        "answer": (
            "Log in and click History from the home page navigation. Select the Ongoing tab for "
            "transfers in progress, or All for your full history. Find your transfer by recipient "
            "name, amount, or date, then tap/click it to open the detail view. The detail view "
            "shows the current status, transaction ID, timestamps, and fee breakdown. Refresh "
            "periodically or wait for the email notification when the status changes."
        ),
        "category": "transfer",
    },
    {
        "question": "Can I filter or search my transaction history?",
        "answer": (
            "Yes. The History section provides three views: All (complete history), Ongoing (only "
            "transfers currently in Pending or Processing state), and Completed (only successfully "
            "delivered transfers). Within each view you can scroll to locate a specific transfer "
            "by recipient name, date, or number."
        ),
        "category": "faq",
    },
    {
        "question": "What is a transaction ID and where do I find it?",
        "answer": (
            "The Transaction ID is a unique code assigned to every transfer the moment you click "
            "Confirm & Send. You can find it on the confirmation screen immediately after sending, "
            "in the transfer confirmation email sent to your inbox, and in the History section on "
            "every transaction record."
        ),
        "category": "faq",
    },
    {
        "question": "The transfer shows Completed but the recipient has not received the money. Why?",
        "answer": (
            "Completed means the funds have left the portal and been delivered to the recipient's "
            "bank. However, the recipient's bank may take additional time to credit the funds. "
            "Ask the recipient to check with their bank. Confirm the account number and bank "
            "details used were correct. If the recipient's bank confirms no credit received "
            "within 2 business days of the Completed status, contact portal support with the "
            "Transaction ID and request a payment trace."
        ),
        "category": "transfer",
    },
    {
        "question": "I sent money to the wrong account. Can I get it back?",
        "answer": (
            "Act immediately — speed is critical. Contact support immediately via the Help Centre "
            "live chat or helpline. Provide your Transaction ID, the incorrect recipient details, "
            "and the correct recipient details. If already Processing or Completed, a recall "
            "request is raised with the recipient's bank. Recovery is not guaranteed and depends "
            "on whether the recipient's account holder agrees to return the funds."
        ),
        "category": "support",
    },
    {
        "question": "What does the service fee cover?",
        "answer": (
            "The service fee covers currency conversion and exchange rate processing, and secure "
            "transaction processing and bank network charges."
        ),
        "category": "transfer",
    },
    {
        "question": "Which support resources are accessible from the portal?",
        "answer": (
            "From the portal you can access: live chat (available 24/7 from any page), the Help "
            "Centre, Terms of Service, and Privacy Policy."
        ),
        "category": "support",
    },
    {
        "question": "Can I add notes or a memo to a transfer?",
        "answer": (
            "Yes. The Reference field on the send money form allows you to add a free-text note "
            "or memo. This is optional and appears on your transaction record. The purpose of "
            "transfer field is separate and mandatory."
        ),
        "category": "transfer",
    },
    {
        "question": "Why do I need to provide my phone number during registration?",
        "answer": (
            "Phone verification proves you have access to the number and enables secure contact. "
            "Your verified number is also pre-filled into the nationality and residence section "
            "of your profile, saving you from re-entering it."
        ),
        "category": "faq",
    },
    {
        "question": "Can I register with an email address already used elsewhere?",
        "answer": (
            "Each email address can only be linked to one portal account. If the email is already "
            "in use, the system will inform you. Use the Forgot Password option on the login page "
            "to recover that existing account."
        ),
        "category": "faq",
    },
    {
        "question": "Is there an age requirement to register?",
        "answer": (
            "Yes, you must be 18 or older to open an account. The system may request identity "
            "verification during KYC to confirm age."
        ),
        "category": "faq",
    },
    {
        "question": "How long does registration take?",
        "answer": (
            "Typically 5–10 minutes if you have your details ready. OTP delivery usually takes "
            "under 2 minutes. Business accounts take slightly longer due to additional profile sections."
        ),
        "category": "faq",
    },
    {
        "question": "Can I save my registration progress and come back later?",
        "answer": (
            "Registration must be completed in a single session. If you close the page mid-way, "
            "you will need to start again. Have your details ready before starting."
        ),
        "category": "faq",
    },
    {
        "question": "Do I need to verify my identity before using the portal?",
        "answer": (
            "Basic registration allows limited use. To unlock full capabilities and higher limits, "
            "you must complete KYC verification via Account → Account Settings → KYC Verification."
        ),
        "category": "compliance",
    },
    {
        "question": "My password reset link has expired. What do I do?",
        "answer": (
            "Return to the login page, click Forgot Password again, and request a new reset link. "
            "Each new request invalidates the previous link."
        ),
        "category": "faq",
    },
    {
        "question": "Can I stay logged in across browser sessions?",
        "answer": (
            "Yes. The refresh cookie keeps your session active for several days. You will only be "
            "asked to log in again when this cookie expires or you explicitly log out."
        ),
        "category": "faq",
    },
    {
        "question": "Why was I logged out automatically?",
        "answer": (
            "Automatic logout happens when your session token expires, when you log out from "
            "another device, or after a password change that invalidates all active sessions. "
            "Log in again normally to continue."
        ),
        "category": "faq",
    },
    {
        "question": "Can I be logged in on multiple devices simultaneously?",
        "answer": (
            "Yes. The portal supports concurrent sessions. Each session has an independent access "
            "token. Logging out on one device does not affect other sessions."
        ),
        "category": "faq",
    },
    {
        "question": "I am blocked out and cannot access my email. What can I do?",
        "answer": (
            "Contact support directly via the Help Centre using an alternative contact method. "
            "They will verify your identity through secondary means (account details, KYC "
            "documents, or registered phone) and help you regain access."
        ),
        "category": "support",
    },
    {
        "question": "Can I save a recipient for future transfers?",
        "answer": (
            "Yes. Once you add a recipient and complete a transfer, their details are saved to "
            "your Recipients list. Future transfers require no re-entry — simply select the "
            "recipient and enter the new amount."
        ),
        "category": "transfer",
    },
    {
        "question": "Can I edit a saved recipient's details?",
        "answer": (
            "Yes. Go to Recipients, select the vertical menu, and choose Edit. Changes apply to "
            "future transfers only. Previous transaction records retain the details at the time of sending."
        ),
        "category": "transfer",
    },
    {
        "question": "Can I delete a saved recipient?",
        "answer": (
            "Yes. Go to Recipients, select the vertical menu, and choose Delete. Past transactions "
            "remain in your history unaffected."
        ),
        "category": "transfer",
    },
    {
        "question": "Can I send to multiple recipients in one transaction?",
        "answer": (
            "No. Each transaction is a single transfer to one recipient. Initiate separate "
            "transfers for each recipient."
        ),
        "category": "transfer",
    },
    {
        "question": "What if I enter the wrong amount?",
        "answer": (
            "Correct it before clicking Confirm & Send on the review screen. After confirmation "
            "the amount cannot be changed. Contact support immediately if the transfer is still Pending."
        ),
        "category": "transfer",
    },
    {
        "question": "How do I know my transfer was successful?",
        "answer": (
            "You receive a confirmation email with full transfer details and a Transaction ID "
            "immediately after confirming. The transfer also appears in History → Ongoing and "
            "moves to Completed with a second email when delivered."
        ),
        "category": "transfer",
    },
    {
        "question": "Can I track a transfer without logging in?",
        "answer": (
            "No. Real-time status requires authentication. However, you receive automatic email "
            "notifications when a transfer reaches Completed or Failed, so you can stay informed "
            "without actively logging in."
        ),
        "category": "transfer",
    },
    {
        "question": "Do I need to re-verify my KYC if documents expire?",
        "answer": (
            "Yes. If your identity document expires during KYC verification, you will need to "
            "upload a new valid document. The portal will notify you in advance and prompt you to update."
        ),
        "category": "compliance",
    },
    {
        "question": "What happens to cookies when I log out?",
        "answer": (
            "The server invalidates all active tokens immediately. Even if someone copied the "
            "raw cookie values before logout, those values become useless instantly."
        ),
        "category": "faq",
    },
    {
        "question": "How can I avoid session expiry mid-transfer?",
        "answer": (
            "Have all details ready before starting. Complete the full transfer in one sitting "
            "(3–5 minutes). Avoid leaving the form open while switching to other tabs for "
            "extended periods."
        ),
        "category": "faq",
    },
    {
        "question": "Can my session extend automatically without logging out?",
        "answer": (
            "Yes. The browser interceptor monitors your access token and uses the refresh token "
            "to renew it silently before it expires. As long as the refresh token is valid and "
            "you remain active, the session continues indefinitely."
        ),
        "category": "faq",
    },
    {
        "question": "How do I close my account?",
        "answer": (
            "Contact support via the Help Centre. Download your transaction history before closure "
            "as access to records is lost after the account is closed."
        ),
        "category": "support",
    },
    {
        "question": "Can I temporarily disable my account?",
        "answer": (
            "Contact support to request a temporary freeze; for example, if you suspect "
            "unauthorised access and want to protect your account while you regain control."
        ),
        "category": "support",
    },
    {
        "question": "How do I update my registered email address?",
        "answer": (
            "Email changes require identity verification and are processed by support. Contact "
            "the Help Centre with your current registered details and provide the new email "
            "address. A verification step for the new email is required."
        ),
        "category": "support",
    },
    {
        "question": "How do I find out why my transfer was blocked?",
        "answer": (
            "A notification email is sent immediately with the specific reason. Check the "
            "transaction detail in History → All for any status messages. Contact the Help Centre "
            "with your Transaction ID if unclear."
        ),
        "category": "support",
    },
    {
        "question": "Can a blocked transfer be resubmitted?",
        "answer": (
            "Yes. Once the underlying issue is resolved, correct the problem and then initiate "
            "a new transfer. Blocked transfers are not automatically retried."
        ),
        "category": "transfer",
    },
    {
        "question": "Will the recipient be charged fees by their bank?",
        "answer": (
            "The portal fee covers the sending side only. The recipient's bank may charge "
            "incoming transfer fees, currency conversion fees, or correspondent bank fees. "
            "The portal cannot control or predict third-party bank charges."
        ),
        "category": "transfer",
    },
    {
        "question": "Do fees vary by destination country?",
        "answer": (
            "Yes. Service fees vary by destination country, currency corridor, and transfer "
            "amount. The applicable fee is always shown before confirmation on the send money form."
        ),
        "category": "transfer",
    },
    {
        "question": "How does the exchange rate affect how much the recipient gets?",
        "answer": (
            "The rate determines how many units of destination currency the recipient receives. "
            "The rate is locked in at the moment of confirmation and shown in full on the review "
            "screen — no surprises after you confirm."
        ),
        "category": "transfer",
    },
    {
        "question": "What if the recipient's country does not use IBANs?",
        "answer": (
            "Countries without IBANs (such as the USA or India) use alternative identifiers, "
            "typically a routing number plus account number, or a BSB code. The send money form "
            "shows the correct fields based on the destination country selected."
        ),
        "category": "transfer",
    },
    {
        "question": "How does the portal protect my account?",
        "answer": (
            "Multiple layers: email OTP on every login, HttpOnly session cookies inaccessible "
            "to JavaScript, automatic session expiry, account lockout after 3 failed attempts, "
            "and AML monitoring to detect unusual patterns."
        ),
        "category": "compliance",
    },
    {
        "question": "What should I do if I think my account has been hacked?",
        "answer": (
            "Change your password immediately via Forgot Password. Contact support live chat "
            "and report the breach. The team will review activity, freeze suspicious transfers, "
            "and help you secure the account."
        ),
        "category": "support",
    },
    {
        "question": "Will the portal ever ask for my password via email?",
        "answer": (
            "Never. The portal will never ask for your password, full OTP, or card details via "
            "email, phone, or chat. If you receive such a request, it is a phishing attempt — "
            "report it to support immediately."
        ),
        "category": "compliance",
    },
    {
        "question": "How do I recognise a phishing email from the portal?",
        "answer": (
            "Legitimate portal emails come from the official registered sender address only and "
            "never ask for your password, OTP, or card number. If you receive a suspicious email "
            "with unexpected links or urgent requests, do not click anything and forward it to support."
        ),
        "category": "compliance",
    },
    {
        "question": "Is my financial data stored securely?",
        "answer": (
            "Yes. All financial and personal data is encrypted at rest and in transit. Session "
            "credentials are HttpOnly cookies inaccessible to JavaScript. The portal operates "
            "under financial regulatory data protection requirements."
        ),
        "category": "compliance",
    },
    {
        "question": "What is two-factor authentication 2FA?",
        "answer": (
            "2FA requires two separate verification steps — something you know (your password) "
            "and something you have (access to your email for the OTP). The portal requires 2FA "
            "on every login, making stolen passwords alone insufficient."
        ),
        "category": "faq",
    },
    {
        "question": "Can I use the portal on a mobile device?",
        "answer": (
            "Yes. The portal is accessible via any modern mobile browser with a responsive "
            "interface. We also have a mobile application for a better experience."
        ),
        "category": "faq",
    },
    {
        "question": "What browsers are supported?",
        "answer": (
            "All modern browsers including Chrome, Firefox, and Edge in current and previous "
            "major versions. Internet Explorer is not supported. Keep your browser updated for "
            "the best experience."
        ),
        "category": "faq",
    },
    {
        "question": "What should I do if the portal is not loading?",
        "answer": (
            "Refresh the page. Clear your browser cache and cookies. Try a different browser or "
            "device. Check your internet connection. If the issue persists across devices, the "
            "portal may be under maintenance — contact support."
        ),
        "category": "support",
    },
    {
        "question": "What happens during portal maintenance?",
        "answer": (
            "During scheduled maintenance some or all functions may be temporarily unavailable. "
            "A notice is displayed in advance. Transfers already in progress are not affected — "
            "they continue on the banking side."
        ),
        "category": "faq",
    },
    {
        "question": "Is there a mobile app available?",
        "answer": (
            "App availability depends on the portal's current product offerings and your region. "
            "Check Account → Support & Legal → Help Centre or the portal's official website for "
            "the latest mobile app information."
        ),
        "category": "faq",
    },
    {
        "question": "What language does the portal support?",
        "answer": (
            "The default language is English. Additional language support may be available "
            "depending on your region. Check the settings or footer for language selection options."
        ),
        "category": "faq",
    },
    {
        "question": "Can I use the portal without accepting cookies?",
        "answer": (
            "Essential session cookies are always active — without them login and transfers are "
            "impossible. Analytics and preference cookies may be optional and can be declined "
            "in cookie consent settings."
        ),
        "category": "faq",
    },
    {
        "question": "How many recipients can I save on my account?",
        "answer": (
            "There is no fixed cap on saved recipients. You can add as many bank account "
            "recipients and cash pickup collectors as needed."
        ),
        "category": "transfer",
    },
    {
        "question": "Can two different recipients share the same bank account number?",
        "answer": (
            "The system allows the same bank account number to be saved under different recipient "
            "profiles — for example, a shared account held by two family members. Each profile "
            "is tracked independently with its own name and contact details."
        ),
        "category": "transfer",
    },
    {
        "question": "Can I send to a recipient in a different currency than their bank account?",
        "answer": (
            "Yes. The portal handles currency conversion automatically. You send in your currency, "
            "the portal converts at the live rate, and the recipient receives the converted amount."
        ),
        "category": "transfer",
    },
    {
        "question": "What details differ for a cash pickup recipient vs a bank recipient?",
        "answer": (
            "Cash pickup recipients require an ID proof type and ID proof number instead of bank "
            "name and account number. A postal or ZIP code is also required. All other contact "
            "fields (name, email, phone, country, and address) are the same as bank recipients."
        ),
        "category": "transfer",
    },
    {
        "question": "What should I do if a recipient's bank details change?",
        "answer": (
            "Go to Recipients, select the recipient, and choose Edit. Update the bank name, "
            "branch, and account number or IBAN. Save before initiating the next transfer. "
            "Sending to outdated details can cause misdirected payments."
        ),
        "category": "transfer",
    },
    {
        "question": "Can a business account send to personal recipients?",
        "answer": (
            "Yes. Business accounts can send to both personal bank account recipients and cash "
            "pickup collectors. The purpose of transfer must accurately reflect the nature of the payment."
        ),
        "category": "faq",
    },
    {
        "question": "Can I use my business account for personal transfers?",
        "answer": (
            "Business accounts are intended for business-related transfers. Using one for "
            "personal remittances may trigger a compliance review. For regular personal transfers, "
            "maintain a separate personal account."
        ),
        "category": "compliance",
    },
    {
        "question": "What counts as acceptable proof of address?",
        "answer": (
            "A recent bank statement, utility bill, official government letter, or tenancy "
            "agreement — all dated within the last 3 months. The document must show your full "
            "name and current resident address matching your profile."
        ),
        "category": "compliance",
    },
    {
        "question": "My KYC document was rejected. What are the common reasons?",
        "answer": (
            "Common rejection reasons: the document is expired; the photo is blurry or has glare; "
            "part of the document is cropped; the name does not exactly match your registered "
            "profile; or the document type is not accepted in your region. Re-upload a clear, "
            "full-frame photo under good lighting."
        ),
        "category": "compliance",
    },
    {
        "question": "Can I submit KYC documents in a language other than English?",
        "answer": (
            "Passports and national IDs in local languages are generally accepted as "
            "internationally recognised documents. If in a non-Latin script, a certified "
            "translation may be requested. Contact support to confirm requirements for your "
            "specific document language."
        ),
        "category": "compliance",
    },
    {
        "question": "How do I know if my KYC has been approved?",
        "answer": (
            "You receive a confirmation email when approved. Your KYC status also updates to "
            "Verified under Account → Account Settings → KYC Verification. If still showing "
            "Pending after 2 business days, contact support for an update."
        ),
        "category": "compliance",
    },
    {
        "question": "Do I need to reverify KYC if I change my address?",
        "answer": (
            "Address changes generally do not require full KYC re-verification, but you should "
            "update your address and contact support to confirm for your account tier."
        ),
        "category": "compliance",
    },
    {
        "question": "Can I turn off the transfer confirmation emails?",
        "answer": (
            "No. Transfer confirmation emails are mandatory transactional communications required "
            "by financial regulations. They serve as official records. Security alerts are also non-optional."
        ),
        "category": "faq",
    },
    {
        "question": "Who can see my transaction history?",
        "answer": (
            "Your history is private and only accessible when you are authenticated. The "
            "compliance team can access records as required by law for AML reviews and regulatory "
            "reporting. Your data is never sold or shared for marketing purposes."
        ),
        "category": "compliance",
    },
    {
        "question": "Does the portal share my data with third parties?",
        "answer": (
            "Only as necessary: with correspondent banks to process transfers, with regulatory "
            "and legal authorities under valid legal orders, and with identity verification "
            "providers during KYC. Your data is never sold or shared for advertising purposes."
        ),
        "category": "compliance",
    },
    {
        "question": "Is my data protected under GDPR?",
        "answer": (
            "If you are based in the EU or UK, your data is protected under GDPR or UK GDPR "
            "respectively. Users in other regions are protected by equivalent local data "
            "protection laws. The Privacy Policy details your rights and how data is processed."
        ),
        "category": "compliance",
    },
    {
        "question": "How does cash pickup work for the recipient?",
        "answer": (
            "Once a cash pickup transfer reaches Completed status, the recipient visits a partner "
            "agent location in the destination country and presents the same ID proof type and "
            "number registered in their recipient profile. No bank account is required."
        ),
        "category": "transfer",
    },
    {
        "question": "Can the recipient send someone else to collect on their behalf?",
        "answer": (
            "Cash pickup is identity-verified — the collector must present the exact ID proof "
            "number registered in the recipient profile. Third-party collection is generally not "
            "permitted unless the destination agent network has a specific authorisation process. "
            "Check with the local agent for their policy."
        ),
        "category": "transfer",
    },
    {
        "question": "What if the recipient loses the ID they used to register?",
        "answer": (
            "If the recipient cannot present the registered ID, they cannot collect. You would "
            "need to update the recipient profile with their new valid ID details and re-initiate "
            "the transfer. Contact support for guidance on any active transfer in this situation."
        ),
        "category": "support",
    },
    {
        "question": "How do I raise a formal complaint about a transfer?",
        "answer": (
            "Go to Account → Support & Legal → Help Centre and submit a complaint. Provide your "
            "Transaction ID, a clear description of the issue, and any supporting evidence. The "
            "portal must acknowledge your complaint within a defined timeframe and resolve it "
            "within the regulatory period for your region."
        ),
        "category": "support",
    },
    {
        "question": "Can I transfer money to myself for example my own foreign bank account?",
        "answer": (
            "Yes. Add yourself as a recipient with your foreign bank account details. This is "
            "common for people managing accounts in multiple countries. The same fees and "
            "compliance requirements apply."
        ),
        "category": "transfer",
    },
    {
        "question": "Is the portal a licensed financial service provider?",
        "answer": (
            "Yes. The portal operates under financial services licenses granted by relevant "
            "regulatory authorities in each country where it operates. License details are "
            "available in the Terms of Service under Account → Support & Legal."
        ),
        "category": "compliance",
    },
    {
        "question": "What regulations govern the portal?",
        "answer": (
            "The portal is subject to AML laws, counter-terrorism financing (CTF) regulations, "
            "data protection laws (GDPR, CCPA, and regional equivalents), and payment services "
            "directives in each jurisdiction it operates. The Terms of Service details the "
            "specific regulatory framework."
        ),
        "category": "compliance",
    },
    {
        "question": "What happens if I am flagged on a sanction list?",
        "answer": (
            "If your name, nationality, or destination matches a sanction list (such as OFAC or "
            "the UN Security Council list), the portal is legally prohibited from processing the "
            "transfer. Your account may be restricted pending a manual review. Contact support for guidance."
        ),
        "category": "compliance",
    },
    {
        "question": "Can I use VPN while accessing the portal?",
        "answer": (
            "VPN usage may interfere with IP-based security signals the portal uses to detect "
            "suspicious logins. Some VPN IPs may trigger additional verification steps. If you "
            "experience login issues while on a VPN, disconnect and access the portal directly."
        ),
        "category": "faq",
    },
    {
        "question": "The recipient says they received less than expected. What should I do?",
        "answer": (
            "Ask the recipient to check whether their bank deducted an incoming fee or applied "
            "their own currency conversion. Compare the received amount against the 'they receive' "
            "figure on your confirmation. If there is an unexplained discrepancy beyond normal "
            "bank fees, contact support with the Transaction ID."
        ),
        "category": "support",
    },
    {
        "question": "What should I do immediately after registering?",
        "answer": (
            "Complete your KYC verification to unlock full transfer capabilities. Add your first "
            "recipient under the Recipients section. Review the send money steps in the Help "
            "Centre before your first transfer to ensure a smooth experience."
        ),
        "category": "faq",
    },
    {
        "question": "Can I test the portal with a small transfer before sending a large amount?",
        "answer": (
            "Yes. Sending a small test transfer first is a sensible approach with a new recipient. "
            "Confirm delivery before sending larger amounts. All transfers including small test "
            "amounts attract the standard fee for the corridor."
        ),
        "category": "transfer",
    },
    {
        "question": "Can support access my account without my permission?",
        "answer": (
            "Authorised staff can access account records (profile data, transaction history, KYC "
            "status) to resolve your query or conduct a compliance review. They cannot initiate "
            "transfers, change your password, or modify your profile without your explicit instruction."
        ),
        "category": "compliance",
    },
    {
        "question": "Is support available on weekends and public holidays?",
        "answer": (
            "Live chat is available 24/7 including weekends and public holidays. Compliance review "
            "and KYC processing may be limited to business days. Weekend email responses may take "
            "slightly longer than the standard 1 business day target."
        ),
        "category": "support",
    },
    {
        "question": "How often does the exchange rate update?",
        "answer": (
            "Rates update in real time, typically every few seconds to minutes depending on "
            "market conditions and the currency pair. The rate shown when you enter your amount "
            "is live. The rate at the moment of confirmation is the rate applied to your transfer."
        ),
        "category": "transfer",
    },
    {
        "question": "Can I lock in an exchange rate before confirming?",
        "answer": (
            "The rate is locked at the moment of confirmation. There is no advance rate-locking "
            "or forward contract feature currently available as self-service. If you are concerned "
            "about rate movements, complete the transfer promptly after reviewing the amount."
        ),
        "category": "transfer",
    },
    {
        "question": "Is the exchange rate the same for all transfer amounts?",
        "answer": (
            "The rate margin may vary for very large or very small amounts depending on the "
            "corridor. Some corridors offer improved rates above certain thresholds. The rate "
            "applicable to your specific amount is always shown before you confirm."
        ),
        "category": "transfer",
    },
    {
        "question": "Will a payment reference appear on the recipient's bank statement?",
        "answer": (
            "The reference field allows you to enter a reference. Whether it appears on the "
            "recipient's statement depends on their bank and the correspondent network used for "
            "that corridor. Not all banks pass payment references through."
        ),
        "category": "transfer",
    },
    {
        "question": "What is the difference between transfer amount and total to pay?",
        "answer": (
            "The transfer amount is what you enter as the send amount — the money converted and "
            "sent. The total to pay is the transfer amount plus the service fee, shown as a "
            "single clear figure before you confirm."
        ),
        "category": "transfer",
    },
    {
        "question": "What should I do if I forget which email I registered with?",
        "answer": (
            "Try email addresses you commonly use on the login page. If you cannot identify the "
            "registered email, contact support with your full name, registered phone number, and "
            "date of birth. The team can locate your account after identity verification."
        ),
        "category": "support",
    },
    {
        "question": "What happened if I enter an invalid email during registration?",
        "answer": (
            "You are asked to correct it before continuing. The system will not allow registration "
            "to proceed with an invalid email format."
        ),
        "category": "faq",
    },
]


def extract_keywords(question: str, answer: str) -> list[str]:
    """Extract simple lowercase keywords from question + answer for fast matching."""
    import re

    text = (question + " " + answer).lower()
    # Remove punctuation, split, deduplicate, filter short words
    words = re.findall(r"[a-z]{3,}", text)
    stopwords = {
        "the",
        "and",
        "for",
        "are",
        "you",
        "your",
        "this",
        "that",
        "with",
        "have",
        "has",
        "been",
        "from",
        "they",
        "will",
        "not",
        "can",
        "may",
        "any",
        "all",
        "also",
        "more",
        "only",
        "when",
        "once",
        "then",
        "than",
        "their",
        "them",
        "into",
        "over",
        "after",
        "before",
        "during",
        "such",
        "each",
        "both",
        "these",
        "those",
        "which",
        "what",
        "how",
        "why",
        "who",
        "where",
        "our",
        "its",
        "via",
        "per",
        "but",
        "out",
        "use",
        "used",
        "make",
        "made",
        "get",
        "one",
        "two",
        "three",
        "new",
    }
    return list({w for w in words if w not in stopwords})


def seed():
    print(f"Connecting to MongoDB: {MONGO_URI[:50]}...")
    client = MongoClient(MONGO_URI)
    db = client["ajeer_db"]

    try:
        client.admin.command("ping")
        print("✓ Connected to MongoDB")
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        return

    col = db["faq_kb"]

    # Drop existing and rebuild (clean seed)
    col.drop()
    print("✓ Dropped existing faq_kb collection")

    # Create text index for full-text search
    col.create_index([("question", TEXT), ("answer", TEXT)], name="text_search")
    col.create_index([("keywords", ASCENDING)], name="keywords_idx")
    col.create_index([("category", ASCENDING)], name="category_idx")
    print("✓ Indexes created on faq_kb")

    # Build documents
    docs = []
    for item in FAQ_DATA:
        docs.append(
            {
                "question": item["question"],
                "answer": item["answer"],
                "category": item["category"],
                "keywords": extract_keywords(item["question"], item["answer"]),
                "created_at": datetime.utcnow(),
            }
        )

    col.insert_many(docs)
    print(f"✓ Seeded {len(docs)} FAQ documents into faq_kb collection")

    # Summary by category
    for cat in ["faq", "transfer", "compliance", "support"]:
        count = col.count_documents({"category": cat})
        print(f"   {cat}: {count} entries")

    print("\n✅ FAQ seeding complete!")
    client.close()


if __name__ == "__main__":
    seed()

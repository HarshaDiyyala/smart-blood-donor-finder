# Firebase Firestore Setup

The app can optionally mirror new and changed records to Cloud Firestore. SQL remains the primary database for application reads; Firebase credentials are used only by the Flask server and must never be placed in browser code or committed to this repository.

## Configure Firebase

1. Create or select a Firebase project and create its Cloud Firestore database.
2. In the Firebase project settings, open **Service accounts** and generate a private key, or configure Google Application Default Credentials on the machine running Flask.
3. Store any downloaded service-account JSON outside this repository. Do not commit it.
4. Set the project ID and, when using a service-account key, its full path in the PowerShell session that starts Flask:

```powershell
$env:FIREBASE_PROJECT_ID = "your-firebase-project-id"
$env:FIREBASE_SERVICE_ACCOUNT = "C:\secure\firebase-service-account.json"
```

To use Application Default Credentials instead, set only the project ID and leave `FIREBASE_SERVICE_ACCOUNT` unset. The `.env.example` file documents these variables but is not automatically loaded by the app.

Start Flask from that same PowerShell session. If `FIREBASE_PROJECT_ID` is not set, Firebase sync is disabled and the app continues using SQL only. If Firebase is temporarily unreachable, SQL writes still succeed and the failure is logged by the server.

## Synced Collections

- `donors`: donor profile fields only; password hashes are never copied.
- `blood_requests`: submitted blood requests, keyed by their SQL record ID.
- `contact_messages`: contact submissions, including blood-camp proposals, keyed by their SQL record ID.

Existing SQL records are not automatically backfilled. Firestore is a write mirror, not the source used to populate current pages.
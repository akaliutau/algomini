Create one GitHub App under the account or organization that owns this repository. It will provide the bot identity.

1. Register the App

   Open GitHub account settings—or organization settings—then Developer settings → GitHub Apps → New GitHub App.

   Use:
   - Name: something unique, such as algomini-yourname.
   - Homepage: your repository URL.
   - Webhooks: disable Active.
   - OAuth callback/setup URLs: leave blank.
   - Installation scope: Only on this account.

   Our workflow uses installation tokens, so it needs no OAuth client secret or webhook server. Registration instructions.
2. Set repository permissions

   | Permission | Access |
   |---|---|
   | Contents | Read and write |
   | Pull requests | Read and write |
   | Issues | Read and write |
   | Metadata | Read, automatically included |

   Leave other permissions unset.
3. Install it

   After creation, open Install App, select the repository owner, then choose Only select repositories and select algomini. Registration alone does not grant repository access. Installation instructions.
4. Copy the client ID and generate a key

   Copy the App’s Client ID, which differs from its numeric App ID.

   In the App settings, open Credentials → Key pairs → New key. GitHub downloads a .pem file. Keep that file outside the repository. Private-key instructions.
5. Prepare local .env

   Keep the existing Meta key and add:

   META_API_KEY=your-existing-key
ALGOMINI_APP_CLIENT_ID=your-client-id
ALGOMINI_APP_PRIVATE_KEY_PATH=/absolute/path/to/app.private-key.pem

   The key-path variable is the proposed local configuration for the upcoming tooling. Never commit .env or the PEM file.
6. Configure GitHub Actions separately

   In the repository, open Settings → Secrets and variables → Actions:

   | Name | Location | Value |
   |---|---|---|
   | ALGOMINI_APP_CLIENT_ID | Variables | Client ID |
   | ALGOMINI_APP_PRIVATE_KEY | Secrets | Entire PEM file contents, including BEGIN/END lines |
   | META_API_KEY | Secrets | Existing Meta API key |

   GitHub Actions does not inherit your local .env. Its private-key secret contains the actual key text, rather than a local file path. The workflow will exchange these credentials for short-lived installation tokens. GitHub’s Actions authentication guide.

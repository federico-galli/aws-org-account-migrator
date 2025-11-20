# Account Migration Batch Script

This script automates the process of migrating multiple AWS accounts from a source organization to a target organization and placing them into a specific Organizational Unit (OU).

## How It Works

The script performs the following sequence of operations for each account specified in the input CSV file:

1.  **Adds a temporary trust** to the account's `OrganizationAccountAccessRole` for the target organization.
2.  **Removes the account** from the source organization.
3.  **Invites the account** to the target organization.
4.  **Accepts the invitation** on behalf of the account.
5.  **Finalizes the trust relationship** to only trust the new target organization.
6.  **Moves the account** to the specified target OU.

## Prerequisites

*   Python 3
*   `boto3` (installable via `requirements.txt`)
*   AWS CLI with configured profiles

## Setup

### 1. Install Dependencies

Navigate to this directory and run:

```bash
pip install -r requirements.txt
```

### 2. Authentication

This script uses named AWS profiles to authenticate to the source and target AWS organizations. You must have credentials configured for the management account of both organizations.

The `--source-profile` argument should correspond to the AWS profile for the source organization's management account, and `--target-profile` for the target's. Various methods can be used to configure these AWS profiles, including those demonstrated in the examples below.

**Example using Static Credentials:**

You can configure static credentials in your `~/.aws/credentials` file or by setting environment variables.

`~/.aws/credentials`:
```ini
[<your-source-profile>]
aws_access_key_id = YOUR_SOURCE_AWS_ACCESS_KEY_ID
aws_secret_access_key = YOUR_SOURCE_AWS_SECRET_ACCESS_KEY

[<your-target-profile>]
aws_access_key_id = YOUR_TARGET_AWS_ACCESS_KEY_ID
aws_secret_access_key = YOUR_TARGET_AWS_SECRET_ACCESS_KEY
```

Environment Variables:
```bash
export AWS_ACCESS_KEY_ID=YOUR_ACCESS_KEY
export AWS_SECRET_ACCESS_KEY=YOUR_SECRET_KEY
export AWS_DEFAULT_REGION=your-aws-region
```

**Example using AWS SSO:**

If you use AWS Single Sign-On (SSO), you can configure your profiles in `~/.aws/config`. The script will automatically use the credentials managed by the AWS CLI.

```ini
[profile <your-source-profile>]
sso_start_url = https://<your-sso-start-url>.awsapps.com/start
sso_region = <your-sso-region>
sso_account_id = <SOURCE_MGMT_ACCT_ID>
sso_role_name = <your-sso-role-name>
region = <your-aws-region>

[profile <your-target-profile>]
sso_start_url = https://<your-sso-start-url>.awsapps.com/start
sso_region = <your-sso-region>
sso_account_id = <TARGET_MGMT_ACCT_ID>
sso_role_name = <your-sso-role-name>
region = <your-aws-region>
```

**Example using `okta-aws-cli`:**

The following are examples. You must update the application ID, OIDC client ID, account numbers, and role ARNs for your specific source and destination organizations.

To generate credentials for the SOURCE organization:

```bash
AWS_REGION=<your-aws-region> okta-aws-cli \
    --aws-acct-fed-app-id <SOURCE_APP_ID> \
    --oidc-client-id <SOURCE_OIDC_ID> \
    --org-domain <your-okta-domain> \
    --format aws-credentials \
    --write-aws-credentials \
    --profile <your-source-profile> \
    --aws-iam-idp arn:aws:iam::<SOURCE_MGMT_ACCT_ID>:saml-provider/<your-saml-provider> \
    --aws-iam-role arn:aws:iam::<SOURCE_MGMT_ACCT_ID>:role/<your-iam-role> \
    --open-browser
```

To generate credentials for the TARGET organization:

```bash
AWS_REGION=<your-aws-region> okta-aws-cli \
    --aws-acct-fed-app-id <TARGET_APP_ID> \
    --oidc-client-id <TARGET_OIDC_ID> \
    --org-domain <your-okta-domain> \
    --format aws-credentials \
    --write-aws-credentials \
    --profile <your-target-profile> \
    --aws-iam-idp arn:aws:iam::<TARGET_MGMT_ACCT_ID>:saml-provider/<your-saml-provider> \
    --aws-iam-role arn:aws:iam::<TARGET_MGMT_ACCT_ID>:role/<your-iam-role> \
    --open-browser
```

Before running the script, ensure your profiles have active credentials.

### 3. CSV File Format

Create a file named `accounts.csv`. This file must contain a header row with the label `account_id`, followed by the list of AWS account IDs to migrate.

**Example `accounts.csv`:**

```csv
account_id
111122223333
444455556666
777788889999
```

## Usage

Modify the `run.sh` script with your specific profile names and target OU ID, and populate the `accounts.csv` file with the account IDs to be migrated.

```bash
#!/bin/bash
./batch_migration.py \
    --csv-file accounts.csv \
    --source-profile <your-source-profile> \
    --target-profile <your-target-profile> \
    --target-ou-id <your-target-ou-id> \
    --max-failures 3 \
    --log-file migration_errors.log
```

Then, execute the script:

```bash
bash run.sh
```

## Error Handling

*   **Log File:** Any errors that occur during the migration will be logged to the file specified by `--log-file` (default: `migration_errors.log`). This log is crucial for diagnosing and manually recovering any accounts that fail mid-migration.
*   **Max Failures:** The script will automatically stop if the number of failed migrations reaches the value set by `--max-failures` (default: 3). This is a safety mechanism to prevent widespread issues.

## :warning: Important Warning

A failure during the migration process can leave an AWS account in an "orphaned" state, where it does not belong to any organization. While recovering an orphaned account requires understanding the migration process, reading the provided logs, and manually restarting from the point of failure, it is a manageable process. This script has been successfully tested with hundreds of accounts. Use this script with caution and ensure your authentication and parameters are correct before running it.

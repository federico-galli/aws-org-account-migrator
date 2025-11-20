#!/usr/bin/env python3
import boto3
import time
import argparse
import logging
import json
import csv


def setup_logging(log_file):
    """Configures logging to both console and a file."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler()],
    )
    # Create a file handler for errors
    error_handler = logging.FileHandler(log_file)
    error_handler.setLevel(logging.ERROR)
    error_formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - AccountID: %(account_id)s - %(message)s"
    )
    error_handler.setFormatter(error_formatter)
    logging.getLogger().addHandler(error_handler)


def parse_arguments():
    """Parses command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Move AWS accounts in batch from a source to a target organization.",
        epilog="""
        Authentication:
        This script uses AWS profiles for authentication. Ensure you have configured profiles for both the source and target management accounts.
        You can configure profiles using the AWS CLI: `aws configure --profile <profile_name>`
        """,
    )
    parser.add_argument(
        "--csv-file",
        required=True,
        help='Path to the CSV file with account IDs. The CSV should have a header row with "account_id".',
    )
    parser.add_argument(
        "--source-profile",
        required=True,
        help="AWS profile for the source organization management account.",
    )
    parser.add_argument(
        "--target-profile",
        required=True,
        help="AWS profile for the target organization management account.",
    )
    parser.add_argument(
        "--target-ou-id",
        required=True,
        help="The ID of the OU in the target organization to move the accounts to.",
    )
    parser.add_argument(
        "--max-failures",
        type=int,
        default=3,
        help="The maximum number of failed migrations before stopping the script.",
    )
    parser.add_argument(
        "--log-file", default="migration_errors.log", help="The file to log errors to."
    )
    return parser.parse_args()


def add_account_to_trust_relationship(
    target_account_id, role_name, target_org_master_account, profile
):
    """Adds the target organization to the trust relationship of the account's access role."""
    try:
        session = boto3.Session(profile_name=profile)
        CHILD_ACCOUNT_ROLE_ARN = (
            f"arn:aws:iam::{target_account_id}:role/OrganizationAccountAccessRole"
        )
        sts_client = session.client("sts")
        assumed_role_client = sts_client.assume_role(
            RoleArn=CHILD_ACCOUNT_ROLE_ARN, RoleSessionName="AccountMover"
        )
        credentials = assumed_role_client["Credentials"]
        temp_session = boto3.Session(
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
        )
        iam_client = temp_session.client("iam")
        role = iam_client.get_role(RoleName=role_name)
        current_policy = role["Role"]["AssumeRolePolicyDocument"]

        new_statement = {
            "Effect": "Allow",
            "Principal": {"AWS": f"arn:aws:iam::{target_org_master_account}:root"},
            "Action": "sts:AssumeRole",
        }

        # Check if the statement already exists
        account_exists = False
        for statement in current_policy["Statement"]:
            if statement.get("Effect") == "Allow" and "AWS" in statement.get(
                "Principal", {}
            ):
                principal = statement["Principal"]["AWS"]
                if (
                    isinstance(principal, str)
                    and principal == f"arn:aws:iam::{target_org_master_account}:root"
                ):
                    account_exists = True
                    break
                elif (
                    isinstance(principal, list)
                    and f"arn:aws:iam::{target_org_master_account}:root" in principal
                ):
                    account_exists = True
                    break

        if not account_exists:
            current_policy["Statement"].append(new_statement)
            iam_client.update_assume_role_policy(
                RoleName=role_name, PolicyDocument=json.dumps(current_policy)
            )
            logging.info(
                f"Successfully added account {target_org_master_account} to trust relationship for role {role_name}"
            )
        else:
            logging.info(
                f"Account {target_org_master_account} is already in the trust relationship for role {role_name}"
            )
        return True
    except Exception as e:
        logging.error(
            f"Error adding second account to trust relationship: {str(e)}",
            extra={"account_id": target_account_id},
        )
        raise


def remove_from_source_org(account_id, profile):
    """Removes the account from the source organization."""
    logging.info(f"Removing account {account_id} from source organization...")
    session = boto3.Session(profile_name=profile)
    org_client = session.client("organizations")
    try:
        org_client.remove_account_from_organization(AccountId=account_id)
        logging.info(f"Account {account_id} removed from source organization")
    except Exception as e:
        logging.error(f"Error removing account: {e}", extra={"account_id": account_id})
        raise


def invite_to_target_org(account_id, profile):
    """Invites the account to the target organization."""
    logging.info(f"Inviting account {account_id} to target organization...")
    session = boto3.Session(profile_name=profile)
    org_client = session.client("organizations")
    try:
        response = org_client.invite_account_to_organization(
            Target={"Id": account_id, "Type": "ACCOUNT"}
        )
        handshake_id = response["Handshake"]["Id"]
        logging.info(f"Invitation sent. Handshake ID: {handshake_id}")
        return handshake_id
    except Exception as e:
        logging.error(
            f"Error sending invitation: {e}", extra={"account_id": account_id}
        )
        raise


def accept_invitation(
    handshake_id, source_profile_name, target_account_id, target_org_master_account
):
    """Accepts the organization invitation handshake."""
    logging.info(f"Accepting invitation handshake {handshake_id}...")
    CHILD_ACCOUNT_ROLE_ARN = (
        f"arn:aws:iam::{target_account_id}:role/OrganizationAccountAccessRole"
    )
    session = boto3.Session(profile_name=source_profile_name)
    sts_client = session.client("sts")
    try:
        assumed_role_client = sts_client.assume_role(
            RoleArn=CHILD_ACCOUNT_ROLE_ARN, RoleSessionName="AccountMover"
        )
        credentials = assumed_role_client["Credentials"]

        acct_assumed_session = boto3.Session(
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
        )
        client = acct_assumed_session.client("organizations")

        # Wait for the handshake to propagate
        handshake_found = False
        max_retries = 10
        for i in range(max_retries):
            handshakes = client.list_handshakes_for_account(
                Filter={"ActionType": "INVITE"}
            )
            for handshake in handshakes["Handshakes"]:
                if handshake["Id"] == handshake_id:
                    handshake_found = True
                    break
            if handshake_found:
                break
            logging.info(
                f"Waiting for handshake to propagate... (attempt {i+1}/{max_retries})"
            )
            time.sleep(10)

        if not handshake_found:
            raise Exception(
                f"Handshake {handshake_id} not found after {max_retries} attempts"
            )

        client.accept_handshake(HandshakeId=handshake_id)
        logging.info("Invitation accepted. Account has joined the target organization.")

        # Finalize the trust relationship
        replace_role_trust_relationship(
            target_account_id,
            "OrganizationAccountAccessRole",
            target_org_master_account,
            source_profile_name,
        )

    except Exception as e:
        logging.error(
            f"Error accepting invitation: {e}", extra={"account_id": target_account_id}
        )
        raise


def replace_role_trust_relationship(
    target_account_id, role_name, target_org_master_account, profile
):
    """Updates the trust policy of the role to only trust the target organization."""
    try:
        session = boto3.Session(profile_name=profile)
        CHILD_ACCOUNT_ROLE_ARN = (
            f"arn:aws:iam::{target_account_id}:role/OrganizationAccountAccessRole"
        )
        sts_client = session.client("sts")
        assumed_role_client = sts_client.assume_role(
            RoleArn=CHILD_ACCOUNT_ROLE_ARN, RoleSessionName="AccountMover"
        )
        credentials = assumed_role_client["Credentials"]
        temp_session = boto3.Session(
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
        )
        iam_client = temp_session.client("iam")

        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {
                        "AWS": f"arn:aws:iam::{target_org_master_account}:root"
                    },
                    "Action": "sts:AssumeRole",
                }
            ],
        }

        iam_client.update_assume_role_policy(
            RoleName=role_name, PolicyDocument=json.dumps(trust_policy)
        )
        logging.info(f"Successfully replaced trust relationship for role {role_name}")
        return True
    except Exception as e:
        logging.error(
            f"Error updating trust relationship: {str(e)}",
            extra={"account_id": target_account_id},
        )
        raise


def move_to_ou(account_id, ou_id, profile):
    """Moves the account to the specified Organizational Unit."""
    logging.info(f"Moving account {account_id} to OU {ou_id}...")
    session = boto3.Session(profile_name=profile)
    org_client = session.client("organizations")
    try:
        # First, we need to find the root of the organization
        roots = org_client.list_roots()
        root_id = roots["Roots"][0]["Id"]

        org_client.move_account(
            AccountId=account_id, SourceParentId=root_id, DestinationParentId=ou_id
        )
        logging.info(f"Account {account_id} moved to OU {ou_id}")
    except Exception as e:
        logging.error(
            f"Error moving account to OU: {e}", extra={"account_id": account_id}
        )
        raise


def main():
    args = parse_arguments()
    setup_logging(args.log_file)

    accounts_to_migrate = []
    with open(args.csv_file, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if "account_id" in row:
                accounts_to_migrate.append(row["account_id"])
            else:
                logging.warning(
                    f"Skipping row in CSV because it does not contain an 'account_id' field: {row}"
                )

    failure_count = 0
    successful_migrations = []
    failed_migrations = []

    session = boto3.Session(profile_name=args.target_profile)
    target_org_master_account = (
        session.client("sts").get_caller_identity().get("Account")
    )

    for account_id in accounts_to_migrate:
        if failure_count >= args.max_failures:
            logging.warning(
                f"Stopping script due to reaching max failures ({args.max_failures})."
            )
            break

        try:
            logging.info(f"Starting migration for account: {account_id}")

            # Step 1: Update IAM role to trust both source & target organizations
            add_account_to_trust_relationship(
                target_account_id=account_id,
                role_name="OrganizationAccountAccessRole",
                target_org_master_account=target_org_master_account,
                profile=args.source_profile,
            )

            # Step 2: Remove account from source organization
            remove_from_source_org(account_id, args.source_profile)

            # Step 3: Wait for the account to be fully removed
            logging.info("Waiting for account removal to complete...")
            time.sleep(30)

            # Step 4: Send invitation from target organization
            handshake_id = invite_to_target_org(account_id, args.target_profile)

            # Step 5: Accept invitation from the child account
            accept_invitation(
                handshake_id=handshake_id,
                source_profile_name=args.source_profile,
                target_account_id=account_id,
                target_org_master_account=target_org_master_account,
            )

            # Step 6: Move account to the target OU
            move_to_ou(account_id, args.target_ou_id, args.target_profile)

            logging.info(
                f"Account {account_id} has been successfully moved to the target organization and OU."
            )
            successful_migrations.append(account_id)

        except Exception as e:
            logging.error(
                f"Failed to move account {account_id}: {e}",
                extra={"account_id": account_id},
            )
            failed_migrations.append(account_id)
            failure_count += 1

    logging.info("\n--- Migration Summary ---")
    logging.info(f"Successful migrations: {len(successful_migrations)}")
    logging.info(f"Failed migrations: {len(failed_migrations)}")
    if failed_migrations:
        logging.info(f"Failed account IDs: {', '.join(failed_migrations)}")
        logging.info(f"Check the log file '{args.log_file}' for detailed errors.")


if __name__ == "__main__":
    main()

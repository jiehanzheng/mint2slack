from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
import mintapi
from tinydb import TinyDB, Query
import os
from collections import defaultdict
import datetime
from datetime import datetime, timedelta
import time


with open("secrets/slack_bot_token.txt") as slack_bot_token_f, open("secrets/slack_app_token.txt") as slack_app_token_f, open("secrets/mint_email.txt") as mint_email_f, open("secrets/mint_password.txt") as mint_password_f, open("secrets/mint_mfa_token.txt") as mint_mfa_token_f:
    slack_bot_token = slack_bot_token_f.read()
    slack_app_token = slack_app_token_f.read()
    mint_email = mint_email_f.read()
    mint_password = mint_password_f.read()
    mint_mfa_token = mint_mfa_token_f.read()

txnsdb = TinyDB("config/txns.json")

app = App(token=slack_bot_token)

session_path = os.path.join(os.path.dirname(__file__), "config/chrome_session")

mint = mintapi.Mint(
    mint_email,
    mint_password,
    mfa_method="soft-token",
    mfa_token=mint_mfa_token,
    headless=True,
    session_path=session_path,
    wait_for_sync=False,
    use_chromedriver_on_path="USE_CHROMEDRIVER_ON_PATH" in os.environ,
)


@app.command("/accts")
def handle_accts_command(ack, respond, command):
    print("Handling accts command...")
    ack()
    accounts_blocks = get_accounts_blocks()
    respond(blocks=accounts_blocks, response_type="in_channel")


@app.event("message")
def handle_message_events(body, logger):
    pass


def post_message(*args, **kwargs):
    result = app.client.conversations_list()
    for channel in result["channels"]:
        if not channel["is_member"]:
            continue

        print(f"Posting to #{channel['name']} ({channel['id']})...")
        app.client.chat_postMessage(*args, **kwargs, channel=channel["id"])


def download_and_persist_and_get_unseen_txns():
    fetch_start_mmddyy = (
        datetime.today() - timedelta(days=14)).strftime("%m/%d/%y")
    fetch_end_mmddyy = datetime.today().strftime("%m/%d/%y")

    api_txns_unsanitized = mint.get_transactions_json(
        start_date=fetch_start_mmddyy, end_date=fetch_end_mmddyy
    )

    unseen_txns_from_api = []
    allowed_keys = set(
        ["id", "date", "fi", "account", "isPending", "merchant", "amount"]
    )
    for api_txn_unsanitized in api_txns_unsanitized:
        api_txn = {
            allowed_key: api_txn_unsanitized[allowed_key]
            for allowed_key in allowed_keys
        }

        Txn = Query()
        seen_txns = txnsdb.search(Txn.id == api_txn["id"])

        if len(seen_txns) > 0:
            continue

        unseen_txns_from_api.append(api_txn)
        txnsdb.insert(api_txn)

    print(f"Found {len(unseen_txns_from_api)} new transactions")

    return unseen_txns_from_api


def get_unseen_txns_blocks():
    unseen_txns = download_and_persist_and_get_unseen_txns()

    def get_txn_section_block(txn):
        return {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{txn['merchant']}* — {txn['amount']}{' (pending)' if txn['isPending'] else ''}",
            },
            "accessory": {
                "type": "overflow",
                "options": [
                    {
                        "text": {
                            "type": "plain_text",
                            "text": txn["fi"],
                        },
                        "value": "fi",
                    },
                    {
                        "text": {
                            "type": "plain_text",
                            "text": txn["account"],
                        },
                        "value": "account",
                    },
                    {
                        "text": {
                            "type": "plain_text",
                            "text": f"Date: {txn['date']}",
                        },
                        "value": "date",
                    },
                    {
                        "text": {
                            "type": "plain_text",
                            "text": f"ID: {txn['id']}",
                        },
                        "value": "id",
                    },
                ],
            },
        }

    return list(map(get_txn_section_block, unseen_txns))


def get_accounts():
    account_data = mint.get_accounts()

    account_data = filter(
        lambda account: account["isActive"]
        and (account["value"] < 0 or account["value"] >= 1),
        account_data,
    )

    accounts_by_class = defaultdict(list)
    for account in account_data:
        accounts_by_class[account["klass"]].append(account)

    return accounts_by_class


def get_accounts_blocks():
    accounts_by_class = get_accounts()

    def without_low_balance_accounts(accounts):
        return filter(
            lambda account: account["value"] < 0 or account["value"] >= 1,
            accounts,
        )

    def get_accounts_section_block(accounts):
        text = "\n".join(
            map(
                lambda account: f"{account['fiName']} {account['name']} — {account['currency']} {'{:.2f}'.format(account['value'])}",
                accounts,
            )
        )
        return {"type": "section", "text": {"type": "mrkdwn", "text": text}}

    credit_cards_header = {
        "type": "header",
        "text": {"type": "plain_text", "text": "Credit cards"},
    }
    credit_cards_block = get_accounts_section_block(
        without_low_balance_accounts(accounts_by_class["credit"])
    )

    bank_accounts_header = {
        "type": "header",
        "text": {"type": "plain_text", "text": "Bank accounts"},
    }
    bank_accounts_block = get_accounts_section_block(
        without_low_balance_accounts(accounts_by_class["bank"])
    )

    return [
        credit_cards_header,
        credit_cards_block,
        bank_accounts_header,
        bank_accounts_block,
    ]


def get_money_buffer_block():
    pass


if __name__ == "__main__":
    SocketModeHandler(app, slack_app_token).connect()

    while True:
        print("Checking for new transactions...")
        txns_blocks = get_unseen_txns_blocks()

        chunk_size = 50
        for i in range(0, len(txns_blocks), chunk_size):
            txns_blocks_in_chunk = txns_blocks[i: i + chunk_size]
            print("len of blocks:", len(txns_blocks_in_chunk))
            post_message(blocks=txns_blocks_in_chunk)

        mint.initiate_account_refresh()

        print("Sleeping...")
        time.sleep(1800)

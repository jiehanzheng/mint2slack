from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
import mintapi
from tinydb import TinyDB, Query
from tinydb.storages import MemoryStorage
import os
from collections import defaultdict
import datetime
from datetime import datetime, timedelta
import time

print('Opening secret files...')
with open("secrets/slack_bot_token.txt") as slack_bot_token_f, open("secrets/slack_app_token.txt") as slack_app_token_f, open("secrets/mint_email.txt") as mint_email_f, open("secrets/mint_password.txt") as mint_password_f, open("secrets/mint_mfa_token.txt") as mint_mfa_token_f:
    slack_bot_token = slack_bot_token_f.read()
    slack_app_token = slack_app_token_f.read()
    mint_email = mint_email_f.read()
    mint_password = mint_password_f.read()
    mint_mfa_token = mint_mfa_token_f.read()

accountsdb = TinyDB(storage=MemoryStorage)
Account = Query()

txnsdb = TinyDB("config/txns_v2.json")
Txn = Query()

print('Init Slack app...')
app = App(token=slack_bot_token)

session_path = os.path.join(os.path.dirname(__file__), "config/chrome_session")

print('Setting up mintapi...')
mint = mintapi.Mint(
    mint_email,
    mint_password,
    mfa_method="soft-token",
    mfa_token=mint_mfa_token,
    headless=True,
    session_path=session_path,
    wait_for_sync=False,
    use_chromedriver_on_path="USE_CHROMEDRIVER_ON_PATH" in os.environ,
    beta="MINTAPI_USE_BETA_HOST" in os.environ,
)


@app.command("/accts")
def handle_accts_command(ack, respond, command):
    print("Handling /accts command...")
    ack()
    accounts_blocks = get_accounts_blocks()
    text = get_text_summary_for_blocks(accounts_blocks)[:3000]
    respond(blocks=accounts_blocks, text=text, response_type="in_channel")


@app.command("/buf")
def handle_buf_command(ack, respond, command):
    print("Handling /buf command...")
    ack()
    buf_blocks = [{
        "type": "section", "text": get_money_buffer_element()
    }]
    text = get_text_summary_for_blocks(buf_blocks)[:3000]
    respond(blocks=buf_blocks, text=text, response_type="in_channel")


@app.event("message")
def handle_message_events(body, logger):
    pass


@app.error
def custom_error_handler(error, respond, body, logger):
    try:
        respond(
            text=f"Sorry, an error occurred.  I'm going to restart myself now and please try again later.", response_type="in_channel")
        respond(text=f"The error was: {str(error)}",
                response_type="in_channel")
    except:
        print("An error occurred while handling another error")
    finally:
        print(error)
        os._exit(1)


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

    api_txns_unsanitized = mint.get_transaction_data(
        start_date=fetch_start_mmddyy,
        end_date=fetch_end_mmddyy,
        remove_pending=False
    )

    unseen_txns = []
    for api_txn_unsanitized in api_txns_unsanitized:
        txn = {
            'id': api_txn_unsanitized['id'],
            'account_id': api_txn_unsanitized['accountId'],
            'date': api_txn_unsanitized['date'],
            'description_from_fi':  api_txn_unsanitized['fiData']['description'],
            'amount':  api_txn_unsanitized['fiData']['amount'],
            'is_pending': api_txn_unsanitized['isPending'],
        }

        if txnsdb.contains(Txn.id == txn["id"]):
            txnsdb.update(txn, Txn.id == txn['id'])
            continue

        unseen_txns.append(txn)
        txnsdb.insert(txn)

    print(f"Found {len(unseen_txns)} new transactions")

    return unseen_txns


def get_unseen_txns_blocks():
    unseen_txns = download_and_persist_and_get_unseen_txns()

    def get_txn_section_block(txn):
        account = accountsdb.get(Account.id == txn['account_id'])

        return {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{txn['description_from_fi']}* — {txn['amount']}{' (pending)' if txn['is_pending'] else ''}",
            },
            "accessory": {
                "type": "overflow",
                "options": [
                    {
                        "text": {
                            "type": "plain_text",
                            "text": account['fi_name'],
                        },
                        "value": "fi",
                    },
                    {
                        "text": {
                            "type": "plain_text",
                            "text": account['name'],
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


def download_accounts():
    print("Downloading accounts...")
    api_accounts_unsanitized = mint.get_account_data()

    for api_account_unsanitized in api_accounts_unsanitized:
        account = {
            'id': api_account_unsanitized['id'],
            'type': api_account_unsanitized['type'],
            'name': api_account_unsanitized['name'],
            'value': api_account_unsanitized['value'],
            'currency': api_account_unsanitized['currency'],
            'fi_name': api_account_unsanitized['fiName'],
            'is_active': api_account_unsanitized['isActive'],
            'created_at': api_account_unsanitized['createdDate'],
            'updated_at': api_account_unsanitized['lastUpdatedDate'],
        }
        accountsdb.upsert(account, Account.id == account['id'])


def get_active_accounts_by_type():
    download_accounts()
    active_accounts = accountsdb.search(Account.is_active == True)

    accounts_by_type = defaultdict(list)
    for account in active_accounts:
        accounts_by_type[account['type']].append(account)

    return accounts_by_type


def get_accounts_blocks():
    accounts_by_class = get_active_accounts_by_type()

    def without_low_balance_accounts(accounts):
        return filter(
            lambda account: account["value"] < 0 or account["value"] >= 1,
            accounts,
        )

    def get_accounts_section_block(accounts):
        text = "\n".join(
            map(
                lambda account: f"{account['fi_name']} {account['name']} — {account['currency']} {'{:.2f}'.format(account['value'])}",
                accounts,
            )
        )
        return {"type": "section", "text": {"type": "mrkdwn", "text": text}}

    credit_cards_header = {
        "type": "header",
        "text": {"type": "plain_text", "text": "Credit cards"},
    }
    credit_cards_block = get_accounts_section_block(
        without_low_balance_accounts(accounts_by_class['CreditAccount'])
    )

    bank_accounts_header = {
        "type": "header",
        "text": {"type": "plain_text", "text": "Bank accounts"},
    }
    bank_accounts_block = get_accounts_section_block(
        without_low_balance_accounts(accounts_by_class["BankAccount"])
    )

    return [
        credit_cards_header,
        credit_cards_block,
        bank_accounts_header,
        bank_accounts_block,
    ]


def get_money_buffer_element():
    accounts_by_class = get_active_accounts_by_type()

    cash_value = sum(
        map(lambda account: account['value'], accounts_by_class["BankAccount"]))
    credit_card_value = sum(
        map(lambda account: account['value'], accounts_by_class["CreditAccount"]))
    buffer_value = cash_value + credit_card_value

    return {"type": "mrkdwn",
            "text": f"Cash: {'{:.2f}'.format(cash_value)}, credit: {'{:.2f}'.format(credit_card_value)}, buffer: *{'{:.2f}'.format(buffer_value)}*"}


def get_text_summary_for_blocks(blocks):
    return "; ".join(filter(lambda x: x is not None, map(get_text_summary_for_block, blocks)))


def get_text_summary_for_block(block):
    if 'text' in block and isinstance(block['text'], str):
        return block['text']
    elif 'text' in block and isinstance(block['text'], dict):
        return get_text_summary_for_block(block['text'])
    elif 'elements' in block and isinstance(block['elements'], list):
        return get_text_summary_for_blocks(block['elements'])
    else:
        return None


if __name__ == "__main__":
    download_accounts()

    print('Init Slack SocketModeHandler...')
    SocketModeHandler(app, slack_app_token).connect()

    while True:
        print("Checking for new transactions...")
        txn_notif_blocks = get_unseen_txns_blocks()

        if len(txn_notif_blocks) > 0:
            txn_notif_blocks.append({
                "type": "context",
                "elements": [get_money_buffer_element()]
            })

        chunk_size = 50
        for i in range(0, len(txn_notif_blocks), chunk_size):
            blocks_in_chunk = txn_notif_blocks[i: i + chunk_size]
            print("len of blocks:", len(blocks_in_chunk))
            text = get_text_summary_for_blocks(blocks_in_chunk)[:3000]
            post_message(blocks=blocks_in_chunk, text=text)

        print("mint.initiate_account_refresh...")
        mint.initiate_account_refresh()

        print("Sleeping...")
        time.sleep(1800)

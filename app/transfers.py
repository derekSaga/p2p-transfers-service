import uuid
from datetime import datetime, timezone
from decimal import Decimal

from botocore.exceptions import ClientError

from app import cache, db
from app.models import TransferRequest


class TransferError(Exception):
    def __init__(self, status_code: int, code: str):
        super().__init__(code)
        self.status_code = status_code
        self.code = code


def format_cents(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def get_balance(user_id: str) -> int:
    cached = cache.get_balance(user_id)
    if cached is not None:
        return cached

    account = db.table(db.ACCOUNTS).get_item(Key={"user_id": user_id}).get("Item")
    if account is None:
        raise TransferError(404, "account_not_found")

    balance = int(account["balance"])
    cache.set_balance(user_id, balance)
    return balance


def _get_existing_transfer_for_idempotency_key(key: str) -> dict | None:
    record = db.table(db.IDEMPOTENCY).get_item(Key={"idempotency_key": key}).get("Item")
    if not record:
        return None

    transfer = db.table(db.TRANSFERS).get_item(Key={"transfer_id": record["transfer_id"]}).get("Item")
    if not transfer:
        return None

    return {
        "transfer_id": transfer["transfer_id"],
        "from_user_id": transfer["from_user_id"],
        "to_user_id": transfer["to_user_id"],
        "amount_cents": int(transfer["amount_cents"]),
        "created_at": transfer["created_at"],
    }


def send_money(sender_id: str, request: TransferRequest, idempotency_key: str) -> dict:
    key = f"{sender_id}#{idempotency_key}"

    if request.to_user_id == sender_id:
        raise TransferError(422, "cannot_send_to_yourself")

    amount_cents = int(Decimal(str(request.amount)) * 100)
    if amount_cents <= 0:
        raise TransferError(422, "invalid_amount")

    accounts = db.table(db.ACCOUNTS)
    sender = accounts.get_item(Key={"user_id": sender_id}).get("Item")
    receiver = accounts.get_item(Key={"user_id": request.to_user_id}).get("Item")

    if sender is None or receiver is None:
        raise TransferError(404, "account_not_found")

    existing = _get_existing_transfer_for_idempotency_key(key)
    if existing:
        return existing

    transfer_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    try:
        db.table(db.IDEMPOTENCY).put_item(
            Item={"idempotency_key": key, "transfer_id": transfer_id},
            ConditionExpression="attribute_not_exists(idempotency_key)",
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            existing = _get_existing_transfer_for_idempotency_key(key)
            if existing:
                return existing
            raise TransferError(409, "insufficient_funds")
        raise

    try:
        accounts.update_item(
            Key={"user_id": sender_id},
            UpdateExpression="SET balance = :new_balance",
            ConditionExpression="balance >= :amount",
            ExpressionAttributeValues={
                ":new_balance": int(sender["balance"]) - amount_cents,
                ":amount": amount_cents,
            },
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            db.table(db.IDEMPOTENCY).delete_item(Key={"idempotency_key": key})
            raise TransferError(409, "insufficient_funds")
        db.table(db.IDEMPOTENCY).delete_item(Key={"idempotency_key": key})
        raise

    try:
        accounts.update_item(
            Key={"user_id": request.to_user_id},
            UpdateExpression="SET balance = :new_balance",
            ExpressionAttributeValues={":new_balance": int(receiver["balance"]) + amount_cents},
        )
    except ClientError:
        db.table(db.IDEMPOTENCY).delete_item(Key={"idempotency_key": key})
        raise

    db.table(db.TRANSFERS).put_item(
        Item={
            "transfer_id": transfer_id,
            "from_user_id": sender_id,
            "to_user_id": request.to_user_id,
            "amount_cents": amount_cents,
            "created_at": now,
        }
    )

    transfer = {
        "transfer_id": transfer_id,
        "from_user_id": sender_id,
        "to_user_id": request.to_user_id,
        "amount_cents": amount_cents,
        "created_at": now,
    }

    cache.delete_balance(sender_id)
    cache.delete_balance(request.to_user_id)

    return transfer
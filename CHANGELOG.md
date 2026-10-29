# Changelog

## Unreleased

### Fixed

- Corrected duplicate-transfer behavior for concurrent retries using the same idempotency key.
  - Error: multiple requests with the same `Idempotency-Key` created different transfer IDs and duplicated transfers.
  - Fix: enforced idempotency at the DynamoDB level using a conditional write on `idempotency_key` and returned the original transfer when the same key was retried.

- Fixed race conditions in balance validation during concurrent transfers.
  - Error: two or more simultaneous transfers could pass the balance check and overdraw the sender account.
  - Fix: added a conditional balance check (`balance >= :amount`) on the sender account before updating it, so only one transfer can succeed when funds are insufficient.

- Fixed stale balance reads after a transfer was completed.
  - Error: the balance endpoint sometimes returned the previous cached value even immediately after a successful transfer.
  - Fix: invalidated the sender and receiver balance cache entries after each transfer so the next read reflects the latest balance.

- Fixed monetary precision errors when transferring fractional amounts.
  - Error: values such as `19.99` were being rounded incorrectly and could appear as `19.98`.
  - Fix: converted amounts to cents using `Decimal` before storing and formatting, preserving exact cent precision.

### Added

- Added a consistent transfer result shape for successful operations, returning the same payload on repeated idempotent requests.

### Notes

- Existing service behavior was preserved for valid requests while fixing the concurrency, cache invalidation, and precision defects described above.

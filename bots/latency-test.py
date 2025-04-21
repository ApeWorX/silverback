"""
This bot allows measuring the latency of a signing setup, by producing a stream of transactions
where the block number is a self-transfer amount, which can be analyzed by comparing the length
of time (in blocks) between the block number the transaction was mined in (`.block.number`), and
the transfer amount. FOR TESTING USE ONLY on a public testnet.

Overall task/txn lifecycle (not to scale):

|------------------------------------------ total time ------------------------------------------|
|<-- trigger block mined |------------- [other blocks] --------------| settlement block mined -->|
|-- RPC --||----------- bot's response time ---------||------ RPC -----||----- tx in mempool ----|

                                       |--- sign ---||-- tx broadcast --|
        |-- runner send --|          |-- worker exec --|      |-- runner recv --|
                         |-- broker --|               |-- RB --|
                        |--------------- task time -------------||-- recording time --|

You can measure this latency via the following scripts in Ape console:

    >>> from datetime import datetime, timezone
    >>> txs = account.history  # or `account.history[start_nonce:stop_nonce]`
    # Block that triggered task to execute
    >>> task_recv_block = [chain.blocks[tx.value // 10**10] for tx in txs]
    # Block that settled signer transaction
    >>> txn_conf_block = [tx.block for tx in txs]
    # Time when transaction was created by worker
    >>> txn_creation_time = [
    ...     datetime.fromtimestamp(tx.value % 10**10, tz=timezone.utc)
    ...     for tx in txs
    ... ]
    # Time between block that triggered transaction vs. broadcasting next one
    >>> task_start_latency = [
    ...     (txn_submit - recv_blk.datetime)
    ...     for recv_blk, txn_submit
    ...     in zip(task_recv_block, txn_creation_time)
    ... ]
    # Time between broadcasting txn and block being mined with txn in it
    >>> broadcasted_task_metric = pd.DataFrame.fromdict()["broadcast"]
    >>> txn_broadcast_latency = [
    ...     (conf_blk.datetime - broadcasted)
    ...     for conf_blk, broadcasted
    ...     in zip(txn_conf_block, broadcasted_task_metric)
    ... ]

"""

import os
from datetime import datetime, timezone

from ape import chain

from silverback import SilverbackBot

# NOTE: By default, assume this is a normal EOA (only 21k base gas required)
GAS_LIMIT = int(os.environ.get("GAS_LIMIT", 21_000))
MAX_EIP1559_BLOCK_DEPTH = int(os.environ.get("MAX_EIP1559_BLOCK_DEPTH", 3))
PRIORITY_FEE = os.environ.get("PRIORITY_FEE", "0 gwei")

bot = SilverbackBot()

# NOTE: Requires a signer to run
# NOTE: Don't run this on a mainnet
assert bot.provider.network.name != "mainnet"


def utc_now() -> float:
    return datetime.now(timezone.utc).timestamp()


@bot.on_(chain.blocks)
async def broadcast(block) -> float:
    # NOTE: Only use attributes of `block` that don't require RPC calls

    # Sign and broadcast a transaction
    bot.signer.transfer(
        bot.signer,
        # NOTE: Divide by 10**10 to obtain `block.number` from `txn.value`
        #       (This allows easy display on most explorers)
        # NOTE: Use `txn.value % 10**10` to obtain `start_time`
        # NOTE: `int(utc_now())` strips sub-second timing from timestamp
        f"0.{block.number:08}{int(utc_now())} ether",
        nonce=bot.nonce,
        # NOTE: all below txn kwargs skip unnecessary RPC calls to form txn
        required_confirmations=0,  # NOTE: No need to wait for confirmation
        gas_limit=GAS_LIMIT,  # NOTE: No need to estimate gas
        # NOTE: assume EIP-1559 transactions are available
        # NOTE: assume we are N blocks behind, w/ 12.5% max increase per block
        base_fee=int(1.125**MAX_EIP1559_BLOCK_DEPTH * block.base_fee),
        priority_fee=PRIORITY_FEE,
    )

    # This is the time at which the transaction has been successfully "broadcast"
    return utc_now()

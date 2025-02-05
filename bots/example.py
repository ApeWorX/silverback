import asyncio
from typing import Annotated

from ape import chain
from ape.api import BlockAPI
from ape.types import ContractLog
from ape_tokens import tokens  # type: ignore[import]
from taskiq import Context, TaskiqDepends, TaskiqState

from silverback import CircuitBreaker, SilverbackBot, StateSnapshot

# Do this first to initialize your bot
bot = SilverbackBot()

# Cannot call `bot.state` outside of an bot function handler
# bot.state.something  # NOTE: raises AttributeError

# NOTE: Don't do any networking until after initializing bot
USDC = tokens["USDC"]
YFI = tokens["YFI"]


@bot.on_startup()
def bot_startup(startup_state: StateSnapshot):
    # This is called just as the bot is put into "run" state,
    # and handled by the first available worker

    # Any exception raised on startup aborts immediately:
    # raise Exception  # NOTE: raises StartupFailure

    # This is a great place to set `bot.state` values
    bot.state.logs_processed = 0
    # NOTE: Can put anything here, any python object works

    return {"block_number": startup_state.last_block_seen}


# Can handle some resource initialization for each worker, like LLMs or database connections
class MyDB:
    def execute(self, query: str):
        pass  # Handle query somehow...


@bot.on_worker_startup()
# NOTE: This event is triggered internally, do not use unless you know what you're doing
def worker_startup(worker_state: TaskiqState):  # NOTE: You need the type hint to load worker state
    # NOTE: Worker state is per-worker, not shared with other workers
    # NOTE: Can put anything here, any python object works
    worker_state.db = MyDB()

    # Any exception raised on worker startup aborts immediately:
    # raise Exception  # NOTE: raises StartupFailure

    # Cannot call `bot.state` because it is not set up yet on worker startup functions
    # bot.state.something  # NOTE: raises AttributeError


# This is how we trigger off of new blocks
@bot.on_(chain.blocks)
# NOTE: The type hint for block is `BlockAPI`, but we parse it using `EcosystemAPI`
# NOTE: If you need something from worker state, you have to use taskiq context
def exec_block(block: BlockAPI, context: Annotated[Context, TaskiqDepends()]):
    context.state.db.execute(f"some query {block.number}")
    return len(block.transactions)


# This is how we trigger off of events
# Set new_block_timeout to adjust the expected block time.
@bot.on_(USDC.Transfer, start_block=19784367, new_block_timeout=25)
# NOTE: Typing isn't required, it will still be an Ape `ContractLog` type
def exec_event1(log):
    if log.log_index % 7 == 3:
        # If you raise any exception, Silverback will track the failure and keep running
        # NOTE: By default, if you have 3 tasks fail in a row, the bot will shutdown itself
        raise ValueError("I don't like the number 3.")

    # You can update state whenever you want
    bot.state.logs_processed += 1

    return {"amount": log.amount}


@bot.on_(YFI.Approval)
# Any handler function can be async too
async def exec_event2(log: ContractLog):
    # All `bot.state` values are updated across all workers at the same time
    bot.state.logs_processed += 1
    # Do any other long running tasks...
    await asyncio.sleep(5)
    return log.amount


@bot.on_(chain.blocks)
# NOTE: You can have multiple handlers for any trigger we support
def check_logs(log):
    if bot.state.logs_processed > 20:
        # If you ever want the bot to immediately shutdown under some scenario, raise this exception
        raise CircuitBreaker("Oopsie!")


# A final job to execute on Silverback shutdown
@bot.on_shutdown()
def bot_shutdown():
    # NOTE: Any exception raised on worker shutdown is ignored:
    # raise Exception
    return {"some_metric": 123}


# Just in case you need to release some resources or something inside each worker
@bot.on_worker_shutdown()
def worker_shutdown(state: TaskiqState):  # NOTE: You need the type hint here
    # This is a good time to release resources
    state.db = None

    # NOTE: Any exception raised on worker shutdown is ignored:
    # raise Exception

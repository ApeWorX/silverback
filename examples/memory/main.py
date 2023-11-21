from ape import chain
from ape.api import BlockAPI
from ape.types import ContractLog
from ape_tokens import tokens  # type: ignore[import]
from typing import Annotated

from silverback import CircuitBreaker, SilverbackApp, SilverbackStartupState
from taskiq import Context, TaskiqDepends, TaskiqState

# Do this to initialize your app
app = SilverbackApp()

# NOTE: Don't do any networking until after initializing app
USDC = tokens["USDC"]
YFI = tokens["YFI"]


@app.on_startup()
def app_startup(startup_state: SilverbackStartupState):
    return {"message": "Starting...", "block_number": startup_state.last_block_seen}


@app.on_client_startup()
def client_startup(state):
    return {"message": "Client started."}


# Can handle some initialization on startup, like models or network connections
@app.on_worker_startup()
def worker_startup(state):
    state.block_count = 0
    # state.db = MyDB()
    return {"message": "Worker started."}


# This is how we trigger off of new blocks
@app.on_(chain.blocks)
# context must be a type annotated kwarg to be provided to the task
def exec_block(block: BlockAPI, context: Annotated[Context, TaskiqDepends()]):
    context.state.block_count += 1
    return len(block.transactions)


# This is how we trigger off of events
# Set new_block_timeout to adjust the expected block time.
@app.on_(USDC.Transfer, start_block=18588777, new_block_timeout=25)
# NOTE: Typing isn't required
def exec_event1(log):
    if log.log_index % 7 == 3:
        # If you ever want the app to shutdown under some scenario, call this exception
        raise CircuitBreaker("Oopsie!")
    return {"amount": log.amount}


@app.on_(YFI.Approval)
# Any handler function can be async too
async def exec_event2(log: ContractLog):
    return log.amount


# Just in case you need to release some resources or something
@app.on_worker_shutdown()
def worker_shutdown(state):
    return {
        "message": f"Worker stopped after handling {state.block_count} blocks.",
        "block_count": state.block_count,
    }


# A final job to execute on Silverback shutdown
@app.on_shutdown()
def app_shutdown(state):
    return {"message": "Stopping..."}

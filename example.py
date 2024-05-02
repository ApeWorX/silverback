from typing import Annotated

from ape import chain
from ape.api import BlockAPI
from ape.types import ContractLog
from ape_tokens import tokens  # type: ignore[import]
from taskiq import Context, TaskiqDepends, TaskiqState

from silverback import AppState, CircuitBreaker, SilverbackApp

# Do this first to initialize your app
app = SilverbackApp()

# NOTE: Don't do any networking until after initializing app
USDC = tokens["USDC"]
YFI = tokens["YFI"]


@app.on_startup()
def app_startup(startup_state: AppState):
    # NOTE: This is called just as the app is put into "run" state,
    #       and handled by the first available worker
    # raise Exception  # NOTE: Any exception raised on startup aborts immediately
    return {"block_number": startup_state.last_block_seen}


# Can handle some resource initialization for each worker, like LLMs or database connections
class MyDB:
    def execute(self, query: str):
        pass


@app.on_worker_startup()
def worker_startup(state: TaskiqState):  # NOTE: You need the type hint here
    # NOTE: Can put anything here, any python object works
    state.db = MyDB()
    state.block_count = 0
    # raise Exception  # NOTE: Any exception raised on worker startup aborts immediately


# This is how we trigger off of new blocks
@app.on_(chain.blocks)
# NOTE: The type hint for block is `BlockAPI`, but we parse it using `EcosystemAPI`
# NOTE: If you need something from worker state, you have to use taskiq context
def exec_block(block: BlockAPI, context: Annotated[Context, TaskiqDepends()]):
    context.state.db.execute(f"some query {block.number}")
    return len(block.transactions)


# This is how we trigger off of events
# Set new_block_timeout to adjust the expected block time.
@app.on_(USDC.Transfer, start_block=19784367, new_block_timeout=25)
# NOTE: Typing isn't required, it will still be an Ape `ContractLog` type
def exec_event1(log):
    if log.log_index % 7 == 3:
        # If you raise any exception, Silverback will track the failure and keep running
        # NOTE: By default, if you have 3 tasks fail in a row, the app will shutdown itself
        raise ValueError("I don't like the number 3.")

    return {"amount": log.amount}


@app.on_(YFI.Approval)
# Any handler function can be async too
async def exec_event2(log: ContractLog):
    if log.log_index % 7 == 6:
        # If you ever want the app to immediately shutdown under some scenario, raise this exception
        raise CircuitBreaker("Oopsie!")

    return log.amount


# A final job to execute on Silverback shutdown
@app.on_shutdown()
def app_shutdown():
    # raise Exception  # NOTE: Any exception raised on shutdown is ignored
    return {"some_metric": 123}


# Just in case you need to release some resources or something inside each worker
@app.on_worker_shutdown()
def worker_shutdown(state: TaskiqState):  # NOTE: You need the type hint here
    state.db = None
    # raise Exception  # NOTE: Any exception raised on worker shutdown is ignored
